"""Batch B of the 4 Oct review (docs/review-2026-10-04.md): the open "What to do" steps on home
with "Did it", conversations without the owner leaving the to-do list, notifications that carry counts
only, and the counts: "new" from current reviews only, and over the last 10 beside the total."""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import device as D
from octo import notify as N
from octo import review as R
from octo import sessions as SV
from octo import ticktick as TT
from octo.api import make_router
from octo.db import connect, row, rows

from test_corrections import DZ

SID, SID2 = "ses_20261001_aaaaaaaa", "ses_20261002_bbbbbbbb"
REVIEW = {"kind": "review", "skipped": None, "reading": [], "between": [], "conduct": [], "beneath": [],
          "next": [{"title": "Send Kiran the dates table", "detail": "d", "why": "w", "due": "2026-10-08"},
                   {"title": "Ask Tara what she needs", "detail": "d", "why": "w", "due": None}]}


def _lab(tmp_path):
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO recordings (id, path, source, started_at, duration_s, created_at) VALUES ('r', 'a.flac', 'phone', 1790863200, 9000, 0)")
    for i, sid in enumerate((SID, SID2)):
        conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, status, created_at) "
                     "VALUES (?, 'r', ?, ?, 60, 'active', 0)", (sid, i * 4000, i * 4000 + 600))
    return conn


def _review(conn, sid, rid, current=1, stale=0, out=REVIEW):
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, stale, output, queued_at) "
                 "VALUES (?, 'insight', ?, 'h', 'done', ?, ?, ?, 0)", (rid, sid, current, stale, json.dumps(out)))


def _client(tmp_path):
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    return TestClient(app)


# ---- M01: "From your reviews" ----------------------------------------------------------------

def test_home_lists_every_open_step_and_did_it_marks_it_acted(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    _review(conn, SID, "run_1")
    _review(conn, SID2, "run_old", current=0)  # replaced: its steps aren't open any more
    _review(conn, SID2, "run_2", stale=1)      # out of date and not rated: being redone, not shown
    TT.record(conn, "run_1", "n1", SID, {"task_id": "t1", "project_id": "inbox", "url": "https://ticktick/t1"})
    c = _client(tmp_path)

    got = c.get("/api/octo/actions").json()
    assert [(t["session_id"], t["item"]) for t in got] == [(SID, "n0"), (SID, "n1")]  # due first
    assert got[0]["title"] == "Send Kiran the dates table" and got[0]["due"] == "2026-10-08" and not got[0]["rated"]
    assert got[1]["ticktick"] == "https://ticktick/t1" and got[0]["ticktick"] is None

    asked = []
    monkeypatch.setattr(TT, "done", lambda sent: asked.append(sent) or {k: True for k in sent})
    assert c.get("/api/octo/actions/ticktick").json() == {"done": {f"{SID}/n1": True}}
    assert list(asked[0]) == [f"{SID}/n1"]  # only sent steps are looked up

    r = c.post(f"/api/octo/sessions/{SID}/feedback", json={"run": "run_1", "item": "n0", "acted": True})
    assert r.status_code == 200 and r.json()["feedback"]["n0"]["acted"]
    assert [t["item"] for t in c.get("/api/octo/actions").json()] == ["n1"]


def test_a_rated_review_kept_after_a_change_still_lists_its_steps(tmp_path):
    conn = _lab(tmp_path)
    _review(conn, SID, "run_1", stale=1)
    conn.execute("INSERT INTO feedback (insight_run, item, session_id, verdict, novelty, updated_at) VALUES ('run_1', 'n1', ?, 'right', 'new', 0)", (SID,))
    assert [(t["item"], t["rated"]) for t in SV.open_tasks(conn)] == [("n0", False), ("n1", True)]


# ---- M18: without the owner --------------------------------------------------------------------------

def _speakers(conn, sid, states, jay_present=None):
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('dz', 'diarize', ?, 'h', 'done', 1, ?, 0)", (sid, json.dumps(DZ)))
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, upstream, output, queued_at) "
                 "VALUES ('id', 'identify', ?, 'h', 'done', 1, '[\"dz\"]', ?, 0)", (sid, json.dumps({"jay_present": jay_present})))
    conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES ('p_jay', 'Sam', 1, 0), ('p_han', 'Kiran', 0, 0)")
    for letter, (state, who) in states.items():
        conn.execute("INSERT INTO session_speakers (session_id, speaker, person_id, state, diarize_run) VALUES (?, ?, ?, ?, 'dz')",
                     (sid, letter, who, state))


def test_everyone_named_and_none_of_them_jay_is_without_you_not_confirm(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    monkeypatch.setattr(SV, "SESSION_STAGES", ["diarize"])
    _speakers(conn, SID, {"A": ("confirmed", "p_han"), "B": ("confirmed", "p_han"), "C": ("confirmed", "p_han")})
    assert SV.todo(conn, SID) == {"kind": "not_you"}
    conn.execute("UPDATE session_speakers SET state='suggested' WHERE speaker='C'")
    assert SV.todo(conn, SID) == {"kind": "confirm"}  # a suggestion still waits for an answer


def test_unknown_voices_count_as_without_you_only_when_none_sounded_like_jay(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    monkeypatch.setattr(SV, "SESSION_STAGES", ["diarize"])
    _speakers(conn, SID, {"A": ("confirmed", "p_han")}, jay_present=None)  # B and C unknown
    assert SV.todo(conn, SID) == {"kind": "confirm"}
    conn.execute("UPDATE stage_runs SET output=? WHERE id='id'", (json.dumps({"jay_present": False}),))
    assert SV.todo(conn, SID) == {"kind": "not_you"}
    conn.execute("UPDATE session_speakers SET person_id='p_jay' WHERE speaker='A'")  # "that one is me"
    assert SV.todo(conn, SID)["kind"] != "not_you"


# ---- M28: notifications ----------------------------------------------------------------------

def test_notifications_carry_counts_only_and_each_thing_once(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    monkeypatch.setattr(N, "STATE", tmp_path / "notify.json")
    todo = {SID: {"kind": "review"}, SID2: {"kind": "failed", "stage": "insight", "auto_retry": False}}
    monkeypatch.setattr(SV, "todo", lambda conn, sid: todo[sid])
    monkeypatch.setattr(D, "load_config", lambda: {"host": "x"})
    seen_at = 1_790_000_000
    monkeypatch.setattr(D, "last_status", lambda: {"seen_at": seen_at, "status": {"want_on": True}})
    sent = []
    now = seen_at + 20 * 3600
    assert N.check(conn, sent.append, now) == "1 conversation ready to review · 1 failed: press Try again · recorder not seen for 20 h"
    assert N.check(conn, sent.append, now + 300) is None and len(sent) == 1  # nothing new: nothing posted
    for text in sent:
        assert "Kiran" not in text and "ses_" not in text  # counts, never names or ids

    todo[SID2] = {"kind": "failed", "stage": "insight", "auto_retry": True}  # will retry by itself: not the owner's to do
    monkeypatch.setattr(D, "last_status", lambda: {"seen_at": now, "status": {"want_on": False}})  # paused by the owner
    assert N.check(conn, sent.append, now + 30 * 3600) is None
    todo[SID2] = {"kind": "confirm"}
    assert N.check(conn, sent.append, now + 30 * 3600) == "1 with speakers to confirm"


# ---- M02: the counts --------------------------------------------------------------------------

def _rated(conn, sid, run, novelty, verdict="right", item="n0", acted=0):
    conn.execute("INSERT INTO feedback (insight_run, item, session_id, verdict, novelty, acted, updated_at) VALUES (?, ?, ?, ?, ?, ?, 0)",
                 (run, item, sid, verdict, novelty, acted))


def test_new_to_me_counts_only_current_reviews_and_items_not_marked_wrong(tmp_path):
    conn = _lab(tmp_path)
    conn.execute("UPDATE sessions SET reviewed_at=1")
    _review(conn, SID, "old", current=0)
    _review(conn, SID, "now")
    _rated(conn, SID, "old", "new")             # on a review since replaced
    _rated(conn, SID, "now", "new", "wrong")    # "new", but the owner said it's wrong
    _rated(conn, SID, "now", "knew", item="n1")
    _review(conn, SID2, "two")
    _rated(conn, SID2, "two", "new", "partly", acted=1)
    g = R.checks(conn)
    assert g["checks"]["new_share"]["value"] == 1  # SID2 only
    # Current reviews' answers, shown as context; an item marked new but wrong isn't news (6 Oct).
    assert g["novelty"] == {"new": 1, "knew": 1, "nothing_new": 0}
    assert g["checks"]["acted"]["value"] == 1


def _reviewed(conn, n, at, acted=0):
    """n more reviewed conversations, each with one item rated new (the first `acted` marked acted)."""
    for i in range(n):
        sid = f"ses_20261010_{i:08x}"
        conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, status, reviewed_at, created_at) "
                     "VALUES (?, 'r', ?, ?, 60, 'active', ?, 0)", (sid, 10000 + i * 700, 10600 + i * 700, at))
        _review(conn, sid, f"run_{sid}")
        _rated(conn, sid, f"run_{sid}", "new", acted=int(i < acted))


def test_new_over_the_last_10_reviewed_sits_beside_the_total(tmp_path):
    """Whether novelty wears off (gate plan, 19 Oct): the total alone would hide it."""
    conn = _lab(tmp_path)
    _reviewed(conn, 12, 1)
    conn.execute("UPDATE sessions SET reviewed_at=rowid WHERE reviewed_at IS NOT NULL")  # reviewed one after another, oldest first
    ids = [r["id"] for r in rows(conn, "SELECT id FROM sessions WHERE reviewed_at IS NOT NULL ORDER BY reviewed_at")]
    conn.execute("UPDATE feedback SET novelty=NULL")
    for sid in (ids[0], ids[1], ids[-1]):  # the two oldest and the newest
        conn.execute("UPDATE feedback SET novelty='new' WHERE session_id=?", (sid,))
    c = R.checks(conn)["checks"]
    assert c["new_share"] == {"value": 3, "of": 12}
    assert c["recent_new"] == {"value": 1, "of": 10}
    assert "gate" not in R.progress(conn, []) and R.progress(conn, [])["counts"]["since"] == "2026-10-01"


# ---- batch E: J9's provisional mark, M09's usage line ----------------------------------------

def test_a_provisional_review_counts_says_so_and_jay_clears_it(tmp_path):
    conn = _lab(tmp_path)
    conn.execute("UPDATE sessions SET reviewed_at=1, provisional='mixed-language transcript' WHERE id=?", (SID,))
    assert R.checks(conn)["checks"]["reviewed"] == {"value": 1, "provisional": 1}
    c = _client(tmp_path)
    assert c.get(f"/api/octo/sessions/{SID}").json()["provisional"] == "mixed-language transcript"
    assert c.post(f"/api/octo/sessions/{SID}/provisional", json={"clear": False}).status_code == 422  # set from the CLI
    assert c.post(f"/api/octo/sessions/{SID}/provisional", json={"clear": True}).status_code == 200
    assert R.checks(conn)["checks"]["reviewed"]["provisional"] == 0


def test_claude_usage_sums_reviews_and_merges_and_names_what_wasnt_recorded(tmp_path):
    import time
    conn = _lab(tmp_path)
    now = time.time()
    for rid, stage, out in (("i1", "insight", {"usage": {"duration_ms": 60000, "input_tokens": 9000, "output_tokens": 1000}}),
                            ("i2", "insight", {"usage": None}),  # reused: no call
                            ("t1", "transcribe", {"merged": {"calls": 3, "usage": {"duration_ms": 30000, "input_tokens": 3000}}}),
                            ("t2", "transcribe", {"merged": {"calls": 2}})):  # before 5 Oct: not recorded
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, output, queued_at, finished_at) "
                     "VALUES (?, ?, ?, 'h', 'done', ?, 0, ?)", (rid, stage, SID, json.dumps(out), now))
    assert SV.claude_usage(conn, now - 60) == {"calls": 4, "minutes": 1.5, "tokens": 13000, "unrecorded_calls": 2}
    assert SV.claude_usage(conn, now + 60)["calls"] == 0
