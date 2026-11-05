"""The focus: one thing the owner is practising, and whether it came up in each conversation after they chose it.

Plan C2 (docs/coaching-plan.md). One focus is active at a time; choosing another ends the current one.
It usually comes from an item in a review (a "What to do" task, a conduct finding, between the lines),
worded by the owner. Each conversation that happened while it was active asks: did it come up?

- "did": they did it. The item it came from is marked acted on, which is what Progress counts as acted on (A3).
  It's still their answer, so changing it back undoes that, unless another conversation also says "did"
  or they had already marked the item acted on themselves (focus.marked_acted says who set it).
- "missed": it came up and they didn't. "na": it didn't come up.

Read-time only: nothing here is a pipeline stage, and nothing re-runs when it changes.
"""

from __future__ import annotations

import time

from .db import new_id, row, rows
# A number a focus can carry (6 Oct): fillers per 100 words, questions and talking over per 10 minutes,
# share of the talk (octo/measures.py). Shown since the focus started against before; his answer decides.
from .measures import MEASURES
from .solved import get as get_solved

ANSWERS = ("did", "missed", "na")
MAX_TEXT = 300


class FocusError(ValueError):
    pass


def active(conn) -> dict | None:
    return row(conn, "SELECT * FROM focus WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1")


def start(conn, text: str, session_id: str | None = None, insight_run: str | None = None, item: str | None = None,
          now: float | None = None, measure: str | None = None, coach_note: str | None = None) -> dict:
    """Make this the focus, ending the one before. From a coach note (octo/coach.py), `coach_note`
    says which; its item is the most recent review item the note cites."""
    text = " ".join((text or "").split())
    if not text:
        raise FocusError("say what you'll practise")
    if len(text) > MAX_TEXT:
        raise FocusError(f"keep it under {MAX_TEXT} characters")
    if (insight_run is None) != (item is None):
        raise FocusError("an item needs its review")
    if measure is not None and measure not in MEASURES:
        raise FocusError("not a number the lab tracks")
    now = now or time.time()
    conn.execute("UPDATE focus SET ended_at=? WHERE ended_at IS NULL", (now,))
    fid = new_id("foc")
    conn.execute("INSERT INTO focus (id, text, session_id, insight_run, item, started_at, measure, coach_note) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (fid, text, session_id, insight_run, item, now, measure, coach_note))
    return row(conn, "SELECT * FROM focus WHERE id=?", fid)


def stop(conn, now: float | None = None) -> None:
    conn.execute("UPDATE focus SET ended_at=? WHERE ended_at IS NULL", (now or time.time(),))


def at(conn, when: float | None) -> dict | None:
    """The focus that was active when a conversation happened (None if its time is unknown)."""
    if when is None:
        return None
    return row(conn, "SELECT * FROM focus WHERE started_at <= ? AND (ended_at IS NULL OR ended_at > ?) "
                     "ORDER BY started_at DESC LIMIT 1", when, when)


def _set_acted(conn, f: dict, acted: bool) -> None:
    if not f["insight_run"] or not f["session_id"] or not row(conn, "SELECT id FROM stage_runs WHERE id=?", f["insight_run"]):
        return
    fb = row(conn, "SELECT acted FROM feedback WHERE insight_run=? AND item=?", f["insight_run"], f["item"])
    if acted:
        if fb and fb["acted"]:
            return  # already his answer; leave it his
        conn.execute("INSERT OR IGNORE INTO feedback (insight_run, item, session_id, updated_at) VALUES (?, ?, ?, ?)",
                     (f["insight_run"], f["item"], f["session_id"], time.time()))
        conn.execute("UPDATE feedback SET acted=1, updated_at=? WHERE insight_run=? AND item=?",
                     (time.time(), f["insight_run"], f["item"]))
        conn.execute("UPDATE focus SET marked_acted=1 WHERE id=?", (f["id"],))
    elif f["marked_acted"]:
        conn.execute("UPDATE feedback SET acted=0, updated_at=? WHERE insight_run=? AND item=?",
                     (time.time(), f["insight_run"], f["item"]))
        conn.execute("UPDATE focus SET marked_acted=0 WHERE id=?", (f["id"],))


def check(conn, focus_id: str, session_id: str, answer: str | None) -> None:
    """Record (or with None, clear) whether the focus came up in this conversation."""
    f = row(conn, "SELECT * FROM focus WHERE id=?", focus_id)
    if not f:
        raise FocusError("no such focus")
    if answer is not None and answer not in ANSWERS:
        raise FocusError("unexpected answer")
    had_did = bool(row(conn, "SELECT 1 FROM focus_checks WHERE focus_id=? AND answer='did'", focus_id))
    if answer is None:
        conn.execute("DELETE FROM focus_checks WHERE focus_id=? AND session_id=?", (focus_id, session_id))
    else:
        conn.execute("INSERT OR REPLACE INTO focus_checks (focus_id, session_id, answer, updated_at) VALUES (?, ?, ?, ?)",
                     (focus_id, session_id, answer, time.time()))
    has_did = bool(row(conn, "SELECT 1 FROM focus_checks WHERE focus_id=? AND answer='did'", focus_id))
    if has_did != had_did:
        _set_acted(conn, f, has_did)


def tally(conn, focus_id: str) -> dict[str, int]:
    got = {r["answer"]: r["n"] for r in rows(conn, "SELECT answer, count(*) n FROM focus_checks WHERE focus_id=? GROUP BY 1", focus_id)}
    return {a: got.get(a, 0) for a in ANSWERS}


def view(conn, f: dict | None, numbers: bool = True) -> dict | None:
    if not f:
        return None
    out = {"id": f["id"], "text": f["text"], "session_id": f["session_id"], "item": f["item"],
           "started_at": f["started_at"], "ended_at": f["ended_at"], "tally": tally(conn, f["id"]),
           "measure": f.get("measure"), "numbers": None, "coach_note": f.get("coach_note"),
           "solved": get_solved(conn, focus_id=f["id"])}
    if numbers and f.get("measure") in MEASURES:  # since it started, against before: evidence beside his answer
        from .measures import since_before
        out["numbers"] = since_before(conn, f["measure"], f["started_at"], f["ended_at"])
    return out


def for_session(conn, session_id: str, when: float | None) -> dict | None:
    """What the conversation page shows: the focus active then, and the answer given here."""
    f = at(conn, when)
    if not f or f["session_id"] == session_id:  # the conversation it came from isn't a test of it
        return None
    got = row(conn, "SELECT answer FROM focus_checks WHERE focus_id=? AND session_id=?", f["id"], session_id)
    here = None
    if f.get("measure") in MEASURES:  # this conversation's own number, beside the question
        from .measures import for_session as number
        here = number(conn, f["measure"], session_id)
    return {**view(conn, f), "answer": got["answer"] if got else None, "here": here}


def summary(conn) -> dict:
    """The active focus and the ones before it, newest first."""
    past = rows(conn, "SELECT * FROM focus WHERE ended_at IS NOT NULL ORDER BY started_at DESC LIMIT 10")
    return {"active": view(conn, active(conn)), "past": [view(conn, f, numbers=False) for f in past]}
