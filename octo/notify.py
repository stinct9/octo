"""Local macOS notifications: something is waiting for the owner (review 4 Oct, M28).

Until 4 Oct nothing said a conversation was ready, a step had failed, or the recorder had gone
quiet: they had to remember to open the page, and 52 h passed without new audio unnoticed. The server
now checks every 5 minutes and posts a notification on this Mac when something new needs them:

  - a conversation ready to review, or with speakers to confirm
  - a step that failed and waits for Try again (not one that will retry by itself)
  - the recorder unseen for QUIET_H hours while it was set to record
  - the coach's note, once it's written and until it's opened (6 Oct)
  - your week, from Monday morning until its card is dismissed (B17, 21 Oct: octo/week.py)

**Counts only**: no names, no transcript text, no titles of review steps. **One exception (B23, Jay 21 Oct):**
10 min before an event with someone Octo knows, octo/events.py posts the event's name and time as the calendar has
them ("1:1 with Priya in 10 min"), opening that person's page; never anything from reviews, commitments or
intentions. Every banner from this module stays counts only. It goes to Notification Centre
through the Octo app while it's open (19 Oct, docs/mac-app-plan.md: the banner says Octo), else through
`osascript` (it says Script Editor), and leaves the Mac nowhere. Each thing is announced once; what
was announced is kept in data/notify.json (ids only), so a restart doesn't repeat it.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time

from . import DATA

STATE = DATA / "notify.json"
# Away from home the recorder keeps recording; the Mac just can't see it, so a workday away is
# normal. Unseen for longer than a day out plus a night in means it's off, flat or not syncing.
QUIET_H = 14
TITLE = "Octo"  # one name (product review P0-9)


def waiting(conn, now: float | None = None) -> set[str]:
    """What needs the owner right now, one key per thing: "review:<session>", "confirm:<session>",
    "failed:<session>", "quiet:<last seen>", "coach:<note>", "week:<Monday>"."""
    from . import device as D
    from . import sessions as SV
    from .db import rows
    now = time.time() if now is None else now
    keys = set()
    for s in rows(conn, "SELECT id FROM sessions WHERE status='active'"):
        t = SV.todo(conn, s["id"])
        if t["kind"] in ("review", "confirm") or (t["kind"] == "failed" and not t.get("auto_retry")):
            keys.add(f"{t['kind']}:{s['id']}")
    for n in rows(conn, "SELECT id FROM coach_notes WHERE status='done' AND seen_at IS NULL AND finished_at > ?",
                  now - 86400):
        keys.add(f"coach:{n['id']}")
    from .week import announce
    if week := announce(conn, now):
        keys.add(week)
    last = D.last_status() or {}
    seen, want = last.get("seen_at"), (last.get("status") or {}).get("want_on")
    if D.load_config() and seen and now - seen > QUIET_H * 3600 and want is not False:
        keys.add(f"quiet:{int(seen)}")
    return keys


def message(new: set[str], now: float | None = None) -> str | None:
    """One line of counts for the things not announced before, or None."""
    now = time.time() if now is None else now
    n = {k: sum(1 for x in new if x.startswith(f"{k}:")) for k in ("review", "confirm", "failed")}
    parts = ["Your week is ready"] if any(x.startswith("week:") for x in new) else []
    if n["review"]:
        parts.append(f"{n['review']} conversation{'s' if n['review'] > 1 else ''} ready to review")
    if n["confirm"]:
        parts.append(f"{n['confirm']} with speakers to confirm")
    if n["failed"]:
        parts.append(f"{n['failed']} failed: press Try again")
    if any(x.startswith("coach:") for x in new):
        parts.append("the coach's note is ready")
    quiet = [int(x.split(":", 1)[1]) for x in new if x.startswith("quiet:")]
    if quiet:
        parts.append(f"recorder not seen for {int((now - max(quiet)) // 3600)} h")
    return " · ".join(parts) or None


# The Octo app (macapp/Octo.swift) holds GET /api/octo/mac-app/notice open. While it does, or did under APP_GAP_S
# ago (between one request and the next), post() hands it the text instead of running osascript: the lab never runs
# the app's code (architecture review, 19 Oct: /Applications is writable by any admin). Texts are numbered and only
# the app's next request (`after`) counts as having got one: a request left waiting by an app that quit can't take a
# text with it (measured 19 Oct). One nobody has asked past within APP_LATE_S goes out through osascript after all.
APP_GAP_S = 10
APP_LATE_S = 15
_app = threading.Condition()
_app_texts: list[tuple[int, float, str, str | None]] = []  # (number, when, text, page), the last APP_LATE_S * 4 seconds
_app_seq = 0  # the last number given
_app_got = 0  # the highest number the app has said it got
_app_waiting = 0
_app_left = 0.0
_app_mode = "on"  # "on", "ask" (macOS hasn't asked the owner yet), "off" (the owner turned Octo's banners off)


def app_open(now: float | None = None) -> bool:
    return _app_waiting > 0 or (time.time() if now is None else now) - _app_left < APP_GAP_S


def _osascript(text: str) -> None:
    subprocess.run(["osascript", "-e", "on run argv", "-e", f'display notification (item 1 of argv) with title "{TITLE}"',
                    "-e", "end run", text], capture_output=True, timeout=10)


def post(text: str, late=None, page: str | None = None) -> str:
    """A Notification Centre banner; returns how: "app", "off" (the owner turned Octo's banners off in System
    Settings: nobody posts), or "osascript" (the text an argument, never part of the script). `page`: what a click
    on the app's banner opens (osascript's opens nothing)."""
    global _app_seq, _app_texts
    with _app:
        if app_open():
            if _app_mode == "off":
                return "off"
            _app_seq += 1
            now = time.time()
            _app_texts = [t for t in _app_texts if now - t[1] < APP_LATE_S * 4] + [(_app_seq, now, text, page)]
            _app.notify_all()
            (late or _late)(_app_seq, text)
            return "app"
    _osascript(text)
    return "osascript"


def _late(seq: int, text: str) -> None:
    timer = threading.Timer(APP_LATE_S, unclaimed, (seq, text))
    timer.daemon = True
    timer.start()


def unclaimed(seq: int, text: str, send=_osascript) -> bool:
    """APP_LATE_S after a text went to the app: if it never asked past it, osascript posts it. True if it did."""
    global _app_texts
    with _app:
        if _app_got >= seq:
            return False
        _app_texts = [t for t in _app_texts if t[0] != seq]  # so the app doesn't post it too, later
    send(text)
    return True


def app_wait(mode: str, after: int | None, timeout: float = 50) -> tuple[int, str | None]:
    """The app's long poll: (number, text) of the first text after `after`, or (number, None) after `timeout`.
    `after` None, or past the last number (the lab restarted): (the last number, None) at once, so it starts there."""
    global _app_waiting, _app_left, _app_mode, _app_got
    with _app:
        _app_mode, _app_waiting = mode, _app_waiting + 1
        try:
            if after is None or after > _app_seq:
                return _app_seq, None
            _app_got = max(_app_got, after)
            _app.wait_for(lambda: any(t[0] > after for t in _app_texts), timeout)
            return next(((n, text) for n, _, text, _ in _app_texts if n > after), (after, None))
        finally:
            _app_waiting -= 1
            _app_left = time.time()


def app_page(seq: int) -> str | None:
    """The page a click on text `seq` opens, if it has one."""
    return next((page for n, _, _, page in _app_texts if n == seq), None)


def menu(keys: set[str], now: float | None = None) -> list[dict]:
    """The app's menu lines for what's waiting, in notify's words, each with the page it opens."""
    now = time.time() if now is None else now
    out = []
    for kind, one, many in (("review", "conversation ready to review", "conversations ready to review"),
                            ("confirm", "conversation with speakers to confirm", "conversations with speakers to confirm"),
                            ("failed", "conversation with a failed step", "conversations with a failed step")):
        ids = sorted(k.split(":", 1)[1] for k in keys if k.startswith(f"{kind}:"))
        if ids:
            out.append({"text": f"{len(ids)} {one if len(ids) == 1 else many}",
                        "page": f"/c/{ids[0]}" if len(ids) == 1 else "/", "needs": True})
    if any(k.startswith("coach:") for k in keys):
        out.append({"text": "The coach's note is ready", "page": "/coach", "needs": True})
    if any(k.startswith("week:") for k in keys):
        out.append({"text": "Your week is ready", "page": "/", "needs": True})
    quiet = [int(k.split(":", 1)[1]) for k in keys if k.startswith("quiet:")]
    if quiet:
        out.append({"text": f"Recorder not seen for {int((now - max(quiet)) // 3600)} h", "page": "/", "needs": True})
    return out


def _load() -> set[str] | None:
    try:
        return set(json.loads(STATE.read_text())["announced"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def check(conn, send=post, now: float | None = None) -> str | None:
    """Announce what's new since the last check; returns the text posted, if any. Only what's still
    waiting is remembered, so a conversation that needs the owner again later is announced again."""
    keys = waiting(conn, now)
    before = _load() or set()
    text = message(keys - before, now)
    if text:
        send(text)
    from . import files as FL
    FL.write_private(STATE, json.dumps({"announced": sorted(keys), "checked_at": time.time()}), 0o644)
    return text
