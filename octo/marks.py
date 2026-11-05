"""B16: mark a moment (docs/backlog.md, 19 Oct). "This mattered", said while it happens: a double tap on the
recorder (rec-26 writes `{"button":"mark","at_ms":…}` to its manifest) or the phone's Mark button (the Mac's clock
places it, as it answers). You mark after it lands, so the moment is just before the mark.

- A mark is a time, nothing else. It lands in whichever conversation was going on then, read at read time, so a
  split, a join or a new find puts it where it now belongs. One a little after the last words (GRACE_S) still counts,
  unless another conversation in the recording had started.
- The review is told "the owner marked about 14:32" and reads the minute before it closely: planning.insight_input
  adds `marks` only when there are some, so every conversation without one keeps its input and hash.
- Deleting the conversation, or the part of it the mark is in, deletes the mark (privacy.py). A recorder's mark keeps
  its row with no time, so the manifest's line isn't read again at the next sync.
"""

from __future__ import annotations

import json
import time

from .db import new_id, row, rows

GRACE_S = 15.0  # a mark just after the conversation's last words is still about it
UNDO_S = 60.0  # the phone's Undo (22 Oct): a mark it just made, and only that


def wall_clock(clock: tuple[float, float] | None, start_ms: float | None) -> float | None:
    """Seconds since 1970 for a moment `start_ms` after boot, if the Mac set the clock in that boot."""
    if not clock or start_ms is None:
        return None
    return (clock[0] - clock[1] + start_ms) / 1000  # here since 5 Nov, so marks and fillers needn't import device (risk 8)


def add(conn, at: float | None = None, source: str = "phone") -> dict:
    """A mark from the phone: now, by the Mac's clock."""
    m = {"id": new_id("mark"), "at": time.time() if at is None else at, "source": source}
    conn.execute("INSERT INTO marks (id, at, source, created_at) VALUES (?, ?, ?, ?)", (m["id"], m["at"], source, time.time()))
    return m


def undo(conn, mark_id: str, now: float | None = None) -> bool:
    """Take back a mark the phone made in the last UNDO_S, as if it never was. Any other mark (the recorder's, or one
    older) stays: an undo is for a tap just made, not for editing what the review was told."""
    now = time.time() if now is None else now
    return conn.execute("DELETE FROM marks WHERE id=? AND source='phone' AND created_at >= ?", (mark_id, now - UNDO_S)).rowcount > 0


def from_manifest(conn, boot: int, text: str) -> int:
    """The recorder's marks in one boot's manifest, placed by that boot's clock as its chunks are. Those already read
    (or deleted) add nothing; one before the Mac set the boot's clock waits for a later sync. How many were new."""
    clock, found = None, []
    for line in text.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if "clock_ms" in d:
            clock = (d["clock_ms"], d["at_ms"])  # the latest one is the best, as for chunks
        elif d.get("button") == "mark" and isinstance(d.get("at_ms"), int):
            found.append(d["at_ms"])
    n = 0
    for at_ms in found:
        at = wall_clock(clock, at_ms)
        if at is not None:
            n += conn.execute("INSERT OR IGNORE INTO marks (id, at, source, boot, at_ms, created_at) VALUES (?, ?, 'recorder', ?, ?, ?)",
                              (new_id("mark"), at, boot, at_ms, time.time())).rowcount
    return n


def _span(conn, session_id: str) -> dict | None:
    return row(conn, "SELECT s.recording_id, r.started_at + s.start_s AS a, r.started_at + s.end_s AS b FROM sessions s "
                     "JOIN recordings r ON r.id = s.recording_id WHERE s.id=? AND r.started_at IS NOT NULL", session_id)


def for_session(conn, session_id: str) -> list[dict]:
    """The conversation's marks, each `at` in seconds from its start (as its lines), oldest first."""
    sp = _span(conn, session_id)
    if not sp:
        return []
    got = rows(conn, "SELECT m.id, m.at, m.source FROM marks m WHERE m.at BETWEEN ? AND ? AND NOT EXISTS ("
                     "SELECT 1 FROM sessions o JOIN recordings r ON r.id = o.recording_id WHERE o.recording_id=? AND o.id<>? "
                     "AND o.status='active' AND m.at BETWEEN r.started_at + o.start_s AND r.started_at + o.end_s) ORDER BY m.at",
               sp["a"], sp["b"] + GRACE_S, sp["recording_id"], session_id)
    return [{**m, "at": round(min(m["at"], sp["b"]) - sp["a"], 2)} for m in got]


def on_lines(marks: list[dict], lines: list[dict]) -> list[dict]:
    """Each mark with the line it followed: the one under the moment just before it, else the last to start before it."""
    out = []
    for m in marks:
        t = m["at"] - 0.5
        under = [ln for ln in lines if ln["start"] <= t <= ln["end"] + 0.5]
        before = [ln for ln in lines if ln["start"] <= t]
        pick = under[-1] if under else before[-1] if before else None
        out.append({**m, "line": pick["id"] if pick else None})
    return out


def review_input(conn, session_id: str) -> list[int]:
    """What the review reads: whole seconds into the conversation. [] for none, so its input is what it was before B16."""
    return [int(m["at"]) for m in for_session(conn, session_id)]


def delete_for(conn, session_id: str, start_s: float | None = None, end_s: float | None = None) -> None:
    """The conversation's marks, or those in a part of it (seconds from its start), go with it."""
    for m in for_session(conn, session_id):
        if start_s is None or start_s <= m["at"] <= end_s:
            conn.execute("DELETE FROM marks WHERE id=? AND boot IS NULL", (m["id"],))
            conn.execute("UPDATE marks SET at=NULL WHERE id=?", (m["id"],))  # the recorder's: no time, not read again
