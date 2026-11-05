"""One discussion in parts (B35, 30 Oct): grouped at the review, never in the audio. The lab's rule (an hour cut
unless the people clearly change; a pause or other recordings at half shared, guesses counted), the owner's word,
answered parts, one review of the whole, the list and Progress once, delete and Forget."""

import json

import pytest

from octo import parts as PT
from octo import pipeline as P
from octo import planning as PL
from octo import sessions as SV
from octo import split as S
from octo import voice as V
from octo.db import connect, row, rows
from tests.test_split import meeting, turns


def _conv(a, b):
    return {"start": a, "end": b, "speech_s": round(b - a - 20, 1), "speech": [[a + 1, b - 1]]}


def _lab(tmp_path, parts, people=None, state="auto", recordings=None, ids="s"):
    """Conversations [(start, end, spans)] (spans in recording time), each diarized (a line per turn) and identified;
    `people` maps a letter to a person's name (J is the owner), named as `state`. `recordings` puts part n in its own
    recording, starting at that wall-clock time."""
    conn = connect(tmp_path / "p.db")
    who = {"J": V.ensure_person(conn, "Sam", is_self=True)["id"]}
    for letter, name in (people or {}).items():
        who[letter] = V.ensure_person(conn, name)["id"]
    for n, (a, b, spans) in enumerate(parts, 1):
        rec = f"r{n}" if recordings else "r1"
        if not row(conn, "SELECT 1 FROM recordings WHERE id=?", rec):
            conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at, started_at) "
                         "VALUES (?, 'device', 'x', ?, 0, ?)", (rec, b + 10, recordings[n - 1] if recordings else 1000.0))
        sid, dz = f"{ids}{n}", f"dz{n}"
        conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES (?, ?, ?, ?, ?, 0)",
                     (sid, rec, a, b, round(b - a - 20, 1)))
        rel = [(s, x - a, y - a) for s, x, y in spans if a <= x < b]
        ts = turns(*rel)
        out = {"speakers": sorted({s for s, _, _ in rel}), "turns": ts, "talk_s": {},
               "lines": [{"id": i, "speaker": t["speaker"], "start": t["start"], "end": t["end"], "text": f"line {i}",
                          "uncertain": False} for i, t in enumerate(ts)]}
        for s in out["speakers"]:
            out["talk_s"][s] = round(sum(t["end"] - t["start"] for t in ts if t["speaker"] == s), 1)
        for stage, run, output in (("diarize", dz, out), ("identify", f"id{n}", {"speakers": []})):
            conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at, "
                         "upstream, params) VALUES (?, ?, ?, 'h', 'done', 1, ?, 0, '[]', '{}')", (run, stage, sid, json.dumps(output)))
        for letter in out["speakers"]:
            if letter in who:
                conn.execute("INSERT INTO session_speakers VALUES (?, ?, ?, ?, 0.9, ?)",
                             (sid, letter, who[letter], "auto" if letter == "J" else state, dz))
    return conn


def _hour_cut(tmp_path, gap=46.0, second=("B", "J", "D"), **kw):
    """cap_length cut one meeting `gap` s apart; the second part with `second` talking."""
    b0 = 1800.0 + gap
    spans = meeting(["B", "J", "D"], 1, 29.5) + meeting(list(second), b0 + 1, 29.5)
    return _lab(tmp_path, [(0.0, 1800.0, spans), (b0, b0 + 1800.0, spans)],
                {"B": "Bo", "D": "Di", "G": "Gu", "K": "Ka"}, **kw)


def _ids(g):
    return [s["id"] for s in g]


def test_an_hour_cut_is_one_discussion_unless_the_people_clearly_change(tmp_path):
    for d in ("same", "unnamed", "changed"):
        (tmp_path / d).mkdir()
    conn = _hour_cut(tmp_path / "same")
    assert PT.auto(conn, "s2") == ["s1"] and _ids(PT.group(conn, "s1")) == ["s1", "s2"]
    assert row(conn, "SELECT by, kind FROM parts") == {"by": "lab", "kind": "hour"}
    # Nobody named on either side: the lab can't see a change of people, so the hour cut stays one.
    conn = _hour_cut(tmp_path / "unnamed", second=("X", "J", "Y"))
    assert PT.auto(conn, "s1") == ["s2"]
    conn = _hour_cut(tmp_path / "changed", second=("G", "J", "K"))
    m = PT.measure(conn, *PT._active(conn))
    assert (m["ab"], m["ba"], m["whole"], m["talk"]) == (0.0, 0.0, 0.0, True)
    assert PT.auto(conn, "s1") == [] and PT.groups(conn) == []


@pytest.mark.parametrize("state, shared, grouped", [("suggested", ("B", "J", "D"), True),  # the lab's guesses count
                                                     ("auto", ("B", "J", "G"), True),      # half each way
                                                     ("auto", ("B", "G", "K"), False)])    # a third: under half
def test_across_a_pause_half_shared_both_ways_guesses_counted(tmp_path, state, shared, grouped):
    conn = _hour_cut(tmp_path, gap=240.0, second=shared, state=state)
    a, b = PT._active(conn)
    assert PT.kind(conn, a, b) == "pause"
    assert bool(PT.auto(conn, "s2")) is grouped, PT.measure(conn, a, b)


def test_across_recordings_back_to_back_and_not_too_far(tmp_path):
    for d in ("near", "far", "overlap"):
        (tmp_path / d).mkdir()
    spans = meeting(["B", "J"], 1, 9.5)
    for d, start2, grouped in (("near", 1000.0 + 600 + 300, True), ("far", 1000.0 + 600 + 900, False),
                               ("overlap", 1000.0 + 300, False)):
        conn = _lab(tmp_path / d, [(0.0, 600.0, spans), (0.0, 600.0, spans)], {"B": "Bo"}, recordings=[1000.0, start2])
        assert bool(PT.auto(conn, "s1")) is grouped, d
        assert not grouped or PT.kind(conn, *PT._active(conn)) == "recordings"


def test_overlapping_recordings_are_never_offered_as_parts(tmp_path):
    """A phone memo of the meeting the recorder also caught: two takes of one time, not one discussion in two parts
    (the demo lab offered them as "-131 s apart", 5 Nov)."""
    spans = meeting(["B", "J"], 1, 9.5)
    conn = _lab(tmp_path, [(0.0, 600.0, spans), (0.0, 600.0, spans)], {"B": "Bo"}, recordings=[1000.0, 1300.0])
    assert PT.page(conn, row(conn, "SELECT * FROM sessions WHERE id='s1'")) is None
    with pytest.raises(ValueError):
        PT.group_with(conn, "s1", "after")


def test_the_owners_word_wins_and_the_lab_decides_each_boundary_once(tmp_path):
    for d in ("1", "2"):
        (tmp_path / d).mkdir()
    conn = _hour_cut(tmp_path / "1")
    PT.auto(conn, "s1")
    PT.ungroup(conn, "s1", "s2")  # "Not one discussion"
    assert PT.groups(conn) == [] and PT.auto(conn, "s1") == [] and PT.auto(conn, "s2") == []
    assert PT.group_with(conn, "s2", "before") == "s1"  # by hand, the owner may change their mind
    conn = _hour_cut(tmp_path / "2")
    S.add(conn, "r1", 1823.0, by="owner")
    assert PT.auto(conn, "s1") == []  # a split there, the owner's
    conn.execute("UPDATE splits SET undone_at=1")
    assert PT.auto(conn, "s1") == []  # or one that was


def test_a_link_goes_dormant_when_something_comes_between(tmp_path):
    conn = _hour_cut(tmp_path)
    PT.add(conn, "s1", "s2")
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('s9', 'r1', 1810, 1830, 5, 0)")
    assert PT.groups(conn) == []  # split.auto cut a piece off the first part: s1 and s2 aren't neighbours now
    conn.execute("UPDATE sessions SET status='superseded' WHERE id='s9'")
    assert _ids(PT.group(conn, "s2")) == ["s1", "s2"]


def test_one_review_reads_the_parts_in_order_with_the_gap_marked(tmp_path):
    conn = _hour_cut(tmp_path, second=("B", "J", "X"))
    PT.add(conn, "s1", "s2", by="lab", why="hour")
    inp = PL.insight_input(conn, "s1")
    assert [p["id"] for p in inp["parts"]] == ["s1", "s2"] and inp["parts"][1]["offset_s"] == 1846.0
    # Bo is B in both; part 2's unnamed X is a speaker of its own; the owner is J throughout.
    assert inp["parts"][1]["letters"] == {"B": "B", "J": "J", "X": "X2"}
    assert {s["speaker"]: s["name"] for s in inp["speakers"]} == {"B": "Bo", "D": "Di", "J": "Sam", "X2": "Speaker X2"}
    g = PT.group(conn, "s1")
    assert PT.letters(conn, g)[0]  # every part settled, the owner in it
    conn.execute("UPDATE session_speakers SET state='suggested' WHERE session_id='s2' AND speaker='B'")
    assert PT.letters(conn, g) == ([], []) and SV.todo(conn, "s1")["part"] == 2  # a guess in part 2 waits for the owner
    conn.execute("UPDATE session_speakers SET state='auto' WHERE session_id='s2' AND speaker='B'")
    assert [u["id"] for u in P.insight_ups(conn, "s1")] == ["dz1", "dz2"] and P.insight_ups(conn, "s2")[0]["id"] == "dz2"
    assert PT.held(conn, "s2") and not PT.held(conn, "s1")
    outs = [json.loads(row(conn, "SELECT output FROM stage_runs WHERE id=?", d)["output"]) for d in ("dz1", "dz2")]
    whole = PT.stitch(outs, inp["parts"])
    second = [ln for ln in whole["lines"] if ln["id"] >= PT.PART_LINES]
    assert second[0]["id"] == PT.PART_LINES and second[0]["start"] == outs[1]["lines"][0]["start"] + 1846.0
    assert second[0]["gap_before"] == round(second[0]["start"] - max(ln["end"] for ln in outs[0]["lines"]))
    assert {ln["speaker"] for ln in second} == {"B", "J", "X2"}
    from octo.insight import transcript
    text = transcript(whole["lines"], {s["speaker"]: s["name"] for s in inp["speakers"]})
    assert "(… " in text and "later, the next part …)" in text and f"[{PT.PART_LINES}]" in text


def test_a_conversation_alone_reads_as_before(tmp_path):
    """The review's input for a conversation that isn't a part: no `parts`, so its hash doesn't move."""
    conn = _hour_cut(tmp_path, second=("G", "J", "K"))
    inp = PL.insight_input(conn, "s1")
    assert "parts" not in inp and set(inp) == {"speakers", "rubric", "corrections"}
    assert inp["corrections"] == PL.corrections_input(conn, "s1")["corrections"]


def test_answered_parts_are_grouped_and_keep_their_reviews_until_redo(tmp_path):
    conn = _hour_cut(tmp_path)
    for sid, run in (("s1", "ins1"), ("s2", "ins2")):  # both reviewed before they were grouped; the second answered
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at, "
                     "upstream, params) VALUES (?, 'insight', ?, 'h', 'done', 1, ?, 0, '[]', '{}')",
                     (run, sid, json.dumps({"skipped": None, "summary": "x", "conduct": [], "next": []})))
    conn.execute("INSERT INTO review_answers (session_id, insight_run, nothing_new, answered_at) VALUES ('s2', 'ins2', 1, 0)")
    conn.execute("UPDATE sessions SET reviewed_at=5 WHERE id='s2'")
    assert PT.auto(conn, "s2") == ["s1"]  # answered or not
    assert PT.held(conn, "s1") and PT.held(conn, "s2")  # no review of the whole made by itself
    assert SV.shown_review(conn, "s2")["id"] == "ins2"  # its answers stay where they are
    conn.execute("UPDATE sessions SET redo_review='ins1' WHERE id='s1'")  # Redo the review, on the whole
    assert not PT.held(conn, "s1")
    # Once the whole one is made, it's the one review: the second part's own isn't shown (nor read by the coach).
    conn.execute("UPDATE stage_runs SET is_current=0 WHERE id='ins1'")
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at, upstream, "
                 "params) VALUES ('insw', 'insight', 's1', 'h', 'done', 1, ?, 0, '[\"dz1\", \"dz2\"]', ?)",
                 (json.dumps({"skipped": None, "summary": "x", "conduct": [], "next": []}),
                  json.dumps(PL.insight_input(conn, "s1"))))
    assert SV.shown_review(conn, "s1")["id"] == "insw" and SV.shown_review(conn, "s2") is None
    assert SV.todo(conn, "s2") == {"kind": "part", "head": "s1"}
    page = SV.detail(conn, row(conn, "SELECT s.*, r.source, r.original, r.started_at, r.path, r.audio_deleted_at FROM sessions s JOIN recordings r ON r.id = s.recording_id "
                                     "WHERE s.id='s1'"))
    assert page["parts"]["whole"] and [p["id"] for p in page["parts"]["list"]] == ["s1", "s2"]
    assert page["parts"]["lines"][0]["id"] == PT.PART_LINES and page["parts"]["lines"][0]["part"] == "s2"
    assert page["parts"]["lines"][0]["start"] >= 1846  # timed as the review times it; the page plays its own part's audio


def test_an_unanswered_group_isnt_held(tmp_path):
    conn = _hour_cut(tmp_path)
    PT.add(conn, "s1", "s2")
    assert not PT.held(conn, "s1") and PT.held(conn, "s2")


def test_progress_and_the_list_count_a_discussion_once(tmp_path):
    from octo import review as R
    conn = _hour_cut(tmp_path)
    conn.execute("UPDATE sessions SET reviewed_at=5")
    assert R.checks(conn)["checks"]["reviewed"]["value"] == 2
    PT.add(conn, "s1", "s2")
    got = R.checks(conn)["checks"]
    assert got["reviewed"]["value"] == 1 and got["recent_new"]["of"] == 1
    assert PT.heads(conn) == {"s1": "s1", "s2": "s1"}
    s1 = row(conn, "SELECT s.*, r.source, r.original, r.started_at FROM sessions s JOIN recordings r ON r.id = s.recording_id "
                   "WHERE s.id='s1'")
    summary = SV.summary(conn, s1)
    assert summary["part_count"] == 2 and summary["duration_s"] == 3600.0
    assert sorted(w["name"] for w in summary["speakers"]) == ["Bo", "Di", "Sam"]


def _reviewed(conn, sid, run, params="{}"):
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at, upstream, params) "
                 "VALUES (?, 'insight', ?, 'h', 'done', 1, ?, 0, '[]', ?)",
                 (run, sid, json.dumps({"skipped": None, "summary": "x", "conduct": [], "next": []}), params))
    conn.execute("INSERT INTO review_answers (session_id, insight_run, nothing_new, answered_at) VALUES (?, ?, 1, 0)", (sid, run))
    conn.execute("UPDATE sessions SET reviewed_at=5 WHERE id=?", (sid,))


def test_deleting_a_later_part_deletes_only_it_and_the_whole_review_that_read_it(tmp_path):
    from octo import privacy as PR
    conn = _hour_cut(tmp_path)
    conn.execute("UPDATE recordings SET audio_deleted_at=1")
    _reviewed(conn, "s1", "ins1")  # part 1's own review, answered before they were grouped
    PT.add(conn, "s1", "s2")
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, queued_at, params) "
                 "VALUES ('old', 'insight', 's1', 'h', 'done', 0, 0, ?)", (json.dumps({"parts": [{"id": "s1"}, {"id": "s2"}]}),))
    assert PR.delete_session(conn, "s2") == ["s2"]
    assert {r["id"]: r["status"] for r in rows(conn, "SELECT * FROM sessions")} == {"s1": "active", "s2": "deleted"}
    assert rows(conn, "SELECT * FROM parts") == [] and not row(conn, "SELECT 1 FROM stage_runs WHERE id='old'")
    assert SV.shown_review(conn, "s1")["id"] == "ins1" and row(conn, "SELECT 1 FROM review_answers WHERE insight_run='ins1'")
    assert row(conn, "SELECT reviewed_at FROM sessions WHERE id='s1'")["reviewed_at"] == 5


def test_deleting_the_first_part_deletes_the_discussion(tmp_path):
    from octo import privacy as PR
    conn = _hour_cut(tmp_path)
    conn.execute("UPDATE recordings SET audio_deleted_at=1")
    PT.add(conn, "s1", "s2")
    assert PR.delete_session(conn, "s1") == ["s1", "s2"]
    assert {r["id"]: r["status"] for r in rows(conn, "SELECT * FROM sessions")} == {"s1": "deleted", "s2": "deleted"}
    assert rows(conn, "SELECT * FROM parts") == []


def test_the_week_and_the_month_count_a_discussion_once(tmp_path):
    from octo import month as MO
    from octo import week as W
    conn = _hour_cut(tmp_path)
    conn.execute("UPDATE sessions SET reviewed_at=1500")
    assert W.counts(conn, 0, 10_000)["heard"] == 2
    PT.add(conn, "s1", "s2")
    assert {k: W.counts(conn, 0, 10_000)[k] for k in ("heard", "reviewed")} == {"heard": 1, "reviewed": 1}
    got = MO._active(conn, 0, 10_000)
    assert [s["id"] for s in got] == ["s1"] and got[0]["end_s"] - got[0]["start_s"] == 3600.0


def test_forget_reaches_the_whole_discussions_review(tmp_path):
    conn = _hour_cut(tmp_path, second=("G", "J", "K"))
    PT.add(conn, "s1", "s2")
    conn.execute("INSERT INTO talks (session_id, created_at, updated_at) VALUES ('s1', 0, 0)")  # a thread on the whole
    assert PT.with_groups(conn, ["s2"]) == ["s2", "s1"]
    V.forget(conn, "Gu")  # only in the second part
    assert not row(conn, "SELECT 1 FROM talks")


def test_the_routes(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import octo.api as API
    a, b = "ses_20261029_00000001", "ses_20261029_00000002"
    _hour_cut(tmp_path, ids="ses_20261029_0000000").commit()
    monkeypatch.setattr(API, "DATA", tmp_path / "data")
    kicks = []
    app = FastAPI()
    app.include_router(API.make_router(lambda: connect(tmp_path / "p.db"),
                                       processor=type("P", (), {"kick": lambda self: kicks.append(1)})()))
    c = TestClient(app)
    assert c.get(f"/api/octo/sessions/{a}").json()["parts"]["offers"][0]["side"] == "after"
    assert c.post(f"/api/octo/sessions/{a}/group", json={"side": "before"}).status_code == 422 and kicks == []
    assert c.post(f"/api/octo/sessions/{b}/group", json={"side": "before"}).json() == {"session": a} and kicks == [1]
    assert [s["id"] for s in c.get("/api/octo/sessions").json()] == [a]
    assert [p["id"] for p in c.get(f"/api/octo/sessions/{b}").json()["parts"]["list"]] == [a, b]
    assert c.post(f"/api/octo/sessions/{a}/ungroup", json={"first": b, "second": a}).status_code == 404
    assert c.post(f"/api/octo/sessions/{a}/ungroup", json={"first": a, "second": b}).json() == {"session": a}
    assert len(c.get("/api/octo/sessions").json()) == 2
    # Delete says what went: from the first part, every part.
    c.post(f"/api/octo/sessions/{b}/group", json={"side": "before"})
    import octo.privacy as PR
    monkeypatch.setattr(PR, "_silence", lambda *a: None)
    assert c.post(f"/api/octo/sessions/{a}/delete", json={"confirm": "delete"}).json() == {"deleted": a, "parts": [a, b]}


def test_the_sweep_lists_before_it_groups_and_never_regroups(tmp_path):
    """`octo parts`: conversations identified before parts existed, the rule over every neighbour at once."""
    conn = _hour_cut(tmp_path)
    found = PT.sweep(conn)
    assert [(f["a"]["id"], f["b"]["id"], f["why"]) for f in found] == [("s1", "s2", "hour")]
    assert rows(conn, "SELECT * FROM parts") == []  # listing writes nothing
    assert len(PT.sweep(conn, apply=True)) == 1 and _ids(PT.group(conn, "s1")) == ["s1", "s2"]
    PT.ungroup(conn, "s1", "s2")
    assert PT.sweep(conn, apply=True) == [] and PT.groups(conn) == []  # the owner's "Not one discussion" stands


def test_a_neighbour_under_a_minute_isnt_offered(tmp_path):
    # Review 5 Nov, item 7: a 40 s neighbour 30 s after a five-minute conversation is too little to be a part of it.
    spans = meeting(["B", "J"], 1, 4.5) + meeting(["B", "J"], 331, 0.5)
    conn = _lab(tmp_path, [(0.0, 300.0, spans), (330.0, 370.0, spans)], {"B": "Bo"})
    s1 = row(conn, "SELECT * FROM sessions WHERE id='s1'")
    assert PT.page(conn, {**s1, "status": "active"}) is None
    conn.execute("UPDATE sessions SET end_s=400 WHERE id='s2'")
    offer = PT.page(conn, {**s1, "status": "active"})["offers"][0]
    assert offer["side"] == "after" and offer["gap_s"] == 30 and offer["duration_s"] == 70
