"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import os
import time
from pathlib import Path

from . import reasoner
from .browser import Browser, StalePage
from .model import NoTextValue, action_space, choose, field_context, field_text
from .questions import MAX_CALLS, MAX_REPEATS, MAX_REVIEWS, MAX_STEPS


class Agent:
    def __init__(self, url, goals, *, record_dir=None, screenshots=False, reasoning=False):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        plan = [task]
        self.pending_text = None
        self.pending_review = None
        self.failed = None  # The last attempt that did not execute, shown to Jev until an action succeeds.
        # System 2 is opt-in: without it, every decision is a single System 1 request.
        self.reasoning = reasoning
        if reasoning:
            reasoner.settings()
        self.browser = Browser(url)
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        try:
            page = self.browser.observe(screenshot=self.screenshots)
        except Exception:
            self.browser.close()
            raise
        self.state = dict(
            browser=self.browser,
            goal="\n".join(plan),
            page=page,
            decision=None,
            history=[],
            status="ready",
            plan=plan,
            plan_index=0,
            decisions=[],
            text_calls=[],
            reviews=[],
            elapsed_ms=0,
            started_at=None,
            record=bool(self.record_dir),
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def spend(self, kind):
        """Refuse a model call past the run's call budget, before anything is sent."""
        state = self.state
        budget = int(os.environ.get("JEV_MAX_CALLS", MAX_CALLS))
        if len(state["decisions"]) + len(state["text_calls"]) + len(state["reviews"]) >= budget:
            state["status"] = "blocked"
            raise ValueError(f"Stopped: {budget}-call model budget reached before a {kind} call")

    def review(self, page, context):
        state = self.state
        self.spend("System 2")
        started = time.perf_counter()
        try:
            # System 2 replaces the proposal with a choice from the same observed operation/target space.
            review = reasoner.review(page, state["goal"], state["history"], state["decision"])
            state["decision"] = review
            self.pending_review = (context, review)
        except reasoner.ReviewUnavailable as error:
            # System 2 is advisory. When it is late or invalid, System 1's validated decision stands.
            review = {"system": 2, "failed": str(error), "agrees": True,
                      "latency_ms": round((time.perf_counter() - started) * 1000)}
        state["reviews"].append(
            {
                **review,
                "system2_probability": state["decisions"][-1]["system2"],
                "fingerprint": page["fingerprint"],
                "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
            }
        )
        if review.get("failed") and state["decision"]["operation"] in {"DONE", "BLOCKED"}:
            # Jev was unsure about stopping and got no second opinion; a stop needs one. Ask again instead.
            # The identical-request limit and the call budget bound these retries.
            state["decision"] = None
            raise StalePage("System 2 did not answer about stopping; choose again")

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "tick":
            try:
                self.command("predict", {})
                return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                state["decision"] = None
                state["status"] = "ready"
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Start a demo first")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh demo.")
            page = state["page"]
            engage = self.reasoning and len(state["reviews"]) < MAX_REVIEWS
            # The same Jev request (same page, history, and questions) is never sent a fourth time.
            request = (page["fingerprint"], len(state["history"]), engage)
            if sum(d.get("input") == request for d in state["decisions"]) >= MAX_REPEATS:
                state["status"] = "blocked"
                raise ValueError(f"Stopped: identical Jev request already sent {MAX_REPEATS} times")
            self.spend("Jev")
            state["decision"] = choose(page, state["goal"], state["history"], engage=engage, failed=self.failed)
            state["decisions"].append(
                {
                    **state["decision"],
                    "fingerprint": page["fingerprint"],
                    "input": request,
                    "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
            )
            if engage and state["decision"]["system2"] >= float(os.environ.get("REVIEW_THRESHOLD", "0.5")):
                context = (page["fingerprint"], reasoner.review_context(page, state["goal"], state["history"]))
                if self.pending_review and self.pending_review[0] == context:
                    # A stale retry with identical System 2 input reuses the answer instead of thinking again.
                    state["decision"] = self.pending_review[1]
                else:
                    self.review(page, context)
            state["status"] = "predicted"
        elif name == "act":
            decision, page = state["decision"], state["page"]
            if not decision or body.get("fingerprint") != page["fingerprint"]:
                raise ValueError("Observe and choose before acting")
            # Consume once, before any mutation or model call. A retry cannot double-click.
            state["decision"] = None
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED"}:
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                state["status"] = "done" if selected == "DONE" else "blocked"
                state["plan_index"] = int(selected == "DONE")
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
            action = next(a for a in page["actions"] if a["id"] == selected)
            attempt = (action["kind"], action["label"], len(state["history"]))
            if state["decisions"]:
                state["decisions"][-1]["attempt"] = attempt
            if sum(d.get("attempt") == attempt for d in state["decisions"]) > MAX_REPEATS:
                # Stale retries on a changing page never reach execution; stop the fourth identical attempt.
                state["status"] = "blocked"
                raise ValueError(f"Stopped: {action['label']!r} chosen {MAX_REPEATS} times without executing")
            if len(state["history"]) >= MAX_STEPS:
                state["status"] = "blocked"
                raise ValueError(f"Stopped at the {MAX_STEPS}-action demo budget")
            text, helper = None, None
            if action["kind"] == "fill":
                if not state["browser"].fresh(page, action):
                    raise StalePage("Page changed before text generation. Choose again.")
                context = field_context(state["goal"], action, page, state["history"], decision.get("note"))
                if self.pending_text and self.pending_text[0] == context:
                    _, text, helper = self.pending_text
                else:
                    self.spend("text")
                    try:
                        text, helper = field_text(context)
                    except NoTextValue as error:
                        self.failed = {"action": action["label"], "kind": action["kind"], "reason": str(error)}
                        raise StalePage(str(error)) from None
                    self.pending_text = (context, text, helper)
                    state["text_calls"].append({**helper, "field": action["label"], "value": text})
            call = (action["kind"], action["label"], text)
            if sum((h["kind"], h["action"], h["text"]) == call for h in state["history"]) >= MAX_REPEATS:
                # Safety boundary: whoever chose it, the same action with the same argument never runs a 4th time.
                state["status"] = "blocked"
                raise ValueError(f"Stopped: {action['label']!r} already ran {MAX_REPEATS} times with this argument")
            # Browser.act checks freshness immediately before input, including after text generation.
            try:
                state["browser"].act(action, page, text=text)
            except StalePage as error:
                self.failed = {"action": action["label"], "kind": action["kind"], "reason": str(error)}
                raise
            self.pending_text = self.pending_review = self.failed = None
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            # Record execution before observing. A stale post-action observation must not erase the action.
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "system": decision.get("system", 1),
                    "action": action["label"],
                    "kind": action["kind"],
                    "choice": selected,
                    "probability": decision["probabilities"][selected],
                    "confidence": decision["confidence"],
                    "latency_ms": decision["latency_ms"],
                    "text": text,
                    "text_helper": helper["model"] if helper else None,
                    "text_latency_ms": helper["latency_ms"] if helper else 0,
                    "operation": decision["operation"],
                    "target": decision["target"],
                    "page_changed": None,
                    "url": page["url"],
                    "usage": decision["usage"],
                    **({"note": decision["note"]} if decision.get("note") else {}),
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                }
            )
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            state["history"][-1].update(
                page_changed=state["page"]["fingerprint"] != page["fingerprint"],
                # What this action revealed. Only System 2 reads it, so evidence it asked for is not forgotten.
                observed=state["page"]["text"][:1500],
                url=state["page"]["url"],
                elapsed_ms=state["elapsed_ms"],
            )
            if state["record"]:
                (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                    base64.b64decode(state["page"]["screenshot"])
                )
            state["status"] = "ready"
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            yield self.command("tick")

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
