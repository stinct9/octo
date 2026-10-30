"""B16: mark a moment (octo/marks.py). A mark is a time, placed in whatever conversation was going on; it joins the
review's input only when a conversation has one, so no other review is made again."""

import json
import time

import pytest

from octo import claude_code as CC
from octo import insight as I
from octo import marks as MK
from octo import privacy as PR
from octo import rubric as RB
from octo.db import connect, rows
from octo.pipeline import wanted
from octo.stages import STAGES
from tests.test_commitments import RUBRIC, make
from tests.test_intention import ANSWER, DZ, NAMES, PARAMS
from tests.test_review_any import RUBRIC as PRINCIPLES

PINNED = "244b9216aef4b70a"  # test_commitments: the review's hash for this conversation since before B14


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(RB, "text", lambda: RUBRIC)
    return connect(tmp_path / "m.db")


def start_of(conn, sid):
    return rows(conn, "SELECT r.started_at + s.start_s AS a FROM sessions s JOIN recordings r ON r.id = s.recording_id WHERE s.id=?",
                sid)[0]["a"]


def manifest(*lines):
    return "\n".join(json.dumps(d) for d in lines) + "\n"


def test_the_recorders_marks_are_placed_by_the_boots_clock_and_read_once(conn):
    t0 = 1_800_000_000.0
    text = manifest({"boot": 7, "fw": "rec-26"}, {"button": "mark", "at_ms": 5_000})  # before the Mac set the clock
    assert MK.from_manifest(conn, 7, text) == 0  # waits for a clock
    text += manifest({"clock_ms": int(t0 * 1000) + 60_000, "at_ms": 60_000}, {"button": "recording_on", "at_ms": 61_000},
                     {"button": "mark", "at_ms": 90_000}, {"filler": "you know", "score": 0.97, "at_ms": 91_000})
    text += '{"button":"mark","at_'  # cut off by a power cut
    assert MK.from_manifest(conn, 7, text) == 2
    assert [(r["at"], r["source"]) for r in rows(conn, "SELECT at, source FROM marks ORDER BY at")] == \
        [(t0 + 5, "recorder"), (t0 + 90, "recorder")]
    assert MK.from_manifest(conn, 7, text) == 0  # the next sync reads the same lines: nothing new
    assert MK.from_manifest(conn, 8, manifest({"clock_ms": 1000, "at_ms": 1000}, {"button": "mark", "at_ms": 90_000})) == 1  # another boot


def test_without_a_mark_the_reviews_input_hash_is_what_it_was(conn):
    sid = make(conn)
    MK.add(conn, start_of(conn, sid) - 600)  # ten minutes before it started: another moment
    MK.add(conn, start_of(conn, sid) + 70 + MK.GRACE_S + 1)  # just too long after its end
    params, h = wanted(conn, STAGES["insight"], sid, ["dz_1"])
    assert "marks" not in params and h == PINNED

    MK.add(conn, start_of(conn, sid) + 8.6)
    params, h = wanted(conn, STAGES["insight"], sid, ["dz_1"])
    assert params["marks"] == [8] and h != PINNED


def test_a_mark_lands_in_the_conversation_it_was_in_after_a_split(conn):
    sid = make(conn)
    a = start_of(conn, sid)
    first, gap, second = MK.add(conn, a + 20), MK.add(conn, a + 33), MK.add(conn, a + 44)
    assert [m["id"] for m in MK.for_session(conn, sid)] == [first["id"], gap["id"], second["id"]]
    # Split in two at a pause (30 s to 40 s): the first keeps the mark just after its end, the second its own.
    conn.execute("UPDATE sessions SET status='superseded' WHERE id=?", (sid,))
    rec = rows(conn, "SELECT recording_id FROM sessions WHERE id=?", sid)[0]["recording_id"]
    for new, s, e in (("ses_a", 0.01, 30), ("ses_b", 40, 70)):  # a new find: not the old one's start
        conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES (?, ?, ?, ?, 20, 0)",
                     (new, rec, s, e))
    assert [m["id"] for m in MK.for_session(conn, "ses_a")] == [first["id"], gap["id"]]
    assert [(m["id"], m["at"]) for m in MK.for_session(conn, "ses_b")] == [(second["id"], 4.0)]
    assert [m["at"] for m in MK.for_session(conn, "ses_a")] == [19.99, 29.99]  # just after its end: at its end


def test_a_mark_flags_the_line_it_followed():
    lines = [{"id": 0, "start": 1.0, "end": 5.0}, {"id": 1, "start": 6.0, "end": 9.0}, {"id": 2, "start": 30.0, "end": 31.0}]
    got = MK.on_lines([{"at": 5.2}, {"at": 9.3}, {"at": 20.0}, {"at": 0.2}], lines)
    assert [m["line"] for m in got] == [0, 1, 1, None]  # under it, just after it, the last before it, none yet


def test_deleting_the_conversation_or_the_part_with_the_mark(conn):
    sid = make(conn)
    a = start_of(conn, sid)
    conn.execute("UPDATE recordings SET audio_deleted_at=1")  # nothing to silence in a test
    MK.add(conn, a + 10)
    text = manifest({"clock_ms": int(a * 1000), "at_ms": 0}, {"button": "mark", "at_ms": 50_000})
    MK.from_manifest(conn, 3, text)
    assert len(MK.for_session(conn, sid)) == 2
    PR.delete_session(conn, sid)
    assert rows(conn, "SELECT source, at FROM marks") == [{"source": "recorder", "at": None}]  # no time left
    assert MK.from_manifest(conn, 3, text) == 0 and MK.for_session(conn, sid) == []  # the next sync doesn't bring it back


def test_deleting_a_part_takes_only_the_marks_in_it(conn, monkeypatch):
    monkeypatch.setattr(PR, "_silence", lambda *a: None)
    sid = make(conn)
    a = start_of(conn, sid)
    MK.add(conn, a + 10)
    MK.add(conn, a + 50)
    PR.delete_range(conn, sid, 40, 60)
    assert [m["at"] for m in MK.for_session(conn, sid)] == [10.0]


def test_a_review_the_owner_answered_is_kept(conn):
    from octo import planning as PL
    from octo.pipeline import review_outdated
    sid = make(conn)
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, upstream, params, output, queued_at) "
                 "VALUES ('ins_1', 'insight', ?, ?, 'done', 1, '[\"dz_1\"]', '{}', '{}', 0)", (sid, PINNED))
    conn.execute("INSERT INTO feedback (insight_run, item, session_id, verdict, updated_at) VALUES ('ins_1', 'o', ?, 'right', 0)", (sid,))
    MK.add(conn, start_of(conn, sid) + 8)
    assert PL.pinned_review(conn, sid)["id"] == "ins_1"  # the planner leaves it; the page offers a redo
    assert review_outdated(conn, sid)


# ---- the review reads it ------------------------------------------------------------------------

def test_the_review_is_told_when_and_reads_the_minute_before(monkeypatch):
    sent = {}
    monkeypatch.setattr(CC, "ask", lambda m, e, system, prompt, schema, **k: (sent.update(prompt=prompt, system=system), (ANSWER, {}))[1])
    started = time.mktime((2026, 10, 21, 14, 30, 0, 0, 0, -1))
    I.run(DZ, NAMES, {"A"}, {**PARAMS, "marks": [7], "started_at": started}, rubric_text=PRINCIPLES)
    assert "Sam marked this moment as they talked" in sent["prompt"] and "at about 0:07 (14:30 by the clock)" in sent["prompt"]
    assert "minute before each mark" in sent["prompt"] and "Sam marked" not in sent["system"]

    I.run(DZ, NAMES, {"A"}, {**PARAMS, "marks": [7, 125]}, rubric_text=PRINCIPLES)
    assert "Sam marked these moments" in sent["prompt"] and "at about 0:07, 2:05 in the transcript's times" in sent["prompt"]


def test_without_one_the_prompt_is_as_before(monkeypatch):
    sent = {}
    monkeypatch.setattr(CC, "ask", lambda m, e, system, prompt, schema, **k: (sent.update(prompt=prompt), (ANSWER, {}))[1])
    I.run(DZ, NAMES, {"A"}, PARAMS, rubric_text=PRINCIPLES)
    assert " marked " not in sent["prompt"] and "words:\n\n1." in sent["prompt"]


# ---- the route, the page ------------------------------------------------------------------------

def test_the_mark_button_and_the_page(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from octo.api import make_router
    monkeypatch.setattr(RB, "text", lambda: RUBRIC)
    conn = connect(tmp_path / "t.db")
    sid = make(conn, at=time.time() - 60)  # going on now: 70 s long, started a minute ago
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    c = TestClient(app)
    assert c.post("/api/octo/marks", json={}).json()["mark"]["source"] == "phone"
    marks = c.get(f"/api/octo/sessions/{sid}").json()["marks"]
    assert len(marks) == 1 and 55 <= marks[0]["at"] <= 65 and marks[0]["line"] is not None
    assert c.post("/api/octo/marks", content=b"{}").status_code == 415  # JSON only, as every write


def test_undo_takes_back_only_the_phones_mark_just_made(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from octo.api import make_router
    conn = connect(tmp_path / "u.db")
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "u.db"), processor=type("P", (), {"kick": lambda self: None})()))
    c = TestClient(app)
    m = c.post("/api/octo/marks", json={}).json()["mark"]
    assert c.post(f"/api/octo/marks/{m['id']}/delete", json={}).json() == {"ok": True}
    assert not rows(conn, "SELECT 1 FROM marks")
    assert c.post(f"/api/octo/marks/{m['id']}/delete", json={}).status_code == 409  # gone already
    old = MK.add(conn, source="phone")
    assert not MK.undo(conn, old["id"], now=time.time() + MK.UNDO_S + 1)  # a minute on, it stays
    MK.from_manifest(conn, 3, manifest({"clock_ms": 1_790_000_000_000, "at_ms": 1000}, {"button": "mark", "at_ms": 5000}))
    rec = rows(conn, "SELECT id FROM marks WHERE source='recorder'")[0]["id"]
    assert not MK.undo(conn, rec)  # the recorder's marks aren't the phone's to undo
    assert len(rows(conn, "SELECT 1 FROM marks")) == 2
