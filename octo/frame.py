"""One request's reads across every conversation, made once (architecture review 5 Nov, risk 5).

The list and Progress work out each conversation's state, and each used to ask the database ~40 times for it: its
current runs, whether a step waits, its last failure, its corrections, the parts it's one of (3,907 queries for 98
conversations). Inside `reading(conn)` those come from a few set-based queries, made the first time they're needed and
kept for the request only: a GET writes nothing, so what it reads can't change under it. Outside, every function reads
as before. The pages show the same either way (tests/test_kept_records.py)."""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

from .db import row, rows

_FRAME: ContextVar[dict | None] = ContextVar("frame", default=None)


@contextmanager
def reading(conn) -> Iterator[None]:
    if get(conn) is not None:
        yield
        return
    token = _FRAME.set({"conn": conn})
    try:
        yield
    finally:
        _FRAME.reset(token)


def get(conn) -> dict | None:
    f = _FRAME.get()
    return f if f is not None and f["conn"] is conn else None


def once(conn, key: str, load: Callable[[], Any]) -> Any:
    """`load()`, made once per request inside `reading`; None outside it (the caller reads its own way)."""
    f = get(conn)
    if f is None:
        return None
    if key not in f:
        f[key] = load()
    return f[key]


def lab(conn) -> str:
    """Which database: another lab (a test's, the demo's) has its own runs under the same ids. Asked once per request."""
    return once(conn, "lab", lambda: _lab(conn)) or _lab(conn)


def _lab(conn) -> str:
    return row(conn, "PRAGMA database_list")["file"]


def current(conn, no_output: str) -> dict[tuple[str, str], dict] | None:
    """Every current run, without its output: (target, stage) → as pipeline.current(output=False) reads one."""
    return once(conn, "current", lambda: {(r["target"], r["stage"]): r for r in rows(
        conn, f"SELECT {no_output}, output IS NOT NULL AS has_output FROM stage_runs WHERE is_current=1")})


def pending(conn) -> set[tuple[str, str]] | None:
    """(target, stage) of every step queued or running."""
    return once(conn, "pending", lambda: {(r["target"], r["stage"]) for r in rows(
        conn, "SELECT target, stage FROM stage_runs WHERE status IN ('queued','running')")})


def failed_last(conn) -> dict[tuple[str, str], dict] | None:
    """(target, stage) → its latest attempt, where that one failed (a failed run is never current)."""
    return once(conn, "failed_last", lambda: {(r["target"], r["stage"]): r for r in rows(
        conn, "SELECT target, stage, status, error, input_hash, finished_at FROM stage_runs f WHERE status='failed' "
              "AND NOT EXISTS (SELECT 1 FROM stage_runs n WHERE n.target=f.target AND n.stage=f.stage "
              "AND n.queued_at > f.queued_at)")})


def corrections(conn) -> dict[tuple[str, str], list[dict]] | None:
    """(session, diarize run) → its corrections rows, in order."""
    def load() -> dict:
        out: dict = defaultdict(list)
        for r in rows(conn, "SELECT * FROM corrections ORDER BY created_at, rowid"):
            out[(r["session_id"], r["diarize_run"])].append(r)
        return out
    return once(conn, "corrections", load)


def set_aside(conn) -> dict[str, str] | None:
    """Conversations set aside as not conversations (B5) → what the owner said they were."""
    return once(conn, "set_aside", lambda: {r["id"]: r["aside"] for r in rows(
        conn, "SELECT id, aside FROM sessions WHERE status='set_aside'")})


def session_speakers(conn) -> dict[str, list[dict]] | None:
    """Conversation → who each of its speakers is (session_speakers with the person, LEFT JOIN as the pages read it)."""
    def load() -> dict:
        out: dict = defaultdict(list)
        for r in rows(conn, "SELECT ss.session_id, ss.speaker, ss.state, ss.diarize_run, p.id AS person_id, p.name, p.is_self "
                            "FROM session_speakers ss LEFT JOIN people p ON p.id = ss.person_id"):
            out[r["session_id"]].append(r)
        return out
    return once(conn, "session_speakers", load)
