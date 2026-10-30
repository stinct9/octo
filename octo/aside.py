"""Recordings that aren't conversations: the TV, music, the owner singing (B5, 13 Oct, docs/backlog.md).

The recorder is always on, so they keep arriving; 13 of 46 conversations were "without you" that day. The owner
marks one and it's set aside: `sessions.status = 'set_aside'` with what it was in `sessions.aside`. Every
list, count, the counts, the coach and the planner read only active conversations, so a set-aside one leaves
the list, isn't processed further and never counts. Its audio goes with other raw audio at 60 days (D5).
Putting it back makes it active again and the planner carries on from where it was.

"It's a conversation" is kept too (`aside = 'conversation'` on an active one), so the lab doesn't ask
about it again. The kinds and those answers are the labels a detector would be measured on later (B5,
"the lab recognises them").

Until then `maybe` is a display-only guess for "Sort them" (part 2): nobody in it is the owner, the owner hasn't
answered for it, and it has few words for its length or is settled without them (the TV talks a lot: on 13
Oct few words alone found 9 of the owner's 46, missing 8 "without you" ones). It never sets anything aside.
"""

from __future__ import annotations

import time

from .db import row, rows, tx

KINDS = ("tv", "music", "other")
CONVERSATION = "conversation"  # The owner said it is one: not asked again
FEW_WORDS_PER_MIN = 20.0  # 13 Oct: 15 of the owner's 46 active conversations were under this
LABEL = {"tv": "TV", "music": "Music", "other": "Something else"}


def mark(conn, session_id: str, kind: str) -> None:
    """Set an active conversation aside as `kind`. Its queued steps are dropped; one already running
    finishes and changes nothing that's shown (the planner and the pages read active conversations only)."""
    if kind not in KINDS:
        raise ValueError(f"not one of {', '.join(KINDS)}")
    with tx(conn):
        n = conn.execute("UPDATE sessions SET status='set_aside', aside=? WHERE id=? AND status IN ('active', 'set_aside')",
                         (kind, session_id)).rowcount
        if not n:
            raise KeyError(session_id)
        conn.execute("UPDATE stage_runs SET status='obsolete', finished_at=? WHERE target=? AND status='queued'",
                     (time.time(), session_id))


def put_back(conn, session_id: str) -> bool:
    """It's a conversation: kept as the owner's answer, and if it was set aside, active again and processed from
    where it stopped (True then)."""
    back = conn.execute("UPDATE sessions SET status='active', aside=? WHERE id=? AND status='set_aside'",
                        (CONVERSATION, session_id)).rowcount
    if not back and not conn.execute("UPDATE sessions SET aside=? WHERE id=? AND status='active'",
                                     (CONVERSATION, session_id)).rowcount:
        raise KeyError(session_id)
    return bool(back)


def unanswer(conn, session_id: str) -> bool:
    """Undo the owner's last answer for it ("Sort them"): active again with no answer, so a mistaken tap leaves
    no label behind. True when it had been set aside (processing carries on)."""
    was = row(conn, "SELECT status FROM sessions WHERE id=? AND status IN ('active', 'set_aside')", session_id)
    if not was:
        raise KeyError(session_id)
    conn.execute("UPDATE sessions SET status='active', aside=NULL WHERE id=?", (session_id,))
    return was["status"] == "set_aside"


def maybe(sess: dict, speakers: list[dict], lines: list[dict], without_jay: bool = False) -> bool:
    """A guess that this active conversation isn't one (for "Sort them"): the owner hasn't answered for it,
    nobody in it is them, and it's settled without them or has under FEW_WORDS_PER_MIN words a minute.
    Display only."""
    if sess.get("aside") or not speakers or any(s["self"] for s in speakers):
        return False
    if without_jay:
        return True
    minutes = max((sess["end_s"] - sess["start_s"]) / 60, 0.5)
    return sum(len(ln["text"].split()) for ln in lines) / minutes < FEW_WORDS_PER_MIN


def listed(conn) -> list[dict]:
    """The set-aside ones, newest first, for the folded group at the end of the list."""
    return rows(conn, "SELECT s.id, s.aside, s.start_s, s.end_s, r.started_at, r.source FROM sessions s "
                      "JOIN recordings r ON r.id = s.recording_id WHERE s.status='set_aside' "
                      "ORDER BY coalesce(r.started_at + s.start_s, r.created_at) DESC")


def count(conn) -> int:
    return row(conn, "SELECT count(*) n FROM sessions WHERE status='set_aside'")["n"]
