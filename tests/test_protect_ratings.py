"""Batch A of the 4 Oct review (docs/review-2026-10-04.md): the owner's ratings stay with the review they
rated, Claude's usage limit doesn't use up retries, reviews don't wait behind transcription, their
speaker answers survive a re-run, and a speaker they left out of insights stays out."""

import json
import time
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

from octo import corrections as C
from octo import pipeline as P
from octo import planning as PL
from octo import sessions as SV
from octo.db import connect, row, rows
from octo.stages import Stage

from test_corrections import DZ
from test_octo import CALLS, lab  # noqa: F401  (the fixture)

IST = ZoneInfo("Asia/Calcutta")
LIMIT = "You've hit your session limit · resets 10:40am (Asia/Calcutta)"


def ist(h, m=0, day=4):
    return datetime(2026, 10, day, h, m, tzinfo=IST).timestamp()


# ---- M08: Claude's usage limit ---------------------------------------------------------------

def test_the_reset_time_is_read_from_claudes_message():
    assert P.limit_reset(LIMIT, ist(5, 56)) == ist(10, 41)  # a minute's margin
    assert P.limit_reset(LIMIT, ist(11, 0)) == ist(10, 41, day=5)  # already past: tomorrow's
    assert P.limit_reset("You've hit your session limit · resets 3:40pm (Asia/Calcutta)", ist(11)) == ist(15, 41)
    assert P.limit_reset("Claude AI usage limit reached. You've hit your usage limit", ist(9)) == ist(10)  # no time: an hour
    assert P.limit_reset("TimeoutError: timed out", ist(9)) is None


def _failed(conn, rid, stage, target, finished, error=LIMIT, h="h", params="{}"):
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, error, params, queued_at, finished_at) "
                 "VALUES (?, ?, ?, ?, 'failed', ?, ?, ?, ?)", (rid, stage, target, h, error, params, finished, finished))


def test_a_usage_limit_is_not_an_attempt_and_holds_claude_steps_until_it_resets(tmp_path):
    conn = connect(tmp_path / "q.db")
    for i, at in enumerate((ist(5, 56), ist(6, 59), ist(8, 2))):  # 4 Oct: three in a row, the hourly passes
        _failed(conn, f"f{i}", "insight", "s1", at)
    assert P.held_back(conn, "insight", "s1", "h", now=ist(9))
    assert not P.held_back(conn, "insight", "s1", "h", now=ist(10, 42))  # not given up: tried again
    assert P.next_due(conn, now=ist(9)) == ist(10, 41)  # the server wakes then, not at its next hourly pass
    # Ordinary failures still count, and still give up after three.
    for i, at in enumerate((ist(1), ist(2), ist(3))):
        _failed(conn, f"t{i}", "insight", "s2", at, error="TimeoutError: timed out")
    assert P.due_at(conn, "insight", "s2", "h") == float("inf")

    q = lambda rid, stage, params: conn.execute(
        "INSERT INTO stage_runs (id, stage, target, input_hash, status, params, queued_at) VALUES (?, ?, 'x', 'q', 'queued', ?, 1)",
        (rid, stage, json.dumps(params)))
    q("review", "insight", {"model": "claude-opus-5-5"})
    q("hindi", "transcribe", {"hindi": {"base_model": "q"}})
    q("voices", "identify", {})
    assert P.next_run(conn, now=ist(9))["id"] == "voices"  # Claude's steps wait; the rest carry on
    assert P.next_run(conn, now=ist(10, 42))["id"] == "review"  # and then the review goes first


def test_the_list_says_why_and_that_it_will_try_again(lab):  # noqa: F811
    conn = lab["conn"]
    P.add_recording(conn, lab["wav"], "bench")
    lab["run"]()
    sid = rows(conn, "SELECT id FROM sessions ORDER BY start_s")[0]["id"]
    _failed(conn, "f1", "transcribe", sid, time.time())  # just now: the next 10:40 is still to come
    t = SV.todo(conn, sid)
    assert t["kind"] == "failed" and t["auto_retry"] and t["hint"].startswith("Claude's usage limit was reached · tries again after")



def test_the_weekly_limit_is_a_limit_and_its_date_is_read():
    weekly = "Claude Code: You've hit your weekly limit · resets Nov 3 at 2:30am (Asia/Calcutta)"  # 31 Oct
    assert P.limit_reset(weekly, ist(16, 34, day=31)) == datetime(2026, 11, 3, 2, 31, tzinfo=IST).timestamp()
    assert P.limit_reset("You've hit your weekly limit · resets Jan 2 at 9am (Asia/Calcutta)", ist(9, day=31)) \
        == datetime(2027, 1, 2, 9, 1, tzinfo=IST).timestamp()  # past this year's: next year's
    assert P.limit_reset("You've hit your weekly limit · resets Feb 31 at 9am", ist(9)) == ist(10)  # no such day: an hour


# ---- 4 Nov: Claude Code signed out on the Mac --------------------------------------------------

SIGNED_OUT = ("RuntimeError: Claude Code: Failed to authenticate: OAuth session expired and could not be refreshed "
              "-- run `claude` in a terminal and log in with /login")  # as 3 Nov's were kept


def test_signed_out_is_not_an_attempt_and_holds_claude_steps_until_signed_in(tmp_path, monkeypatch):
    conn = connect(tmp_path / "q.db")
    signed = {"in": False}
    monkeypatch.setattr(P, "signed_in", lambda now=None: signed["in"])
    claude = json.dumps({"model": "claude-opus-5-5"})
    for i, at in enumerate((ist(1), ist(2), ist(3), ist(4))):
        _failed(conn, f"f{i}", "insight", "s1", at, error=SIGNED_OUT, params=claude)
    assert P.due_at(conn, "insight", "s1", "h") < float("inf")  # never given up: Try again couldn't fix it
    assert P.claude_signed_out(conn, ist(5)) == ist(4)
    assert P.claude_waits(conn, ist(5)) == P.SIGN_IN
    assert P.next_due(conn, now=ist(5)) == ist(5) + P.SIGN_IN_CHECK_S  # the server looks again soon
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, params, queued_at) "
                 "VALUES ('review', 'insight', 's2', 'q', 'queued', ?, 1)", (claude,))
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, params, queued_at) "
                 "VALUES ('voices', 'identify', 's2', 'q', 'queued', '{}', 1)")
    assert P.next_run(conn, now=ist(5))["id"] == "voices"  # no new failures piling up: it waits, the rest carry on
    signed["in"] = True
    assert P.claude_signed_out(conn, ist(5)) is None and P.claude_waits(conn, ist(5)) is None
    assert P.next_run(conn, now=ist(5))["id"] == "review"  # signed in: it goes by itself
    # A local review failing tells nothing about Claude Code; a later Claude review that worked ends it too.
    signed["in"] = False
    conn.execute("UPDATE stage_runs SET status='done', finished_at=? WHERE id='review'", (ist(6),))
    assert P.claude_signed_out(conn, ist(7)) is None


def test_the_list_says_to_sign_in_and_the_mac_is_told_once(lab):  # noqa: F811
    from octo import notify as N
    conn = lab["conn"]
    lab["mp"].setattr(P, "signed_in", lambda now=None: False)
    P.add_recording(conn, lab["wav"], "bench")
    lab["run"]()
    sid = rows(conn, "SELECT id FROM sessions ORDER BY start_s")[0]["id"]
    _failed(conn, "f1", "insight", sid, time.time(), error=SIGNED_OUT, params=json.dumps({"model": "claude-opus-5-5"}))
    t = SV.todo(conn, sid)
    assert t["kind"] == "failed" and t["auto_retry"] and t["hint"] == P.SIGN_IN
    keys = N.waiting(conn)
    assert any(k.startswith("signin:") for k in keys) and not any(k.startswith("failed:") for k in keys)
    assert N.message(keys).startswith("Claude Code is signed out")
    assert N.menu(keys)[0]["text"] == "Claude Code is signed out: reviews wait"

# ---- M10: a Claude review goes first ---------------------------------------------------------

def test_a_claude_review_runs_before_queued_transcription_but_a_local_one_waits(tmp_path):
    conn = connect(tmp_path / "q.db")
    for rid, stage, params, at in (("tr", "transcribe", {}, 1.0), ("fc", "find_conversations", {}, 3.0),
                                   ("local", "insight", {"model": "qwen3:8b"}, 0.5),
                                   ("claude", "insight", {"model": "claude-opus-5-5"}, 2.0)):
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, params, queued_at) "
                     "VALUES (?, ?, 's', 'h', 'queued', ?, ?)", (rid, stage, json.dumps(params), at))
    order = []
    while (r := P.next_run(conn)):
        order.append(r["id"])
        conn.execute("UPDATE stage_runs SET status='done' WHERE id=?", (r["id"],))
    assert order == ["fc", "claude", "tr", "local"]


# ---- M07: a rated review is kept until the owner asks for a redo ----------------------------------

def fake_review(inputs, params):
    CALLS.append(("ins", str(inputs["start_s"])))
    return {"kind": "review", "model": params["model"], "skipped": None, "summary": "s", "reading": [{"text": "r"}],
            "between": [], "conduct": [], "next": [], "beneath": [], "no_moment": []}


def with_review(lab, names):
    lab["stages"]["insight"] = Stage("insight", 1, "session", "transcribe", {"model": "claude-test"}, fake_review,
                                     dynamic=lambda conn, target: {"speakers": names["now"]})


def test_a_rated_review_is_kept_until_jay_asks_for_a_redo(lab):  # noqa: F811
    names = {"now": "Nisha"}
    with_review(lab, names)
    conn = lab["conn"]
    P.add_recording(conn, lab["wav"], "bench")
    lab["run"]()
    rated, other = [s["id"] for s in rows(conn, "SELECT id FROM sessions ORDER BY start_s")]
    first = P.current(conn, rated, "insight")["id"]
    # The list's state, with this fixture's stages (no speakers step; everyone settled).
    lab["mp"].setattr(SV, "SESSION_STAGES", ["transcribe", "diarize", "insight"])
    lab["mp"].setattr(SV, "settled_speakers", lambda conn, sid: [])
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('dz', 'diarize', ?, 'h', 'done', 1, '{}', 0)", (rated,))
    conn.execute("INSERT INTO feedback (insight_run, item, session_id, verdict, novelty, updated_at) "
                 "VALUES (?, 'r0', ?, 'right', 'new', 0)", (first, rated))
    assert PL.pinned_review(conn, rated) and not P.review_outdated(conn, rated)

    names["now"] = "Asha"  # a rename: every review's input changes
    CALLS.clear()
    lab["run"]()
    assert CALLS == [("ins", "10.0")]  # only the unrated one is redone
    assert P.current(conn, rated, "insight")["id"] == first
    assert P.review_outdated(conn, rated)
    assert SV.todo(conn, rated)["kind"] == "review"  # rated, but never marked reviewed: the question waits
    conn.execute("UPDATE sessions SET reviewed_at=1 WHERE id=?", (rated,))
    assert SV.todo(conn, rated) == {"kind": "reviewed"}  # still shown as reviewed, not "processing"

    conn.execute("UPDATE sessions SET redo_review=? WHERE id=?", (first, rated))  # "Redo the review"
    CALLS.clear()
    lab["run"]()
    assert CALLS == [("ins", "1.0")]
    assert P.current(conn, rated, "insight")["id"] != first and not P.review_outdated(conn, rated)
    assert row(conn, "SELECT count(*) n FROM feedback WHERE insight_run=?", first)["n"] == 1  # the old ratings stay


def test_an_empty_claude_review_is_a_failure_not_a_review(lab):  # noqa: F811
    with_review(lab, {"now": "x"})
    lab["stages"]["insight"] = replace(lab["stages"]["insight"], fn=lambda i, p: {"kind": "review", "skipped": None, "summary": ""})
    conn = lab["conn"]
    P.add_recording(conn, lab["wav"], "bench")
    lab["run"]()
    failed = rows(conn, "SELECT error FROM stage_runs WHERE stage='insight' AND status='failed'")
    assert len(failed) == 2 and failed[0]["error"] == "the review came back empty"
    assert not rows(conn, "SELECT id FROM stage_runs WHERE stage='insight' AND is_current=1")


# ---- M38: identify keeps the owner's answers -------------------------------------------------------

def test_identify_keeps_names_given_while_it_waited_and_carries_not_this_person(tmp_path):
    conn = connect(tmp_path / "i.db")
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('r', 'phone', 'x', 15, 0)")
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('s', 'r', 0, 15, 9, 0)")
    for pid in ("p1", "p2", "p3"):
        conn.execute("INSERT INTO people (id, name, created_at) VALUES (?, ?, 0)", (pid, pid))
    turns = [{"speaker": k, "start": a, "end": b} for k, a, b in (("A", 0, 5), ("B", 5, 10), ("C", 10, 15))]
    for rid in ("dz0", "dz1"):
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, output, queued_at) "
                     "VALUES (?, 'diarize', 's', ?, 'done', ?, 0)", (rid, rid, json.dumps({"turns": turns})))
    put = lambda k, pid, state, dz: conn.execute("INSERT OR REPLACE INTO session_speakers VALUES ('s', ?, ?, ?, NULL, ?)",
                                                 (k, pid, state, dz))
    put("A", "p1", "confirmed", "dz0")
    put("B", None, "rejected", "dz0")
    put("C", "p2", "confirmed", "dz1")  # The owner named C on the new run before identify finished
    match = {"person_id": "p3", "similarity": 0.7}
    out = {"diarize_run": "dz1", "jay_present": True,
           "speakers": [{"speaker": "A", "match": None}, {"speaker": "B", "match": match}, {"speaker": "C", "match": match}]}
    P._apply_identify(conn, {"target": "s", "output": json.dumps(out)})
    got = {r["speaker"]: (r["person_id"], r["state"], r["diarize_run"]) for r in rows(conn, "SELECT * FROM session_speakers")}
    assert got == {"A": ("p1", "confirmed", "dz1"), "B": (None, "rejected", "dz1"), "C": ("p2", "confirmed", "dz1")}


# ---- M16: left out of insights means left out -----------------------------------------------

def test_a_left_out_speaker_stays_out_through_merges_and_undo(tmp_path):
    out_then_merged = [{"kind": "exclude", "letter": "C"}, {"kind": "merge", "from": "C", "into": "B"}]
    assert C.left_out(DZ, out_then_merged) == {"B"}  # the merge doesn't bring C's words back
    assert C.left_out(DZ, [{"kind": "merge", "from": "B", "into": "C"}, {"kind": "exclude", "letter": "C"}]) == {"C"}
    kept = C.without(C.apply(DZ, out_then_merged), {"B"})
    assert kept["speakers"] == ["A"] and set(kept["talk_s"]) == {"A"}
    assert {x["speaker"] for k in ("turns", "overlapping_turns", "lines") for x in kept[k]} == {"A"}
    assert C.without(DZ, set()) is DZ  # nothing left out: exactly what was sent before

    conn = connect(tmp_path / "u.db")
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('r', 'phone', 'x', 9, 0)")
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('s', 'r', 0, 9, 5, 0)")
    C.add(conn, "s", "dz", "merge", {"from": "C", "into": "B"})
    C.add(conn, "s", "dz", "exclude", {"letter": "B"})
    assert C.undo_last(conn, "s", "dz")  # takes back the merge, not the exclusion
    assert [f["kind"] for f in C.for_run(conn, "s", "dz")] == ["exclude"]
    assert not C.undo_last(conn, "s", "dz")


def test_neither_the_words_nor_the_name_of_a_left_out_speaker_reach_claude(monkeypatch):
    from octo import review_any as RA

    sent = []
    ask = lambda reader, system, prompt, schema: (sent.append(system + prompt), ({}, {}))[1]  # noqa: E731
    dz = {**DZ, "turns": [{**t, "end": t["start"] + 30} for t in DZ["turns"]]}  # long enough for a review
    names = {"A": "Sam", "B": "Nisha", "C": "Rohini"}
    RA.run(C.without(dz, {"C"}), names, {"A"}, {"reader": {"kind": "claude-code", "model": "m"}, "min_jay_speech_s": 20.0},
           "# Rubric\n\n## Conduct principles\n\n1. Listen first.\n", ask=ask)
    assert sent and "Nisha" in sent[0] and "Rohini" not in sent[0]


def test_a_providers_outage_or_rate_limit_waits_and_isnt_an_attempt(tmp_path):
    """Review 8 Oct, finding 5: an hour offline, or a provider overloaded, used up every queued review's three
    attempts. Now it waits, 15 min doubling to 4 h (or what the service asked for), and is never given up."""
    from octo.providers import TRANSIENT
    conn = connect(tmp_path / "q.db")
    for i in range(6):  # six in a row, an hour apart
        _failed(conn, f"o{i}", "insight", "s1", ist(1 + i), error=f"Couldn't reach Local (timed out): {TRANSIENT}")
    assert P.due_at(conn, "insight", "s1", "h") == ist(6) + P.TRANSIENT_MAX_S  # never inf: 15, 30, 60, 120, 240, 240 min
    _failed(conn, "r", "insight", "s2", ist(1), error=f"Local's rate limit was reached (it asked for 20000 s): {TRANSIENT}")
    assert P.due_at(conn, "insight", "s2", "h") == ist(1) + 20000
    # Counted failures still count, and the transient ones between them don't add to them.
    for i, err in enumerate(("bad key", f"x: {TRANSIENT}", "bad key", f"x: {TRANSIENT}", "bad key")):
        _failed(conn, f"k{i}", "insight", "s3", ist(1 + i), error=err)
    assert P.due_at(conn, "insight", "s3", "h") == float("inf")
    assert P.next_due(conn, now=ist(7)) == ist(6) + P.TRANSIENT_MAX_S
