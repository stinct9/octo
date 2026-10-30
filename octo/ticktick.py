"""Send a review's "What to do" tasks to the owner's TickTick (their ask, 2 Oct).

TickTick's Open API with a **personal API token** (TickTick web → avatar → Settings → Account →
API Token): no OAuth app, no client secret. The owner pastes the token into `octo ticktick setup`, which
checks it and keeps it in data/ticktick.json (gitignored, readable by them only). Nothing is sent
until they press "Add to TickTick" on a task: its title, details, why, due date, and a link back
to the conversation on this Mac.

Docs read 2 Oct 2026 (developer.ticktick.com/docs): POST /open/v1/task (title, projectId, content,
dueDate "yyyy-MM-dd'T'HH:mm:ssZ", isAllDay, timeZone, tags), GET /open/v1/project,
POST /open/v1/preference (the account's time zone).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import DATA

API = "https://api.ticktick.com/open/v1"
CONFIG = DATA / "ticktick.json"
TAG = "conversation-lab"
LAB_URL = "http://127.0.0.1:8765"


class TickTickError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status  # TickTick's HTTP status, when it answered


# ---- the token and settings -------------------------------------------------------------------

def load() -> dict | None:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return None


def save(cfg: dict) -> None:
    from . import files as FL
    with FL.locked(CONFIG):  # whole, its own temporary file, one writer at a time (octo/files.py)
        FL.write_private(CONFIG, json.dumps(cfg, indent=2))


def status() -> dict:
    cfg = load()
    if not cfg or not cfg.get("token"):
        return {"connected": False}
    return {"connected": True, "list": cfg.get("project_name") or "Inbox"}


def connect(token: str, list_name: str | None = None) -> dict:
    """Check the token against TickTick (it must list his projects), then keep it. Raises TickTickError."""
    token = token.strip()
    if not token:
        raise TickTickError("Paste the token first")
    lists = projects(token)
    chosen = None
    if list_name:
        chosen = next((p for p in lists if p["name"].lower() == list_name.lower()), None)
        if not chosen:
            raise TickTickError(f"No list called {list_name!r}; yours: " + ", ".join(p["name"] for p in lists))
    save({"token": token, "timezone": timezone(token), "project_id": chosen["id"] if chosen else None,
          "project_name": chosen["name"] if chosen else None})
    return settings()


def choose_list(project_id: str | None) -> dict:
    cfg = load()
    if not cfg or not cfg.get("token"):
        raise TickTickError("TickTick isn't connected")
    if project_id:
        chosen = next((p for p in projects(cfg["token"]) if p["id"] == project_id), None)
        if not chosen:
            raise TickTickError("That list isn't in your TickTick any more")
        cfg.update(project_id=chosen["id"], project_name=chosen["name"])
    else:
        cfg.update(project_id=None, project_name=None)
    save(cfg)
    return settings()


def disconnect() -> None:
    CONFIG.unlink(missing_ok=True)


def settings() -> dict:
    """What the Settings page shows. Never the token itself: only its last four characters."""
    cfg = load()
    if not cfg or not cfg.get("token"):
        return {"connected": False}
    out = {"connected": True, "list_id": cfg.get("project_id"), "list": cfg.get("project_name") or "Inbox",
           "timezone": cfg.get("timezone"), "token_ends": cfg["token"][-4:], "lists": None, "error": None}
    try:
        out["lists"] = [{"id": p["id"], "name": p["name"]} for p in projects(cfg["token"])]
    except TickTickError as e:
        out["error"] = str(e)
    return out


# ---- talking to TickTick ----------------------------------------------------------------------

def _call(method: str, path: str, token: str, body: dict | None = None, timeout: float = 15) -> object:
    req = urllib.request.Request(f"{API}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise TickTickError("TickTick refused the token: make a new one and run `octo ticktick setup`") from e
        raise TickTickError(f"TickTick said {e.code}: {e.read()[:200].decode(errors='replace')}", e.code) from e
    except OSError as e:
        raise TickTickError(f"Couldn't reach TickTick: {e}") from e
    return json.loads(raw) if raw.strip() else None


def projects(token: str) -> list[dict]:
    return [p for p in (_call("GET", "/project", token) or []) if not p.get("closed")]


def timezone(token: str) -> str:
    try:
        return (_call("POST", "/preference", token) or {}).get("timeZone") or "Asia/Kolkata"
    except TickTickError:
        return "Asia/Kolkata"


def due_fields(due: str | None, tz: str) -> dict:
    """An all-day due date: midnight on that day in the owner's time zone, written in UTC as TickTick wants."""
    if not due:
        return {}
    try:
        d = date.fromisoformat(due)
    except ValueError:
        return {}
    local = datetime(d.year, d.month, d.day, tzinfo=ZoneInfo(tz))
    return {"dueDate": local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S+0000"),
            "startDate": local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S+0000"),
            "isAllDay": True, "timeZone": tz}


def content(task: dict, when: str, session_id: str) -> str:
    parts = [task.get("detail") or ""]
    if task.get("why"):
        parts.append(f"Why: {task['why']}")
    parts.append(f"From the conversation of {when}: {LAB_URL}/#/c/{session_id}")
    return "\n\n".join(p for p in parts if p)


def add(task: dict, when: str, session_id: str, cfg: dict | None = None,
        call=_call) -> dict:
    """Create the task; returns {"task_id", "project_id", "url"}."""
    cfg = load() if cfg is None else cfg  # {} means "not set up", never "read the real one"
    if not cfg or not cfg.get("token"):
        raise TickTickError("TickTick isn't set up: run `octo ticktick setup` in a terminal")
    tz = cfg.get("timezone") or "Asia/Kolkata"
    body = {"title": task["title"], "content": content(task, when, session_id), "tags": [TAG],
            **due_fields(task.get("due"), tz)}
    # The Inbox has no id of its own in the docs: leave projectId out first, then try "inbox".
    tries = [{"projectId": cfg["project_id"]}] if cfg.get("project_id") else [{}, {"projectId": "inbox"}]
    last: Exception | None = None
    for extra in tries:
        try:
            got = call("POST", "/task", cfg["token"], {**body, **extra}) or {}
            if got.get("id"):
                pid = got.get("projectId") or extra.get("projectId") or "inbox"
                return {"task_id": got["id"], "project_id": pid,
                        "url": f"https://ticktick.com/webapp/#p/{pid}/tasks/{got['id']}"}
        except TickTickError as e:
            if "refused the token" in str(e):
                raise
            if not (e.status and 400 <= e.status < 500):
                # No answer, or a server error: the task may well have been made. Trying the Inbox now
                # could make a second one (review 4 Oct, M23).
                raise TickTickError(f"{e} · check TickTick for the task before trying again") from e
            last = e
    raise last or TickTickError("TickTick didn't return the new task")


def done(sent_tasks: dict[str, dict], cfg: dict | None = None, call=_call) -> dict[str, bool | None]:
    """Each sent task: completed in TickTick (True), not yet (False), or couldn't tell (None: no token,
    TickTick unreachable, the task deleted or moved). Read-only; nothing is changed in TickTick."""
    cfg = load() if cfg is None else cfg  # {} means "not set up", never "read the real one"
    out: dict[str, bool | None] = {}
    for item, t in sent_tasks.items():
        if not cfg or not cfg.get("token") or not t.get("project_id"):
            out[item] = None
            continue
        try:
            got = call("GET", f"/project/{t['project_id']}/task/{t['task_id']}", cfg["token"], timeout=8) or {}
            out[item] = got.get("status") == 2 if "status" in got else None  # 0 open, 2 completed
        except TickTickError:
            out[item] = None
    return out


# ---- which tasks were sent (so a second press doesn't make a second task) ---------------------

def sent(conn, insight_run: str) -> dict[str, dict]:
    from .db import rows
    return {r["item"]: {"task_id": r["task_id"], "project_id": r["project_id"], "url": r["url"], "added_at": r["added_at"]}
            for r in rows(conn, "SELECT * FROM ticktick_tasks WHERE insight_run=?", insight_run)}


def record(conn, insight_run: str, item: str, session_id: str, got: dict) -> None:
    conn.execute("INSERT OR REPLACE INTO ticktick_tasks (insight_run, item, session_id, task_id, project_id, url, added_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (insight_run, item, session_id, got["task_id"], got["project_id"], got["url"], time.time()))
