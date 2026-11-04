"""B14: commitments both ways (octo/commitments.py). A separate step after the review: the review's own
input hash never moves, so no review is made again."""

import json
import time

import pytest

from octo import rubric as RB
from octo.db import connect
from octo.pipeline import wanted
from octo.stages import STAGES

DZ = {"speakers": ["A", "B"], "talk_s": {"A": 40.0, "B": 30.0},
      "turns": [{"speaker": "A", "start": 0.0, "end": 40.0}, {"speaker": "B", "start": 40.0, "end": 70.0}],
      "lines": [
          {"id": 0, "speaker": "B", "start": 1.0, "end": 5.0, "text": "Can you send me the deck before Friday?", "uncertain": False},
          {"id": 1, "speaker": "A", "start": 6.0, "end": 12.0, "text": "Yes, I'll send you the deck by Thursday.", "uncertain": False},
          {"id": 2, "speaker": "B", "start": 13.0, "end": 20.0, "text": "Great, and I'll review the budget numbers tomorrow.", "uncertain": False},
          {"id": 3, "speaker": "A", "start": 21.0, "end": 25.0, "text": "Thanks, that helps a lot.", "uncertain": False},
      ]}
RUBRIC = "# Rubric\n\n## Principles\n\n1. **Be honest.** Say what is so.\n"


def make(conn, sid="ses_20261019_00000001", at=None, rec="rec_20261019_00000001", dz_id="dz_1"):
    """A conversation with its diarize run done and both speakers confirmed (Sam is the owner)."""
    at = time.time() - 3600 if at is None else at
    conn.execute("INSERT OR IGNORE INTO people (id, name, is_self, created_at) VALUES ('per_sam', 'Sam', 1, 0), "
                 "('per_asha', 'Asha', 0, 0)")
    conn.execute("INSERT INTO recordings (id, source, path, started_at, duration_s, created_at) VALUES (?, 'file', 'x.flac', ?, 70, ?)",
                 (rec, at, at))
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES (?, ?, 0, 70, 70, ?)",
                 (sid, rec, at))
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, upstream, params, output, queued_at, "
                 "finished_at) VALUES (?, 'diarize', ?, 'h', 'done', 1, '[]', '{}', ?, 0, 0)", (dz_id, sid, json.dumps(DZ)))
    for letter, pid in (("A", "per_sam"), ("B", "per_asha")):
        conn.execute("INSERT INTO session_speakers (session_id, speaker, person_id, state, similarity, diarize_run) "
                     "VALUES (?, ?, ?, 'confirmed', 0.9, ?)", (sid, letter, pid, dz_id))
    return sid


def test_the_reviews_input_hash_is_what_it_was_before_commitments(tmp_path, monkeypatch):
    # Pinned on the code before B14 (20 Oct): adding the step, its table and its inputs must not move the
    # review's hash, or every review not pinned by the owner's answers would be made again.
    monkeypatch.setattr(RB, "text", lambda: RUBRIC)
    conn = connect(tmp_path / "h.db")
    sid = make(conn)
    params, h = wanted(conn, STAGES["insight"], sid, ["dz_1"])
    assert params == {"model": "claude-opus-5-5", "effort": "high", "min_jay_speech_s": 20.0, "max_ctx": 32768,
                      "speakers": [{"speaker": "A", "name": "Sam", "self": True}, {"speaker": "B", "name": "Asha", "self": False}],
                      "rubric": params["rubric"], "corrections": "none"}
    assert h == "244b9216aef4b70a"


# ---- the answer, checked in code ---------------------------------------------------------------

from octo import commitments as CM  # noqa: E402
from octo import pipeline as P  # noqa: E402
from octo.db import config, row, rows  # noqa: E402

NAMES = {"A": "Sam", "B": "Asha"}
BY_ID = {ln["id"]: ln for ln in DZ["lines"]}
SPEAKERS = [{"speaker": "A", "name": "Sam", "self": True, "person": "per_sam"},
            {"speaker": "B", "name": "Asha", "self": False, "person": "per_asha"}]
RAW = {"commitments": [
    {"by": "Sam", "to": "Asha", "what": "Send Asha the deck", "quote": {"line": 1, "text": "I'll send you the deck by Thursday"},
     "due": "2026-10-22"},
    {"by": "Asha", "to": "Sam", "what": "Review the budget numbers", "quote": {"line": 2, "text": "I'll review the budget numbers tomorrow"},
     "due": "2026-10-20"},
    # Fabricated: nobody said it.
    {"by": "Asha", "to": "Sam", "what": "Approve the hire", "quote": {"line": 2, "text": "I'll approve the hire"}, "due": ""},
    # Real words, but Asha's, put in Sam's mouth.
    {"by": "Sam", "to": "Asha", "what": "Review the budget", "quote": {"line": 2, "text": "I'll review the budget numbers"}, "due": ""},
    # Nobody here is "Farhan": not between the owner and someone in the conversation.
    {"by": "Sam", "to": "Farhan", "what": "Call Farhan", "quote": {"line": 3, "text": "Thanks"}, "due": ""},
], "came_up": [{"id": "com_20261012_aaaaaaaa", "note": "Asha asked for it again.", "quote": {"line": 0, "text": "send me the deck"}},
               {"id": "com_20261012_bbbbbbbb", "note": "Made up.", "quote": {"line": 0, "text": "send me the deck"}}]}
EARLIER = [{"id": "com_20261012_aaaaaaaa", "when": "Mon 12 Oct", "by": "Sam", "to": "Asha", "what": "Send the deck", "due": None}]


def test_a_fabricated_or_misattributed_quote_drops_the_commitment():
    out = CM.check(RAW, BY_ID, NAMES, {"A"}, EARLIER)
    kept = [(c["by"], c["what"], c["due"], c["by_self"]) for c in out["commitments"]]
    assert kept == [("Sam", "Send Asha the deck", "2026-10-22", True), ("Asha", "Review the budget numbers", "2026-10-20", False)]
    assert [d["reason"] for d in out["dropped"]] == ["its quote isn't in the transcript", "its quote isn't the promiser's own words",
                                                    "not between you and someone here"]
    # Only an id it was given, with a quote that's there: the made-up id is left out.
    assert [(c["id"], c["line"]) for c in out["came_up"]] == [("com_20261012_aaaaaaaa", 0)]


def test_the_step_sends_the_transcript_and_the_open_ones_and_reuses_its_answer():
    asked = []

    def ask(reader, system, prompt, schema):
        asked.append((reader, prompt))
        return RAW, {"duration_ms": 1000}
    params = {"model": "claude-opus-5-5", "effort": "high", "speakers": SPEAKERS, "open": EARLIER}
    out = CM.run(DZ, params, started_at=None, ask=ask)
    assert asked[0][0] == {"kind": "claude-code", "model": "claude-opus-5-5", "effort": "high"}  # tools off: claude_code.ask
    assert "[1] 0:06 Sam: Yes, I'll send you the deck by Thursday." in asked[0][1] and "id com_20261012_aaaaaaaa" in asked[0][1]
    assert len(out["commitments"]) == 2 and out["usage"]
    again = CM.run(DZ, params, memo=out["memo"], ask=lambda *a: pytest.fail("asked again"))
    assert again["reused"] == 1 and again["commitments"] == out["commitments"]


# ---- in the lab ----------------------------------------------------------------------------------

def _review(conn, sid, rid="in_1"):
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, upstream, params, output, queued_at, "
                 "finished_at) VALUES (?, 'insight', ?, 'h', 'done', 1, '[\"dz_1\"]', '{}', '{\"skipped\": null}', 0, ?)",
                 (rid, sid, time.time()))


def _done(conn, sid, rid="cm_1", out=None, params=None, finished=None):
    """A commitments run made current through the pipeline, so its rows are written as after a real one."""
    out = out or {**CM.check(RAW, BY_ID, NAMES, {"A"}, []), "usage": {"duration_ms": 1}}
    params = params or {"model": "claude-opus-5-5", "speakers": SPEAKERS, "open": []}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, upstream, params, output, queued_at, finished_at) "
                 "VALUES (?, 'commitments', ?, 'h', 'done', '[\"dz_1\"]', ?, ?, 0, ?)",
                 (rid, sid, json.dumps(params), json.dumps(out), finished or time.time()))
    P._make_current(conn, row(conn, "SELECT * FROM stage_runs WHERE id=?", rid))


def test_it_waits_for_the_review_and_for_its_history(tmp_path):
    conn = connect(tmp_path / "c.db")
    sid = make(conn)
    assert not CM.ready(conn, sid)  # no review yet
    _review(conn, sid)
    assert CM.ready(conn, sid)
    params, _ = wanted(conn, STAGES["commitments"], sid, ["dz_1"])
    assert params["speakers"] == SPEAKERS and params["open"] == [] and params["model"] == "claude-opus-5-5"
    # From the 14 days before the step came (set once by the migration), never older (19 Oct).
    since = config(conn, "commitments_from")
    assert abs(since - (time.time() - 14 * 86400)) < 60
    old = make(conn, "ses_20260901_00000002", at=since - 3600, rec="rec_20260901_00000002", dz_id="dz_2")
    _review(conn, old, "in_2")
    assert not CM.ready(conn, old) and CM.for_session(conn, old)["status"] == "old"
    # Marked by the owner: like a rated review, not looked for again.
    _done(conn, sid)
    CM.mark(conn, rows(conn, "SELECT id FROM commitments WHERE by_self=1")[0]["id"], "done")
    assert not CM.ready(conn, sid)


def test_its_rows_both_ways_marks_ticktick_and_never_acted(tmp_path):
    from octo import review as R
    conn = connect(tmp_path / "c.db")
    sid = make(conn)
    _review(conn, sid)
    before = R.checks(conn)
    _done(conn, sid)
    got = CM.for_session(conn, sid)
    assert got["status"] == "done" and [(c["mine"], c["by"], c["to"]) for c in got["items"]] == [(True, "Sam", "Asha"), (False, "Asha", "Sam")]
    mine, theirs = got["items"]
    with pytest.raises(ValueError):
        CM.mark(conn, mine["id"], "did")  # They did / They didn't are for theirs
    assert CM.mark(conn, theirs["id"], "did")["state"] == "did"
    assert [c["id"] for c in CM.for_person(conn, "per_asha")] == [mine["id"]]  # open ones only
    sent = []
    got = CM.to_ticktick(conn, mine["id"], add=lambda task, when, s: sent.append(task) or {"task_id": "t1", "project_id": "p", "url": "u"})
    assert got["ticktick"]["url"] == "u" and sent[0]["title"] == "Send Asha the deck" and sent[0]["due"] == "2026-10-22"
    CM.to_ticktick(conn, mine["id"], add=lambda *a: pytest.fail("sent twice"))
    with pytest.raises(ValueError):
        CM.to_ticktick(conn, theirs["id"])
    CM.mark(conn, mine["id"], "done")
    assert R.checks(conn) == before and not rows(conn, "SELECT 1 FROM feedback")  # not "acted" on Progress


def test_a_redone_step_keeps_the_marked_ones_and_replaces_the_rest(tmp_path):
    conn = connect(tmp_path / "c.db")
    sid = make(conn)
    _done(conn, sid)
    theirs = rows(conn, "SELECT id FROM commitments WHERE by_self=0")[0]["id"]
    CM.mark(conn, theirs, "didnt")
    _done(conn, sid, "cm_2")
    assert sorted((r["by_self"], r["state"], r["run_id"]) for r in rows(conn, "SELECT * FROM commitments")) == \
        [(0, "didnt", "cm_1"), (1, None, "cm_2")]


def test_next_time_it_is_given_the_open_ones_with_these_people_and_says_which_came_up(tmp_path):
    conn = connect(tmp_path / "c.db")
    first = make(conn, at=time.time() - 3 * 86400)
    _done(conn, first)
    later = make(conn, "ses_20261019_00000003", rec="rec_20261019_00000003", dz_id="dz_3")
    _review(conn, later, "in_3")
    open_ = CM.inputs(conn, later)["open"]
    assert [o["what"] for o in open_] == ["Send Asha the deck", "Review the budget numbers"]
    assert CM.inputs(conn, first)["open"] == []  # nothing from after it, nor its own
    mine = open_[0]["id"]
    out = {"commitments": [], "dropped": [], "came_up": [{"id": mine, "note": "Asked again.", "line": 0, "quote": "send me the deck",
                                                         "start": 1.0, "end": 5.0}], "usage": None}
    _done(conn, later, "cm_3", out=out, params={"speakers": SPEAKERS, "open": open_})
    came = CM.for_session(conn, later)["came_up"]
    assert [(c["id"], c["note"], c["here"]["start"]) for c in came] == [(mine, "Asked again.", 1.0)]
    # Taken once: marking one elsewhere doesn't send this conversation round again.
    CM.mark(conn, mine, "done")
    assert CM.inputs(conn, later)["open"] == open_


def test_deleting_the_conversation_or_forgetting_the_person_deletes_them(tmp_path):
    from octo import privacy as PR
    from octo import voice as V
    conn = connect(tmp_path / "c.db")
    sid = make(conn)
    _done(conn, sid)
    conn.execute("UPDATE recordings SET audio_deleted_at=1")  # nothing to silence in a test
    PR.delete_session(conn, sid)
    assert not rows(conn, "SELECT 1 FROM commitments")
    other = make(conn, "ses_20261019_00000004", rec="rec_20261019_00000004", dz_id="dz_4")
    _done(conn, other, "cm_4")
    conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES ('per_a2', 'Asha M', 0, 0)")
    conn.execute("UPDATE commitments SET to_person='per_a2' WHERE by_self=1")
    V.rename(conn, "Asha M", "Asha")  # the same person: their commitments go with them
    assert {r["to_person"] for r in rows(conn, "SELECT to_person FROM commitments WHERE by_self=1")} == {"per_asha"}
    assert V.forget(conn, "Asha") and not rows(conn, "SELECT 1 FROM commitments")  # both ways


LIMIT = "You've hit your session limit · resets 10:40am (Asia/Calcutta)"


def test_it_counts_toward_the_daily_calls_and_waits_out_a_usage_limit(tmp_path):
    conn = connect(tmp_path / "c.db")
    sid = make(conn)
    for i in range(P.DAILY_CLAUDE_CALLS - 1):
        _done(conn, sid, f"cm_{i}")
    assert P.claude_calls_since(conn, P._midnight(time.time())) == P.DAILY_CLAUDE_CALLS - 1
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, params, queued_at) VALUES "
                 "('q1', 'commitments', ?, 'q', 'queued', '{\"model\": \"claude-opus-5-5\"}', 1)", (sid,))
    assert P.next_run(conn)["id"] == "q1"
    _done(conn, sid, "cm_last")
    assert P.next_run(conn) is None  # 60 used: it waits for midnight, with every other Claude step
    # A usage-limit failure of this step holds Claude's steps, and isn't counted as an attempt.
    conn2 = connect(tmp_path / "d.db")
    now = time.time()
    conn2.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, error, params, queued_at, finished_at) VALUES "
                  "('f', 'commitments', 's', 'h', 'failed', ?, '{\"model\": \"claude-opus-5-5\"}', ?, ?)", (LIMIT, now, now))
    assert P.claude_blocked_until(conn2, now) and P.held_back(conn2, "commitments", "s", "h", now) \
        and P.due_at(conn2, "commitments", "s", "h") < float("inf")


def test_a_speaker_left_out_of_insights_is_left_out_here_too(tmp_path):
    from octo import corrections as C
    from octo import worker as W
    conn = connect(tmp_path / "c.db")
    sid = make(conn)
    C.add(conn, sid, "dz_1", "exclude", {"letter": "B"})
    run = {"stage": "commitments", "target": sid, "upstream": '["dz_1"]'}
    assert {ln["speaker"] for ln in W.inputs_for(conn, run)["upstream"][0]["lines"]} == {"A"}


def test_the_routes(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from octo.api import make_router
    conn = connect(tmp_path / "t.db")
    sid = make(conn)
    _done(conn, sid)
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    c = TestClient(app)
    got = c.get("/api/octo/commitments", params={"person": "per_asha"}).json()
    assert [i["what"] for i in got["items"]] == ["Send Asha the deck", "Review the budget numbers"] and not got["ticktick_connected"]
    mine = got["items"][0]["id"]
    assert c.post(f"/api/octo/commitments/{mine}", json={"state": "didnt"}).status_code == 422
    assert c.post(f"/api/octo/commitments/{mine}", json={"state": "dropped"}).json()["state"] == "dropped"
    assert c.post(f"/api/octo/commitments/{mine}", json={"state": None}).json()["state"] is None
    assert c.post(f"/api/octo/commitments/{mine}/ticktick", json={}).status_code == 502  # TickTick isn't set up
    assert c.post("/api/octo/commitments/com_x/ticktick", json={}).status_code == 404
    assert c.get("/api/octo/commitments", params={"person": "per_nobody"}).status_code == 404
    page = c.get(f"/api/octo/sessions/{sid}").json()["commitments"]
    assert page["status"] == "done" and len(page["items"]) == 2


def test_the_owners_check_counts_keeps_the_row_and_changes_nothing_else(tmp_path):
    from octo import review as R
    conn = connect(tmp_path / "c.db")
    sid = make(conn)
    _review(conn, sid)
    before = R.checks(conn)
    _done(conn, sid)
    got = CM.to_check(conn)
    assert got["checked"] == 0 and len(got["items"]) == 2 and got["first"] == 10
    first, second = got["items"]
    with pytest.raises(ValueError):
        CM.judge(conn, first["id"], "maybe")
    got = CM.judge(conn, first["id"], "right")
    got = CM.judge(conn, second["id"], "wrong")
    assert got["counts"] == {"right": 1, "partly": 0, "wrong": 1} and got["checked"] == 2
    assert CM.judge(conn, second["id"], None)["counts"]["wrong"] == 0  # taken back
    assert not CM.ready(conn, sid)  # a checked one stays, like a marked one: the step isn't redone over it
    _done(conn, sid, "cm_2")
    assert [r["checked"] for r in rows(conn, "SELECT checked FROM commitments WHERE checked IS NOT NULL")] == ["right"]
    assert CM.get(conn, first["id"])["state"] is None and R.checks(conn) == before  # not a mark, not "acted"
