"""Live System 1 vs System 1 + System 2 comparison. Paid APIs; not run by pytest.

uv run python scripts/compare_systems.py [--suite all|core|wildlife|reallife] [--tasks NAME ...] [--max-calls N]
"""

import argparse
import json
import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

from jev_ultrafast import Agent, demo
from jev_ultrafast.tasks import tasks, verify

TASKS = tasks(demo.ORIGIN)


def run(name, reasoning, folder, max_actions):
    _, url, goal, check, _ = TASKS[name]
    error = None
    with Agent(url, goal, reasoning=reasoning) as agent:
        try:
            for state in agent.run():
                last = state["history"][-1] if state["history"] else {}
                tag = f"S{last.get('system', 1)}" if last else ""
                print(f"  {state['elapsed_ms']:>6} ms {tag} {last.get('action', '')[:70]}", flush=True)
                if len(state["history"]) >= max_actions:
                    raise RuntimeError(f"Stopped at {max_actions} actions")
        except Exception as exc:  # A failed run is a result, not a reason to stop the comparison.
            error = f"{type(exc).__name__}: {exc}"
        state = agent.snapshot()
        passed = error is None and state["status"] == "done" and verify(check, agent)["passed"]
    reviews = state["reviews"]
    result = {
        "task": name,
        "mode": "system1+2" if reasoning else "system1",
        "passed": bool(passed),
        "status": state["status"],
        "error": error,
        "ms": state["elapsed_ms"],
        "actions": len(state["history"]),
        "system1_calls": len(state["decisions"]),
        "system2_calls": len(reviews),
        "system2_overrides": sum(not r["agrees"] for r in reviews),
        "system2_fallbacks": sum("failed" in r for r in reviews),
        "system2_ms": sum(r["latency_ms"] for r in reviews),
        "text_calls": len(state["text_calls"]),
        "reviews": [
            {k: r.get(k) for k in ("system2_probability", "operation", "target", "agrees", "failed", "note")}
            | {"latency_ms": r["latency_ms"]}
            for r in reviews
        ],
        "trace": [
            {k: h.get(k) for k in ("system", "action", "text", "page_changed", "note")} for h in state["history"]
        ],
        "url": state["page"]["url"],
    }
    (folder / f"{name}-{result['mode']}-{datetime.now(timezone.utc):%H%M%S%f}.json").write_text(
        json.dumps({**result, "state": state}, indent=2, default=str)
    )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=["all", *sorted({t.suite for t in TASKS.values()})])
    parser.add_argument("--tasks", nargs="+", choices=TASKS)
    parser.add_argument("--max-calls", type=int, default=300, help="Stop the session after this many model calls.")
    parser.add_argument("--modes", nargs="+", choices=["system1", "system1+2"], default=["system1", "system1+2"])
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-actions", type=int, default=25)
    args = parser.parse_args()
    suite = args.suite or "core"
    names = args.tasks or [n for n, t in TASKS.items() if suite in {"all", t.suite}]
    demo.load_environment()
    server = ThreadingHTTPServer(("127.0.0.1", demo.PORT), demo.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    folder = Path("artifacts/systems") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder.mkdir(parents=True, exist_ok=True)
    print(f"Traces: {folder}", flush=True)
    # Alternate which mode runs first per task so neither always gets a warm cache.
    plan = [
        (name, mode)
        for repeat in range(args.repeats)
        for i, name in enumerate(names)
        for mode in (args.modes if (repeat + i) % 2 == 0 else args.modes[::-1])
    ]
    results = []
    try:
        for name, mode in plan:
            spent = sum(r["system1_calls"] + r["system2_calls"] + r["text_calls"] for r in results)
            if spent >= args.max_calls:
                # Session budget: stop starting runs rather than spend past the cap.
                print(f"Session budget reached after {spent} model calls; {len(plan) - len(results)} runs skipped")
                break
            print(f"{name} · {mode}", flush=True)
            results.append(run(name, mode == "system1+2", folder, args.max_actions))
            r = results[-1]
            print(
                f"  -> {'PASS' if r['passed'] else 'FAIL'} {r['ms']} ms, {r['actions']} actions, "
                f"S2 {r['system2_calls']}/{r['system2_overrides']} overrides/{r['system2_fallbacks']} late "
                f"{r['error'] or ''}",
                flush=True,
            )
    finally:
        server.shutdown()
        (folder / "summary.json").write_text(json.dumps(results, indent=2))
    print(f"\n{'task':<16} {'mode':<10} {'ok':<4} {'ms':>7} {'acts':>4} {'S1':>3} {'S2':>3} {'ovr':>3} {'S2 ms':>6}")
    for r in results:
        print(
            f"{r['task']:<16} {r['mode']:<10} {'yes' if r['passed'] else 'NO':<4} {r['ms']:>7} {r['actions']:>4} "
            f"{r['system1_calls']:>3} {r['system2_calls']:>3} {r['system2_overrides']:>3} {r['system2_ms']:>6}"
        )


if __name__ == "__main__":
    main()
