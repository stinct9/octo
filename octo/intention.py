"""B15: an intention before, a reflection after (docs/backlog.md, 19 Oct). The owner's own words, no model call here.

- **The intention** hangs on a person (the recorder is always on, so there's no moment "before a conversation"):
  "Next time with Asha, I want to…", one open line per person, on their page. The next conversation where they're
  confirmed takes it, once its review is about to be made: the review reads it (planning.insight_input) and says
  whether it happened. Taken, it's kept with that conversation, and the person's box is empty again.
- **Only a conversation that started after it was written takes it**, and never one whose review the owner has
  answered (planning.pinned_review): an intention set after a review was made doesn't redo it. A conversation with
  no intention has exactly the input it had before B15.
- **The reflection**: "How did that go, in your words?" on the conversation page. The review never waits for it and
  never reads it; the coach does (octo/coach.py).

Deleting the conversation deletes both (privacy.delete_session); forgetting the person deletes their intentions
(voice.forget).
"""

from __future__ import annotations

import time

from .db import new_id, row, rows

MAX_INTENTION = 300  # one line
MAX_REFLECTION = 4000


def _clean(text: str | None, most: int) -> str:
    text = " ".join((text or "").split()) if most == MAX_INTENTION else (text or "").strip()
    if len(text) > most:
        raise ValueError(f"at most {most} characters")
    return text


# ---- the intention ------------------------------------------------------------------------------

def open_for(conn, person_id: str) -> dict | None:
    return row(conn, "SELECT id, text, created_at FROM intentions WHERE person_id=? AND session_id IS NULL", person_id)


def last_taken(conn, person_id: str) -> dict | None:
    return row(conn, "SELECT id, text, created_at, session_id, taken_at FROM intentions WHERE person_id=? AND session_id IS NOT NULL "
                     "ORDER BY taken_at DESC LIMIT 1", person_id)


def set_open(conn, person_id: str, text: str | None) -> dict | None:
    """Write, change or clear (empty) the person's open intention. A taken one is never changed: its review read it."""
    text = _clean(text, MAX_INTENTION)
    cur = open_for(conn, person_id)
    if not text:
        conn.execute("DELETE FROM intentions WHERE person_id=? AND session_id IS NULL", (person_id,))
    elif cur:
        conn.execute("UPDATE intentions SET text=? WHERE id=?", (text, cur["id"]))
    else:
        conn.execute("INSERT INTO intentions (id, person_id, text, created_at) VALUES (?, ?, ?, ?)",
                     (new_id("int"), person_id, text, time.time()))
    return open_for(conn, person_id)


def _here(conn, session_id: str) -> set[str]:
    """The people (not the owner) confirmed in the conversation's current speakers."""
    return {r["person_id"] for r in rows(
        conn, "SELECT DISTINCT ss.person_id FROM session_speakers ss JOIN stage_runs dz ON dz.id = ss.diarize_run AND dz.is_current=1 "
              "JOIN people p ON p.id = ss.person_id WHERE ss.session_id=? AND ss.state IN ('confirmed', 'auto') AND p.is_self=0", session_id)}


def take(conn, session_id: str) -> list[dict]:
    """What the review of this conversation reads: the intentions it took, taking any open one now if it may.
    [] for a conversation without one, so its input is what it was before B15."""
    from .planning import MERGE_MIN_JAY_S, jay_talk_s, pinned_review, settled_speakers
    settled = settled_speakers(conn, session_id) is not None
    here = _here(conn, session_id) if settled else set()
    if settled and not pinned_review(conn, session_id):
        if here and jay_talk_s(conn, session_id) >= MERGE_MIN_JAY_S:  # a review that will be made, not skipped as too short
            at = row(conn, "SELECT coalesce(r.started_at + s.start_s, r.created_at) AS at FROM sessions s "
                           "JOIN recordings r ON r.id = s.recording_id WHERE s.id=?", session_id)["at"]
            marks = ",".join("?" * len(here))
            conn.execute(f"UPDATE intentions SET session_id=?, taken_at=? WHERE session_id IS NULL AND created_at <= ? "
                         f"AND person_id IN ({marks})", (session_id, time.time(), at, *sorted(here)))
        # Someone the owner since said isn't here: their intention goes back to waiting, unless they've written a new one.
        for b in rows(conn, "SELECT id, person_id FROM intentions WHERE session_id=?", session_id):
            if b["person_id"] not in here and not open_for(conn, b["person_id"]):
                conn.execute("UPDATE intentions SET session_id=NULL, taken_at=NULL WHERE id=?", (b["id"],))
    got = for_session(conn, session_id)
    return [{"person": i["name"], "text": i["text"]} for i in got if not settled or i["person_id"] in here]


def for_session(conn, session_id: str) -> list[dict]:
    return rows(conn, "SELECT i.id, i.person_id, p.name, i.text, i.created_at, i.taken_at FROM intentions i "
                      "JOIN people p ON p.id = i.person_id WHERE i.session_id=? ORDER BY i.taken_at, i.id", session_id)


def merged(conn, gone: str, into: str) -> None:
    """Two people were one (voice.rename): theirs move over; of two open ones, the one kept is the survivor's."""
    if open_for(conn, into):
        conn.execute("DELETE FROM intentions WHERE person_id=? AND session_id IS NULL", (gone,))
    conn.execute("UPDATE intentions SET person_id=? WHERE person_id=?", (into, gone))


# ---- the reflection -----------------------------------------------------------------------------

def reflection(conn, session_id: str) -> dict | None:
    return row(conn, "SELECT text, updated_at FROM reflections WHERE session_id=?", session_id)


def reflect(conn, session_id: str, text: str | None) -> dict | None:
    """Write, change or clear (empty) the owner's words on how the conversation went."""
    text = _clean(text, MAX_REFLECTION)
    if not text:
        conn.execute("DELETE FROM reflections WHERE session_id=?", (session_id,))
    else:
        conn.execute("INSERT INTO reflections (session_id, text, updated_at) VALUES (?, ?, ?) ON CONFLICT (session_id) "
                     "DO UPDATE SET text=excluded.text, updated_at=excluded.updated_at", (session_id, text, time.time()))
    return reflection(conn, session_id)
