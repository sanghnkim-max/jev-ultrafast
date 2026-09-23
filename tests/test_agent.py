"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.browser import StalePage, browser_operation, fingerprint


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target"}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2", "999"], "999"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["1"]["checked"] == "true"
        assert target["criteria"]["1"]["selected"] is False
        assert questions["operation"]["instructions"]["rules"] in target["instructions"]["rules"]
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3"


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
        "reviews": [],
    }
    a.reasoning = False
    a.pending_review = None
    a.failed = None
    return a


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_text_helper_input_is_recorded_with_its_output(runner, monkeypatch):
    monkeypatch.setattr(loop, "field_text", Mock(return_value=("book", {"model": "test", "latency_ms": 10})))
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    call = runner.state["text_calls"][0]
    assert call["input"]["goal"] == "Find a book" and call["input"]["field"]["label"] == "Search"
    assert call["value"] == "book" and runner.state["history"][-1]["text_call"] == 0


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_same_action_and_argument_never_executes_a_fourth_time(runner):
    for _ in range(3):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "ready" and runner.state["browser"].act.call_count == 3
    runner.state["decision"] = decision("wait")
    with pytest.raises(ValueError, match="already ran 3 times"):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "blocked" and runner.state["browser"].act.call_count == 3


def test_repeat_boundary_counts_the_argument(runner, monkeypatch):
    values = iter(["Zurich", "Zurich", "Zurich", "London", "Zurich"])
    monkeypatch.setattr(loop, "field_text", lambda _context: (next(values), {"model": "test", "latency_ms": 1}))
    for _ in range(4):
        runner.state["decision"] = decision("e1")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision("e1")
    with pytest.raises(ValueError, match="already ran 3 times"):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert [h["text"] for h in runner.state["history"]] == ["Zurich", "Zurich", "Zurich", "London"]


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import jev_ultrafast.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import jev_ultrafast.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "value": "Design",
        }})
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from examples.flights import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": "Track prices from Zürich to London departing 2026-09-20",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Sun, Sep 20"),
                ("Nonstop flight on Sunday, September 20. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2026", "2027")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", ["Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()


def s1(operation, target=None, choice_id=None, p=1.0, system2=None):
    return {
        **decision(choice_id or operation),
        "system": 1,
        "operation": operation,
        "target": target,
        "probabilities": {choice_id or operation: p},
        "operation_probabilities": {operation: p},
        "target_probabilities": {target: p} if target else {},
        "system2": system2,
    }


def reviewer(monkeypatch, output):
    from jev_ultrafast import reasoner

    monkeypatch.setenv("REVIEW_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": json.dumps(output)}}]})
    monkeypatch.setattr(model, "post_json", post)
    return reasoner, post


def jev(system2, operation="DONE"):
    def post(_url, _key, body):
        answers = {"operation": choice(body["questions"]["operation"]["criteria"], operation)}
        if "system2" in body["questions"]:
            answers["system2"] = {"type": "noul", "noul": system2}
        return {"model": "test", "answers": answers}

    return post


def test_jev_decides_system2_in_the_same_request_only_when_enabled(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    calls = []
    post = jev(0.8)
    monkeypatch.setattr(model, "post_json", lambda *a: calls.append(a[2]) or post(*a))
    assert model.choose(page(), "Find a book", [], engage=True)["system2"] == 0.8
    assert model.choose(page(), "Find a book", [])["system2"] is None
    assert "system2" in calls[0]["questions"] and "system2" not in calls[1]["questions"]
    assert calls[0]["questions"]["system2"]["type"] == "noul"


@pytest.mark.parametrize("answer", [None, {"noul": 1.5}, {"noul": float("nan")}, {"noul": "yes"}])
def test_invalid_system2_probability_is_rejected(monkeypatch, answer):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")

    def post(_url, _key, body):
        return {"model": "test", "answers": {"operation": choice(body["questions"]["operation"]["criteria"], "DONE"),
                                             "system2": answer}}

    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [], engage=True)


def test_system2_maps_to_observed_targets(monkeypatch):
    reasoner, post = reviewer(monkeypatch, {"analysis": "Not searched yet.", "operation": "CLICK", "target": 2,
                                            "note": "Submit the search."})
    d = reasoner.review(page(), "Find a book", [], s1("DONE", system2=0.9))
    assert d["system"] == 2 and d["choice"] == "e3" and d["target"] == "2" and not d["agrees"]
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["system1"]["system2_probability"] == 0.9 and "WAIT" in sent["operations"]


@pytest.mark.parametrize(
    "output",
    [
        {"operation": "CLICK", "target": "999"},
        {"operation": "CLICK", "target": "#search > button"},
        {"operation": "TYPE_TEXT", "target": "2"},  # A button is not an editable target.
        {"operation": "EVAL", "target": None},
        {"operation": "CLICK", "target": "2", "note": {"code": "click()"}},
        "not an object",
    ],
)
def test_invalid_system2_output_is_rejected(monkeypatch, output):
    reasoner, _ = reviewer(monkeypatch, output)
    with pytest.raises(reasoner.ReviewUnavailable, match="Invalid System 2"):
        reasoner.review(page(), "Find a book", [], s1("DONE", system2=0.9))


def test_system2_needs_its_own_credential(monkeypatch):
    from jev_ultrafast import reasoner

    monkeypatch.delenv("REVIEW_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="REVIEW_MODEL_API_KEY"):
        reasoner.settings()


@pytest.fixture
def thinker(runner, monkeypatch):
    runner.reasoning = True
    runner.pending_review = None
    runner.state["reviews"] = []
    return runner


@pytest.mark.parametrize("system2, reviewed", [(0.9, True), (0.2, False)])
def test_jev_not_code_decides_when_system2_runs(thinker, monkeypatch, system2, reviewed):
    monkeypatch.setattr(loop, "choose", Mock(return_value=s1("DONE", system2=system2)))
    review = Mock(return_value={**s1("CLICK", "2", "e3"), "system": 2, "agrees": False})
    monkeypatch.setattr(loop.reasoner, "review", review)
    thinker.command("tick")
    assert review.called is reviewed
    assert thinker.state["status"] == ("ready" if reviewed else "done")


def test_system2_overrides_premature_done_and_its_note_reaches_both_models(thinker, monkeypatch):
    monkeypatch.setattr(loop, "choose", Mock(return_value=s1("DONE", system2=0.9)))
    review = Mock(return_value={**s1("TYPE_TEXT", "1", "e1"), "system": 2, "note": "Type the title first."})
    monkeypatch.setattr(loop.reasoner, "review", review)
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    thinker.command("tick")
    assert thinker.state["status"] == "ready" and len(thinker.state["reviews"]) == 1
    assert helper.call_args.args[0]["advice"] == "Type the title first."
    assert thinker.state["history"][-1]["system"] == 2 and thinker.state["history"][-1]["note"]
    sent = model.recent_actions(thinker.state["history"], ("action", "note"))
    assert sent[-1]["note"] == "Type the title first."
    assert model.recent_actions([{"action": "Go"}], ("action", "note")) == [{"action": "Go"}]


def test_stale_retry_reuses_identical_review(thinker, monkeypatch):
    monkeypatch.setattr(loop, "choose", Mock(return_value=s1("DONE", system2=0.9)))
    review = Mock(return_value={**s1("CLICK", "2", "e3"), "system": 2})
    monkeypatch.setattr(loop.reasoner, "review", review)
    thinker.state["browser"].act.side_effect = [StalePage("changed before input"), None]
    thinker.command("tick")
    thinker.command("tick")
    assert review.call_count == 1 and thinker.state["browser"].act.call_count == 2
    assert thinker.pending_review is None


def test_system2_is_off_by_default_and_budgeted(runner, monkeypatch):
    runner.reasoning = False
    runner.state["reviews"] = []
    choose = Mock(return_value=s1("DONE", system2=0.9))
    monkeypatch.setattr(loop, "choose", choose)
    review = Mock()
    monkeypatch.setattr(loop.reasoner, "review", review)
    runner.command("tick")
    assert runner.state["status"] == "done" and choose.call_args.kwargs["engage"] is False
    runner.reasoning, runner.state["status"] = True, "ready"
    runner.state["reviews"] = [{"fingerprint": "x"}] * loop.MAX_REVIEWS
    runner.command("tick")
    review.assert_not_called()
    assert choose.call_args.kwargs["engage"] is False


def test_late_or_failed_system2_leaves_system1_decision(thinker, monkeypatch):
    from jev_ultrafast import reasoner

    monkeypatch.setenv("REVIEW_MODEL_API_KEY", "test")
    monkeypatch.setenv("REVIEW_TIMEOUT", "0.05")
    monkeypatch.setattr(model, "post_json", lambda *_args: time.sleep(1))
    with pytest.raises(reasoner.ReviewUnavailable, match="deadline"):
        reasoner.review(page(), "Find a book", [], s1("DONE", system2=0.9))
    monkeypatch.setattr(loop, "choose", Mock(return_value=s1("CLICK", "2", "e3", system2=0.9)))
    thinker.command("tick")
    assert thinker.state["history"][-1]["action"] == "Go"  # Jev's validated non-terminal choice stands.
    assert "deadline" in thinker.state["reviews"][0]["failed"]


def test_unsure_stop_without_system2_answer_is_asked_again_not_accepted(thinker, monkeypatch):
    from jev_ultrafast import reasoner

    monkeypatch.setattr(loop, "choose", Mock(return_value=s1("BLOCKED", system2=0.9)))
    monkeypatch.setattr(loop.reasoner, "review", Mock(side_effect=reasoner.ReviewUnavailable("late")))
    for _ in range(3):
        thinker.command("tick")
        assert thinker.state["status"] == "ready"
    with pytest.raises(ValueError, match="identical Jev request"):
        thinker.command("tick")
    assert thinker.state["status"] == "blocked" and len(thinker.state["reviews"]) == 3


def test_jev_routes_through_openrouter_without_a_typesafe_key(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "router")
    monkeypatch.setenv("TYPESAFE_MODEL", "jev-latest")
    assert model.jev_endpoint() == ("https://openrouter.ai/api/alpha/decisions", "router", "~typesafe/jev-latest")
    monkeypatch.setenv("TYPESAFE_API_KEY", "direct")
    assert model.jev_endpoint()[:2] == ("https://api.typesafe.ai/v1/systemone", "direct")


def test_shared_openrouter_key_is_never_sent_elsewhere(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "router")
    assert model.api_key("TEXT_MODEL", "https://openrouter.ai/api/v1") == "router"
    assert model.api_key("TEXT_MODEL", "https://api.deepseek.com/v1") is None
    assert model.api_key("TEXT_MODEL", "https://openrouter.ai.evil.test/v1") is None


def test_call_budget_stops_before_the_next_model_call(runner, monkeypatch):
    monkeypatch.setenv("JEV_MAX_CALLS", "2")
    choose = Mock(return_value=s1("CLICK", "2", "e3"))
    monkeypatch.setattr(loop, "choose", choose)
    runner.state["text_calls"] = [{"usage": {}}]
    runner.command("predict")
    with pytest.raises(ValueError, match="2-call model budget"):
        runner.command("predict")
    assert choose.call_count == 1 and runner.state["status"] == "blocked"


def test_identical_jev_request_is_never_sent_a_fourth_time(runner, monkeypatch):
    # A page that keeps going stale re-observes the same state; the same request must not loop forever.
    choose = Mock(return_value=s1("CLICK", "2", "e3"))
    monkeypatch.setattr(loop, "choose", choose)
    for _ in range(3):
        runner.command("predict")
    with pytest.raises(ValueError, match="identical Jev request"):
        runner.command("predict")
    assert choose.call_count == 3 and runner.state["status"] == "blocked"
    runner.state["browser"].act.assert_not_called()


def test_repeated_attempts_on_a_changing_page_stop_before_a_fourth_try(runner, monkeypatch):
    helper = Mock(return_value=("Bald Eagle", {"model": "test", "latency_ms": 1}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = StalePage("banner rotated")
    for attempt in range(3):
        runner.state["decisions"].append({"usage": {}})
        runner.state["decision"] = decision("e1")
        with pytest.raises(StalePage):
            runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
        runner.state["page"]["text"] = f"banner {attempt}"  # Each retry sees a different page and prompt.
    runner.state["decisions"].append({"usage": {}})
    runner.state["decision"] = decision("e1")
    with pytest.raises(ValueError, match="chosen 3 times without executing"):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["status"] == "blocked" and helper.call_count == 3


def test_submit_is_offered_for_editable_fields_only():
    p = page()
    p["actions"].append({**p["actions"][0], "id": "e1-enter", "kind": "submit"})
    elements, targets, _ = model.action_space(p["actions"])
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK", "PRESS_ENTER"]
    assert list(targets["PRESS_ENTER"]) == ["1"] and targets["PRESS_ENTER"]["1"]["id"] == "e1-enter"


def test_unexecuted_attempt_is_reported_to_jev_then_cleared(runner, monkeypatch):
    runner.state["browser"].act.side_effect = [StalePage("Target changed or is covered. Observe again."), None]
    runner.state["decision"] = decision("e3")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.failed == {"action": "Go", "kind": "click", "reason": "Target changed or is covered. Observe again."}
    sent = []
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", lambda *a: sent.append(a[2]) or jev(0.1, "WAIT")(*a))
    runner.command("predict")
    assert sent[0]["state"]["last_failed_attempt"]["action"] == "Go"
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.failed is None


def test_text_helper_without_a_value_is_a_failed_attempt_not_a_crash(runner, monkeypatch):
    monkeypatch.setattr(loop, "field_text", Mock(side_effect=model.NoTextValue("no value")))
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.failed["reason"] == "no value" and runner.state["status"] == "predicted"
    runner.state["browser"].act.assert_not_called()


def test_huge_dropdowns_are_capped_and_capped_options_cannot_be_chosen():
    actions = [
        {"id": f"s{i}", "kind": "select", "label": f"Model → iPhone {i}", "value": str(i), "node": 5}
        for i in range(100)
    ]
    elements, targets, _ = model.action_space(actions)
    assert len(elements[0]["options"]) == model.MAX_OPTIONS == len(targets["SELECT"])
    assert "1:21" not in targets["SELECT"] and elements[0]["options"][0]["label"] == "iPhone 0"
    many = [{**a, "node": 5 + i // 20, "id": f"t{i}"} for i, a in enumerate(actions * 2)]
    assert len(model.action_space(many)[1]["SELECT"]) == model.MAX_PAGE_OPTIONS


def test_covered_controls_are_not_offered():
    p = page()
    p["actions"][2]["covered"] = True  # "Go" sits under a banner.
    _, targets, _ = model.action_space(p["actions"])
    assert "e3" not in {a["id"] for a in targets["CLICK"].values()}


@pytest.mark.parametrize(
    "body, message",
    [
        ({"task": "custom", "url": "javascript:alert(1)", "goal": "Open it"}, "http"),
        ({"task": "custom", "url": "file:///etc/passwd", "goal": "Open it"}, "http"),
        ({"task": "nope", "goal": "Open it"}, "Unknown test task"),
        ({"task": "filters", "goal": ""}, "1–2,000"),
    ],
)
def test_console_rejects_unsafe_or_unknown_runs_before_opening_a_browser(monkeypatch, body, message):
    from jev_ultrafast import demo

    opened = Mock()
    monkeypatch.setattr(demo, "Agent", opened)
    with pytest.raises(ValueError, match=message):
        demo.command("reset", body)
    opened.assert_not_called()


def test_every_task_has_a_goal_start_url_and_check():
    from jev_ultrafast.tasks import tasks

    all_tasks = tasks("http://127.0.0.1:8766")
    assert {t.suite for t in all_tasks.values()} == {"core", "wildlife", "reallife"}
    assert all(t.goal and t.url.startswith("http") and callable(t.check) for t in all_tasks.values())


def test_closing_a_tab_that_is_already_gone_is_not_an_error(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.target = "gone"
    cdp = Mock(side_effect=RuntimeError("{'code': -32602, 'message': 'No target with given id found'}"))
    monkeypatch.setattr(browser, "cdp", cdp)
    b.close()
    b.close()
    assert b.target is None and cdp.call_count == 1
    b.target = "other"
    cdp.side_effect = RuntimeError("Browser disconnected")
    with pytest.raises(RuntimeError, match="disconnected"):
        b.close()


def test_a_failed_tab_close_does_not_block_the_next_run(monkeypatch):
    from jev_ultrafast import demo

    monkeypatch.setattr(demo, "AGENT", Mock(close=Mock(side_effect=RuntimeError("Browser disconnected"))))
    with pytest.raises(RuntimeError):
        demo.close_browser()
    assert demo.AGENT is None
