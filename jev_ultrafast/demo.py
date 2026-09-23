"""Loopback-only inspector for the Jev browser agent."""

import atexit
import json
import os
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import reasoner
from .agent import Agent
from .questions import MAX_STEPS
from .tasks import tasks, verify

ROOT = Path(__file__).parent
PORT = int(os.environ.get("TYPESAFE_DEMO_PORT", "8766"))
ORIGIN = f"http://127.0.0.1:{PORT}"
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.Lock()
AGENT = None
TASKS = tasks(ORIGIN)
RUN = {}  # The current run's task, mode, and independent verification.
RESULTS = []  # Finished runs this session, newest last.


def load_environment():
    path = Path.cwd() / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)


def system2_status():
    try:
        return {"available": True, "model": reasoner.settings()[0]}
    except ValueError as error:
        return {"available": False, "error": str(error)}


def finish_run():
    """Verify a stopped run once, from a fresh observation, and log it."""
    if not AGENT or RUN.get("verification") or AGENT.state["status"] not in {"done", "blocked"}:
        return
    state = AGENT.state
    check = RUN.get("check")
    if check is None:
        RUN["verification"] = {"passed": None, "checks": {}, "note": RUN["unverified"]}
    elif state["status"] != "done":
        RUN["verification"] = {"passed": False, "checks": {}, "note": RUN.get("stopped") or "Stopped without DONE"}
    else:
        try:
            RUN["verification"] = verify(check, AGENT)
        except Exception as error:
            RUN["verification"] = {"passed": False, "checks": {}, "note": f"Verification failed: {error}"}
    RESULTS.append(
        {
            "task": RUN["task"],
            "label": RUN["label"],
            "mode": "System 1 + 2" if AGENT.reasoning else "System 1",
            "passed": RUN["verification"]["passed"],
            "status": state["status"],
            "ms": state["elapsed_ms"],
            "actions": len(state["history"]),
            "system1_calls": len(state["decisions"]),
            "system2_calls": len(state["reviews"]),
            "system2_overrides": sum(not r["agrees"] for r in state["reviews"]),
            "system2_late": sum("failed" in r for r in state["reviews"]),
            "url": state["page"]["url"],
        }
    )


def response_state():
    finish_run()
    state = AGENT.snapshot() if AGENT else {"page": None, "status": "idle", "history": [], "decision": None}
    return {
        **state,
        "text_model": os.environ.get("TEXT_MODEL", "deepseek-chat"),
        "max_steps": MAX_STEPS,
        "tasks": [
            {"name": k, "label": t.label, "url": t.url, "goal": t.goal, "suite": t.suite} for k, t in TASKS.items()
        ],
        "system2": system2_status(),
        "run": {k: v for k, v in RUN.items() if k != "check"},
        "results": RESULTS,
    }


def close_browser():
    global AGENT
    if AGENT:
        AGENT.close()
        AGENT = None


def command(name, body):
    global AGENT
    if name == "reset":
        task = body.get("task", "flights")
        goal = body.get("goal", "").strip()
        if not goal or len(goal) > 2000:
            raise ValueError("Enter 1–2,000 characters")
        if task == "custom":
            url = body.get("url", "").strip()
            if urlparse(url).scheme not in {"http", "https"} or not urlparse(url).hostname or len(url) > 2000:
                raise ValueError("Enter an http(s) start URL")
            label, check, unverified = "Custom · " + urlparse(url).hostname, None, "Custom task: no verifier"
        elif task in TASKS:
            label, url, preset_goal, check, _ = TASKS[task]
            unverified = None
            if goal != preset_goal:
                # A preset check only proves the preset goal.
                check, unverified = None, "Goal edited: preset check not applied"
        else:
            raise ValueError("Unknown test task")
        close_browser()
        RUN.clear()
        AGENT = Agent(
            url,
            goal,
            screenshots=True,
            record_dir=Path.cwd() / "artifacts" / "frames" if body.get("record") else None,
            reasoning=bool(body.get("reasoning")),
        )
        RUN.update(task=task, label=label, check=check, unverified=unverified)
        AGENT.state["scenario"] = task
    elif name == "stop":
        # Ends a run that failed mid-way, so it is logged as a result instead of silently abandoned.
        if AGENT and AGENT.state["status"] not in {"done", "blocked"}:
            AGENT.state["status"] = "blocked"
            RUN["stopped"] = str(body.get("reason", "Stopped by the tester"))[:300]
    else:
        if AGENT is None:
            raise ValueError("Start a demo first")
        AGENT.command(name, body)
    return response_state()


class Handler(BaseHTTPRequestHandler):
    def send(self, status, content, mime="application/json"):
        content = content if isinstance(content, bytes) else content.encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.headers.get("Host") != f"127.0.0.1:{PORT}":
            return self.send(403, "Forbidden", "text/plain")
        path = urlparse(self.path).path
        if path == "/api/state":
            with LOCK:
                return self.send(200, json.dumps(response_state()))
        if path == "/demo.mp4":
            video = ROOT.parent / "docs" / "demo.mp4"
            if video.exists():
                return self.send(200, video.read_bytes(), "video/mp4")
        files = {
            "/": ("index.html", "text/html"),
            "/app.js": ("app.js", "text/javascript"),
            "/style.css": ("style.css", "text/css"),
            "/fixture.html": ("fixture.html", "text/html"),
            "/wildlife.html": ("wildlife.html", "text/html"),
        }
        if path not in files:
            return self.send(404, "Not found", "text/plain")
        name, mime = files[path]
        content = (ROOT / "static" / name).read_text().replace("__TOKEN__", TOKEN)
        self.send(200, content, mime + "; charset=utf-8")

    def do_POST(self):
        if (
            self.headers.get("Host") != f"127.0.0.1:{PORT}"
            or self.headers.get("X-Demo-Token") != TOKEN
            or self.headers.get("Origin") not in (None, ORIGIN)
        ):
            return self.send(403, json.dumps({"error": "Local demo requests only"}))
        if not LOCK.acquire(blocking=False):
            return self.send(409, json.dumps({"error": "A browser step is already running"}))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length < 8192:
                raise ValueError("Invalid request size")
            body = json.loads(self.rfile.read(length))
            result = command(self.path.removeprefix("/api/"), body)
            self.send(200, json.dumps(result))
        except (ValueError, RuntimeError, TimeoutError) as error:
            self.send(400, json.dumps({"error": str(error)}))
        except Exception:
            self.send(500, json.dumps({"error": "Local demo failed; no automatic retry. Reset to recover."}))
        finally:
            LOCK.release()

    def log_message(self, *_args):
        pass


def main():
    load_environment()
    atexit.register(close_browser)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Jev Ultrafast: {ORIGIN}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
