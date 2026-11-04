"""Delete and Forget reach every table (architecture review 6, 28 Oct): each is wired by hand across many tables, so this
looks in all of them, by name, for what should be gone. A new table that holds a conversation or a person fails here
until it's seeded below and covered by privacy.delete_session or voice.forget."""

import pytest

from octo import privacy as PR
from octo import voice as V
from octo.db import connect, rows

REFS = ("session_id", "person_id", "by_person", "to_person", "target")
# What's left on purpose: the conversation's row, as 'deleted', so finding conversations again can't bring it back.
KEPT_AFTER_DELETE = {"sessions.id"}
# Forget leaves the steps' stored results as they are (who-is-who matches, the commitments step's inputs): security
# review L9, the owner's call whether Forget goes further. The reviews that named them are made again (api.py, /people/forget).
KEPT_AFTER_FORGET = {"stage_runs.params", "stage_runs.output"}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    from tests import demo_lab as DL
    monkeypatch.setattr(DL, "ROOT", tmp_path)
    monkeypatch.setattr(DL, "DEMO", tmp_path / "data" / "demo")
    monkeypatch.setattr(DL, "DB", tmp_path / "data" / "demo" / "octo.db")
    DL.build()
    conn = connect(DL.DB)
    conn.execute("UPDATE recordings SET audio_deleted_at=1")  # nothing to silence: the demo's audio isn't under HOME
    sid, sid2 = [r["id"] for r in rows(conn, "SELECT id FROM sessions WHERE status='active' ORDER BY id")[:2]]
    run = rows(conn, "SELECT id FROM stage_runs WHERE target=? AND stage='insight'", sid)[0]["id"]
    pid = "per_asha"
    # The tables the demo lab leaves empty, one row each.
    for sql, args in (
            ("INSERT INTO voiceprints (id, person_id, embedding, seconds, source, created_at) VALUES ('vp_t', ?, x'00', 1, ?, 0)", (pid, f"label:{sid}:B")),
            ("INSERT INTO question_checks (id, session_id, start_s, line, text, app, verdict, created_at) VALUES ('qc_t', ?, 1, 1, 'q?', 'you', 'you', 0)", (sid,)),
            ("INSERT INTO filler_checks (id, session_id, start_s, end_s, by_ear, by_app, words, phrases, created_at) VALUES ('fc_t', ?, 0, 1, 1, 1, 10, '[]', 0)", (sid,)),
            ("INSERT INTO corrections (id, session_id, diarize_run, kind, data, created_at) VALUES ('co_t', ?, 'run', 'line', '{}', 0)", (sid,)),
            ("INSERT INTO ticktick_tasks (insight_run, item, session_id, task_id, added_at) VALUES (?, 'n0', ?, 'task', 0)", (run, sid)),
            ("INSERT INTO focus_checks (focus_id, session_id, answer, updated_at) VALUES ('foc_demo', ?, 'yes', 0)", (sid,)),
            ("INSERT INTO asks (id, created_at, status, question) VALUES ('ask_t', 0, 'done', 'q')", ()),
            ("INSERT INTO ask_sources (ask_id, session_id) VALUES ('ask_t', ?)", (sid,)),
            ("INSERT INTO invitee_matches (invitee, person_id, created_at) VALUES ('asha@example.com', ?, 0)", (pid,)),
            ("INSERT INTO talks (session_id, created_at, updated_at) VALUES (?, 0, 0)", (sid,)),
            # B36: a plan for Asha that read the first conversation
            ("INSERT INTO preparations (id, person_id, status, created_at) VALUES ('prep_t', ?, 'done', 0)", (pid,)),
            ("INSERT INTO preparation_sources (prep_id, session_id) VALUES ('prep_t', ?)", (sid,)),
            # B35: two parts the owner said aren't one discussion, the row kept (undone), naming them both
            ("INSERT OR IGNORE INTO parts (first_id, second_id, created_at, undone_at) VALUES (?, ?, 0, 1)", (sid, sid2))):
        conn.execute(sql, args)
    return conn


def _tables(conn) -> dict[str, list[str]]:
    return {t["name"]: [c["name"] for c in rows(conn, f"PRAGMA table_info({t['name']})")]
            for t in rows(conn, "SELECT name FROM sqlite_master WHERE type='table'")}


def _holding(conn, ids: list[str]) -> set[str]:
    """Every table.column whose text names any of these ids."""
    found = set()
    for t, cols in _tables(conn).items():
        for c in cols:
            if any(conn.execute(f"SELECT 1 FROM {t} WHERE instr(CAST({c} AS TEXT), ?) > 0 LIMIT 1", (i,)).fetchone() for i in ids):
                found.add(f"{t}.{c}")
    return found


def test_deleting_every_conversation_and_forgetting_everyone_leaves_nothing_of_them(lab):
    conn = lab
    empty = [t for t, cols in _tables(conn).items() if set(cols) & set(REFS)
             and not conn.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone()]
    assert empty == [], "seed a row in these in the fixture, so the test can see them"

    people = rows(conn, "SELECT id, name FROM people WHERE is_self=0")
    for p in people:
        V.forget(conn, p["name"])
    assert _holding(conn, [p["id"] for p in people]) - KEPT_AFTER_FORGET == set()

    sessions = [r["id"] for r in rows(conn, "SELECT id FROM sessions WHERE status='active'")]
    for sid in sessions:
        PR.delete_session(conn, sid)
    assert _holding(conn, sessions) - KEPT_AFTER_DELETE == set()
