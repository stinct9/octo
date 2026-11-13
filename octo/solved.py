"""Who has solved this (B38, goal 2; 5 Nov): on the focus or a coach note's *work on*, the owner notes who has
solved it (a person, a book, a talk), what they did or would say, and where from, in their own words.

- The owner writes every word. Octo never suggests a person, a book or a quote, and nothing is fetched: the coach and
  Talk it through read only what the owner wrote, as the owner's account of them, never as what they actually think.
- Deleted with what it's on (focus or coach note, by cascade); in the database, so exported. Forget doesn't reach it:
  it's the owner's own learning, not something recorded of someone (docs/backlog.md B38).
- The review stage never reads it, so no review's input moves.
"""

from __future__ import annotations

import time

from .db import load_json, row

MAX_WHO, MAX_WHAT, MAX_SOURCE = 120, 1000, 200


class SolvedError(ValueError):
    pass


def _clean(text: str | None, most: int, what: str) -> str:
    text = " ".join((text or "").split())
    if len(text) > most:
        raise SolvedError(f"keep {what} under {most} characters")
    return text


def get(conn, *, focus_id: str | None = None, note_id: str | None = None) -> dict | None:
    r = row(conn, "SELECT who, what, source, updated_at FROM solved WHERE focus_id IS ? AND note_id IS ?", focus_id, note_id)
    return dict(r) if r else None


def put(conn, who: str | None, what: str | None, source: str | None = None, *,
        focus_id: str | None = None, note_id: str | None = None) -> dict | None:
    """Keep the owner's note on this focus or note; who and what both empty clears it."""
    if (focus_id is None) == (note_id is None):
        raise SolvedError("on a focus or a coach note")
    who, what = _clean(who, MAX_WHO, "who"), _clean(what, MAX_WHAT, "what they did")
    source = _clean(source, MAX_SOURCE, "where it's from") or None
    conn.execute("DELETE FROM solved WHERE focus_id IS ? AND note_id IS ?", (focus_id, note_id))
    if not who and not what:
        return None
    if not who or not what:
        raise SolvedError("say who, and what they did or would say")
    conn.execute("INSERT INTO solved (focus_id, note_id, who, what, source, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (focus_id, note_id, who, what, source, time.time()))
    return get(conn, focus_id=focus_id, note_id=note_id)


def for_coach(conn) -> dict | None:
    """What the coach's next note reads: the active focus's, else the latest note's *work on*'s, with what it's on."""
    f = row(conn, "SELECT id, text FROM focus WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1")
    if f and (s := get(conn, focus_id=f["id"])):
        return {**s, "on": "focus", "about": f["text"]}
    n = row(conn, "SELECT id, output FROM coach_notes WHERE status='done' ORDER BY created_at DESC LIMIT 1")
    theme = ((load_json(n["output"]) or {}).get("work_on") or {}).get("theme") if n else None
    if theme and (s := get(conn, note_id=n["id"])):
        return {**s, "on": "work_on", "about": theme}
    return None


def text(s: dict, owner: str) -> str:
    """The note as a prompt gives it: the owner's words, quoted."""
    out = f"{s['who']}, in {owner}'s words: \"{s['what']}\""
    return out + (f" (from {s['source']})" if s.get("source") else "")
