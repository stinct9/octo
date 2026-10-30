"""Your week, and "Octo hasn't heard from…" (B17, Jay 19 Oct; docs/backlog.md).

- **The week's card**, on home from Monday to the next Monday, about the seven days before (local time): conversations
  heard, reviewed and acted on; the focus's number that week against the usual (octo/measures.py); commitments open
  and due (B14); the coach's latest theme. Gone once dismissed, replaced by the next Monday's. Earlier weeks are on
  Progress (counts and the focus only: what's open now belongs to this week's card).
- **Quiet people:** someone with conversations on ≥ 3 days has a usual gap (the median between those days); once it's
  been twice that since the last, their page and the card say what Octo heard, since a call it didn't record looks
  like a gap too. Muted per person (app_config `quiet_muted`, person ids).
- **The notification** "Your week is ready", from Monday 08:00 until the card is dismissed (octo/notify.py, no names).

Read from what's stored: no model call, nothing re-runs, no daily recap, no streaks.
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import date, datetime, timedelta

from .db import config, load_json, row, rows

MORNING_H = 8  # the notification waits for Monday morning


def monday(now: float | None = None) -> date:
    """This week's Monday, local time."""
    d = datetime.fromtimestamp(time.time() if now is None else now).date()
    return d - timedelta(days=d.weekday())


def _ts(d: date) -> float:
    return datetime.combine(d, datetime.min.time()).timestamp()  # local midnight


def _set(conn, key: str, value) -> None:
    conn.execute("INSERT INTO app_config (key, value, updated_at) VALUES (?, ?, ?) "
                 "ON CONFLICT (key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                 (key, json.dumps(value), time.time()))


# ---- quiet people -------------------------------------------------------------------------------

def _ago(days: int) -> str:
    if days < 14:
        return f"{days} day{'s' if days != 1 else ''}"
    if days < 60:
        return f"{round(days / 7)} weeks"
    return f"{round(days / 30)} months"


def _often(days: float) -> str:
    if days <= 1.5:
        return "about daily"
    if days < 5:
        return f"about every {round(days)} days"
    if days < 10.5:
        return "about weekly"
    if days < 25:
        return f"about every {round(days / 7)} weeks"
    if days < 45:
        return "about monthly"
    return f"about every {round(days / 30)} months"


def muted(conn) -> set[str]:
    return set(config(conn, "quiet_muted", []))


def mute(conn, person_id: str, on: bool) -> None:
    ids = muted(conn) - {person_id} | ({person_id} if on else set())
    _set(conn, "quiet_muted", sorted(ids))


def heard(conn, person_id: str, name: str, now: float | None = None) -> dict | None:
    """Their usual gap and how long since the last conversation, or None under 3 days with them. `text` only once it's
    been twice the usual. Days, not conversations: two on one day (a split call) would make a usual of no time at all."""
    from .person import with_them
    days = sorted({datetime.fromtimestamp(s["at"]).date() for s in with_them(conn, person_id) if s["at"]})
    if len(days) < 3:
        return None
    usual = statistics.median((b - a).days for a, b in zip(days, days[1:]))
    since = (datetime.fromtimestamp(time.time() if now is None else now).date() - days[-1]).days
    quiet = since >= 2 * usual
    return {"person_id": person_id, "name": name, "usual_days": usual, "since_days": since, "muted": person_id in muted(conn),
            "text": f"Octo hasn't heard a conversation with {name} in {_ago(since)}; usually {_often(usual)}" if quiet else None}


def quiet(conn, now: float | None = None) -> list[dict]:
    """Everyone due a conversation by their own usual, longest quiet first; the muted left out."""
    off = muted(conn)
    got = [h for p in rows(conn, "SELECT id, name FROM people WHERE is_self=0") if p["id"] not in off
           and (h := heard(conn, p["id"], p["name"], now)) and h["text"]]
    return sorted(got, key=lambda h: -h["since_days"])


# ---- the card ----------------------------------------------------------------------------------

def counts(conn, start: float, end: float) -> dict:
    """Conversations heard that week, reviews finished, review items marked acted on (by when the mark was last saved)."""
    from .parts import heads
    head = heads(conn)  # B35: one discussion in parts counts once
    heard_n = len({head.get(r["id"], r["id"]) for r in rows(
        conn, "SELECT s.id FROM sessions s JOIN recordings r ON r.id = s.recording_id WHERE s.status='active' "
              "AND coalesce(r.started_at + s.start_s, r.created_at) >= ? AND coalesce(r.started_at + s.start_s, r.created_at) < ?",
        start, end)})
    reviewed = len({head.get(r["id"], r["id"]) for r in rows(
        conn, "SELECT id FROM sessions WHERE status='active' AND reviewed_at >= ? AND reviewed_at < ?", start, end)})
    # shortcut: feedback keeps no time of its own for "acted", so a later note on an acted item moves it to that week.
    acted = row(conn, "SELECT count(*) n FROM feedback f JOIN sessions s ON s.id = f.session_id WHERE s.status='active' "
                      "AND f.acted=1 AND f.updated_at >= ? AND f.updated_at < ?", start, end)["n"]
    return {"heard": heard_n, "reviewed": reviewed, "acted": acted}


def _focus(conn, start: float, end: float) -> dict | None:
    """The focus active at the week's end; with a number, that week's against the usual (the talk just before it)."""
    from . import focus as F
    from .measures import MEASURES, since_before
    f = F.at(conn, end - 1)
    if not f:
        return None
    got = since_before(conn, f["measure"], start, end) if f.get("measure") in MEASURES else None
    return {"id": f["id"], "text": f["text"], "measure": f.get("measure"),
            "week": got and got["since"], "usual": got and got["before"]}


def _commitments(conn, sunday: date) -> dict:
    from . import commitments as CM
    open_ = [CM.view(c) for c in rows(conn, CM._SELECT + "WHERE c.state IS NULL AND s.status='active' ORDER BY c.due, at")]
    return {"mine": sum(1 for c in open_ if c["mine"]), "theirs": sum(1 for c in open_ if not c["mine"]),
            "due": [c for c in open_ if c["due"] and c["due"] <= sunday.isoformat()]}


def _coach(conn) -> dict | None:
    n = row(conn, "SELECT id, created_at, output FROM coach_notes WHERE status='done' ORDER BY created_at DESC LIMIT 1")
    work = (load_json(n["output"]) or {}).get("work_on") if n else None
    return {"note_id": n["id"], "at": n["created_at"], "theme": work["theme"] if work else None} if n else None


def card(conn, week: date, now: float | None = None) -> dict:
    """The card for the seven days from `week` (a Monday). Commitments, quiet people and the coach only on this week's
    card (the one shown from the Monday after), since they're what's open now."""
    now = time.time() if now is None else now
    start, end = _ts(week), _ts(week + timedelta(days=7))
    out = {"week": week.isoformat(), "start": start, "end": end, "counts": counts(conn, start, end),
           "focus": _focus(conn, start, end), "current": week == monday(now) - timedelta(days=7)}
    if out["current"]:
        out |= {"commitments": _commitments(conn, monday(now) + timedelta(days=6)), "quiet": quiet(conn, now),
                "coach": _coach(conn)}
    return out


def ready(conn, now: float | None = None) -> str | None:
    """The Monday of the week the card on home covers, while it's there: not dismissed, and the lab had heard something
    by then (a new lab's first Monday has nothing to say)."""
    m = monday(now)
    week = m - timedelta(days=7)
    if config(conn, "week_dismissed") == week.isoformat():
        return None
    first = row(conn, "SELECT min(coalesce(r.started_at + s.start_s, r.created_at)) t FROM sessions s "
                      "JOIN recordings r ON r.id = s.recording_id WHERE s.status='active'")["t"]
    return week.isoformat() if first is not None and first < _ts(m) else None


def current(conn, now: float | None = None) -> dict | None:
    """Home's card, or None."""
    week = ready(conn, now)
    return card(conn, date.fromisoformat(week), now) if week else None


def announce(conn, now: float | None = None) -> str | None:
    """notify.py's key while the card waits from Monday morning on: "week:<Monday it covers>"."""
    now = time.time() if now is None else now
    if datetime.fromtimestamp(now) < datetime.combine(monday(now), datetime.min.time()) + timedelta(hours=MORNING_H):
        return None
    week = ready(conn, now)
    return f"week:{week}" if week else None


def dismiss(conn, week: str) -> None:
    _set(conn, "week_dismissed", date.fromisoformat(week).isoformat())
