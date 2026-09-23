"""System 2: a reasoning LLM decides a step only when Jev asks for it."""

import os
import threading

from .model import api_key, chat_json, operation_space, resolve
from .questions import REVIEW


class ReviewUnavailable(RuntimeError):
    """System 2 was late, failed, or answered outside the action space. System 1's decision stands."""


def settings():
    """Fail at startup, not mid-run, when System 2 is enabled but not configured."""
    base = os.environ.get("REVIEW_MODEL_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    key = api_key("REVIEW_MODEL", base)
    if not key:
        raise ValueError("System 2 needs REVIEW_MODEL_API_KEY; disable reasoning or configure a review model.")
    effort = os.environ.get("REVIEW_MODEL_REASONING", "medium")
    reasoning = {"reasoning": {"enabled": False}} if effort == "none" else {"reasoning": {"effort": effort}}
    return os.environ.get("REVIEW_MODEL", "deepseek/deepseek-v4.1-flash"), key, base, reasoning


def deadline_call(function, *args):
    # A daemon thread bounds wall-clock time even while a provider streams keep-alives; a late answer is dropped.
    result = {}

    def target():
        try:
            result["value"] = function(*args)
        except Exception as error:
            result["error"] = error

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(float(os.environ.get("REVIEW_TIMEOUT", "10")))
    if worker.is_alive():
        raise ReviewUnavailable("System 2 exceeded its deadline")
    if "error" in result:
        raise ReviewUnavailable(f"System 2 request failed: {result['error']}")
    return result["value"]


def review_context(page, goal, history):
    elements, _, _, operations = operation_space(page["actions"])
    return {
        "goal": goal,
        "page": {"url": page["url"], "title": page["title"], "text": page["text"][:12000]},
        "elements": elements,
        "operations": operations,
        "history": [
            {k: h.get(k) for k in ("step", "system", "action", "kind", "text", "page_changed", "note")} for h in history
        ],
        "recently_observed": [
            {"after_step": h["step"], "url": h.get("url"), "text": h.get("observed")} for h in history[-4:]
        ],
    }


def proposal_summary(proposal):
    top = sorted(proposal["target_probabilities"].items(), key=lambda item: -item[1])[:5]
    return {
        "operation": proposal["operation"],
        "target": proposal["target"],
        "operation_probabilities": proposal["operation_probabilities"],
        "top_targets": dict(top),
        "system2_probability": proposal["system2"],
    }


def review(page, goal, history, proposal):
    """Return a decision in System 1's shape, chosen from the same observed operation/target space."""
    model, key, base, reasoning = settings()
    _, targets, controls, operations = operation_space(page["actions"])
    context = {**review_context(page, goal, history), "system1": proposal_summary(proposal)}
    output, call = deadline_call(chat_json, model, key, base, reasoning, REVIEW, context, 8192)
    try:
        operation, target = output["operation"], output.get("target")
        note, analysis = output.get("note") or "", output.get("analysis") or ""
        if operation not in operations or not isinstance(note, str) or not isinstance(analysis, str):
            raise ValueError()
        if operation in targets:
            target = str(target)
            if target not in targets[operation]:
                raise ValueError()
        else:
            target = None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ReviewUnavailable("Invalid System 2 response; System 1's decision stands.") from None
    choice = resolve(operation, target, targets, controls)
    return {
        "system": 2,
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": None,
        "probabilities": {choice: None},
        "operation_probabilities": {},
        "target_probabilities": {},
        "target_confidence": None,
        "analysis": analysis[:1000],
        "note": note[:300],
        "agrees": choice == proposal["choice"],
        "proposal": {k: proposal[k] for k in ("choice", "operation", "target", "confidence")},
        "model": call["model"],
        "usage": call["usage"],
        "latency_ms": call["latency_ms"],
        "request": context,
    }
