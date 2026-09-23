"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import math
import os
import time
from urllib.parse import urlparse

import httpx

from .questions import ENGAGE, NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)


def jev_endpoint():
    """TypeSafe directly, or the same Jev decision API routed through OpenRouter."""
    if os.environ.get("TYPESAFE_API_KEY"):
        return (
            "https://api.typesafe.ai/v1/systemone",
            os.environ["TYPESAFE_API_KEY"],
            os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        )
    if os.environ.get("OPENROUTER_API_KEY"):
        model = os.environ.get("TYPESAFE_MODEL") or "jev-latest"
        model = model if "/" in model else "~typesafe/" + model
        return "https://openrouter.ai/api/alpha/decisions", os.environ["OPENROUTER_API_KEY"], model
    raise ValueError("Jev needs TYPESAFE_API_KEY or OPENROUTER_API_KEY; no action executed.")


def api_key(prefix, base):
    # A shared OpenRouter key is only ever sent to OpenRouter.
    key = os.environ.get(prefix + "_API_KEY")
    if not key and urlparse(base).hostname == "openrouter.ai":
        key = os.environ.get("OPENROUTER_API_KEY")
    return key


def post_json(url, key, body):
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            detail = response.text[:300].replace(key, "…") if key else response.text[:300]
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed. {detail}")
        return response.json()
    raise RuntimeError("Model unavailable")


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return answer


# Dropdown options offered per element and per page. Options past these are not offered, so they cannot be
# selected. A native select's accessible name can repeat every option; 120 options overflowed Jev's input.
MAX_OPTIONS = 20
MAX_PAGE_OPTIONS = 60
MAX_LABEL = 200  # Characters of an element label sent to Jev.


def option_text(action):
    return action["label"].split(" → ")[-1][:80]


def criterion_label(action):
    if action["kind"] == "select":
        return f"{action['label'].split(' → ')[0][:60]} → {option_text(action)}"
    return action["label"][:MAX_LABEL]


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT", "submit": "PRESS_ENTER"}
    for action in actions:
        kind = action["kind"]
        if action.get("covered"):
            # The executor would refuse it; offering it only invites a wasted attempt.
            continue
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0][:MAX_LABEL], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        if kind == "select":
            if len(elements[int(index) - 1]["options"]) >= MAX_OPTIONS:
                continue
            if len(targets.get("SELECT", {})) >= MAX_PAGE_OPTIONS:
                continue
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": option_text(action)})
        group[target] = action
    return elements, targets, controls


def operation_space(actions):
    """Elements, per-operation targets, and every operation either system may choose on this page."""
    elements, targets, controls = action_space(actions)
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
        "PRESS_ENTER": "Press the Enter key in a text field; this runs the search or submits what the field holds.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    return elements, targets, controls, operations


def resolve(operation, target, targets, controls):
    """Map an operation and index to one observed action id. Anything else is rejected upstream."""
    if operation in targets:
        return targets[operation][target]["id"]
    return controls[operation]["id"] if operation in controls else operation


def recent_actions(history, keys):
    # A System 2 note travels with its action so System 1 can see why it happened.
    return [{k: h.get(k) for k in keys if k in h or k != "note"} for h in history]


def validate_noul(answer):
    value = answer.get("noul") if isinstance(answer, dict) else None
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return value


class NoTextValue(ValueError):
    """The text helper had no value for the field. Nothing was typed."""


def choose(state, goal, history, engage=False, failed=None):
    elements, targets, controls, operations = operation_space(state["actions"])
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {criterion_label(a)}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded") if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    url, key, jev = jev_endpoint()
    if engage:
        # Jev itself decides, in the same request, whether this step deserves System 2.
        questions["system2"] = {
            "type": "noul",
            "criteria": {
                "true": "A slower reasoning model should decide this step.",
                "false": "The next step is routine; act now.",
            },
            "instructions": {"goal": goal, "rules": [NEXT_ACTION, ENGAGE]},
        }
    body = {
        "model": jev,
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": recent_actions(history[-10:], ("action", "kind", "text", "page_changed", "note")),
            **({"last_failed_attempt": failed} if failed else {}),
        },
        "questions": questions,
    }
    started = time.perf_counter()
    result = post_json(url, key, body)
    operation_answer = validate_choice(result["answers"].get("operation", {}), operations)
    operation = operation_answer["choice"]
    target = None
    target_answer = None
    probabilities = {}
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        target_answer = validate_choice(result["answers"].get(operation.lower() + "_target", {}), targets[operation])
        target = target_answer["choice"]
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
    choice = resolve(operation, target, targets, controls)
    engage_probability = validate_noul(result["answers"].get("system2")) if engage else None
    if not probabilities:
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "system": 1,
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "system2": engage_probability,
        "raw_answers": result["answers"],
        "model": result["model"],
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
    }


def field_context(goal, action, page, history, advice=None):
    context = {
        "goal": goal,
        "field": {k: action.get(k) for k in ("label", "role", "value")},
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }
    if advice:
        context["advice"] = advice
    return context


def chat_json(model, key, base, reasoning, system, context, max_tokens):
    """One OpenAI-compatible JSON-mode request. Returns the parsed object and call metadata."""
    started = time.perf_counter()
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            **reasoning,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(context)}],
        },
    )
    try:
        output = json.loads(result["choices"][0]["message"]["content"])
    except (ValueError, KeyError, TypeError, IndexError):
        output = None
    return output, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
    }


def field_text(context):
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    key = api_key("TEXT_MODEL", base)
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY; no text is hardcoded or guessed by the executor.")
    model = os.environ.get("TEXT_MODEL", "deepseek-chat")
    reasoning = {"thinking": {"type": "disabled"}} if "api.deepseek.com/" in base else {"reasoning": {"effort": "low"}}
    if os.environ.get("TEXT_MODEL_REASONING") == "none":
        reasoning = {"reasoning": {"enabled": False}}
    output, helper = chat_json(model, key, base, reasoning, TEXT_VALUE, context, 1024)
    try:
        value = output["text"]
        if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise NoTextValue("Text helper returned no valid field value; nothing typed.") from None
    return value, helper
