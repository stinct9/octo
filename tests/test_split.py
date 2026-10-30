"""Two discussions in one conversation: where the people change, and splitting there."""

from octo import split as S


def turns(*spans):
    return [{"speaker": s, "start": a, "end": b} for s, a, b in spans]


def meeting(speakers, start, minutes, step=20.0):
    out, t, i = [], start, 0
    while t < start + minutes * 60:
        out.append((speakers[i % len(speakers)], t, t + step - 2))
        t, i = t + step, i + 1
    return out


def test_suggests_where_the_people_change_ignoring_jay():
    first = meeting(["B", "J", "D"], 0, 30)
    second = meeting(["G", "J", "K"], 30 * 60 + 90, 30)  # a 90 s pause, then other people
    got = S.suggest(turns(*first, *second), ignore={"J"})
    assert len(got) == 1 and abs(got[0]["at"] - (30 * 60 + 45)) < 30
    assert set(got[0]["before"]) == {"B", "D"} and set(got[0]["after"]) == {"G", "K"}


def test_the_people_are_compared_over_the_whole_conversation_too():
    # 15 Oct 21:31: B quiet for over 10 minutes after a pause, then back -- the same discussion
    t = (meeting(["B", "J", "D"], 0, 15) + meeting(["G", "J", "K"], 15 * 60 + 90, 11)
         + meeting(["B", "J", "D"], 26 * 60 + 90, 10))
    assert S.suggest(turns(*t), {"J"}) == []


def test_no_suggestion_when_the_same_people_carry_on_after_a_pause():
    one = meeting(["B", "J", "D"], 0, 20) + meeting(["B", "J", "D"], 20 * 60 + 90, 20)
    assert S.suggest(turns(*one), ignore={"J"}) == []


def test_no_suggestion_near_either_end():
    t = meeting(["B", "D"], 0, 2) + meeting(["G", "K"], 2 * 60 + 60, 20)  # first part under 3 min
    assert S.suggest(turns(*t)) == []


def test_apply_cuts_conversations_and_their_speech():
    convs = [{"start": 10.0, "end": 300.0, "speech_s": 200.0, "speech": [[10.0, 100.0], [140.0, 250.0]]}]
    got = S.apply(convs, [120.0, 1000.0], min_speech_s=2.0)  # 1000 is outside: ignored
    assert [(c["start"], c["end"], c["speech_s"]) for c in got] == [(10.0, 120.0, 90.0), (120.0, 300.0, 110.0)]
    assert S.apply(convs, [], 2.0) == convs


def test_a_split_reruns_only_its_own_recording(tmp_path):
    from octo.db import connect
    from octo.planning import splits_input as _splits
    conn = connect(tmp_path / "s.db")
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('r1', 'device', 'x', 1, 0), "
                 "('r2', 'device', 'y', 1, 0)")
    assert _splits(conn, "r1") == {}  # no splits anywhere: find_conversations' inputs don't change
    S.add(conn, "r1", 1993.2)
    assert _splits(conn, "r1") == {"splits": [1993.2]} and _splits(conn, "r2") == {}


def test_a_split_takes_effect_at_once_and_a_rerun_agrees(tmp_path):
    import json
    from octo.db import connect, row, rows
    conn = connect(tmp_path / "n.db")
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('r1', 'device', 'x', 5000, 0)")
    conv = {"start": 350.0, "end": 4430.0, "speech_s": 3000.0, "speech": [[350.0, 2300.0], [2390.0, 4430.0]]}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('fc', 'find_conversations', 'r1', 'h', 'done', 1, ?, 0)", (json.dumps({"conversations": [conv]}),))
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('s1', 'r1', 350, 4430, 3000, 0)")
    new = S.split_now(conn, row(conn, "SELECT * FROM sessions WHERE id='s1'"), 2345.0)
    got = rows(conn, "SELECT id, start_s, end_s, speech_s FROM sessions ORDER BY start_s")
    assert got == [{"id": "s1", "start_s": 350.0, "end_s": 2345.0, "speech_s": 1950.0},
                   {"id": new, "start_s": 2345.0, "end_s": 4430.0, "speech_s": 2040.0}]
    # find_conversations re-running with the split gives the same two: nothing moves again.
    assert [(c["start"], c["end"]) for c in S.apply([conv], S.for_recording(conn, "r1"), 2.0)] == [(350.0, 2345.0), (2345.0, 4430.0)]


def _lab_with(tmp_path, spans, self_state="auto", lines=()):
    """A recording with one conversation of these turns, the owner found by identify (`self_state`)."""
    import json
    from octo import voice as V
    from octo.db import connect
    conn = connect(tmp_path / "a.db")
    end = max(b for _, _, b in spans)
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('r1', 'device', 'x', ?, 0)", (end + 10,))
    conv = {"start": 0.0, "end": end, "speech_s": end, "speech": [[0.0, end]]}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('fc', 'find_conversations', 'r1', 'h', 'done', 1, ?, 0)", (json.dumps({"conversations": [conv]}),))
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('s1', 'r1', 0, ?, ?, 0)", (end, end))
    letters = sorted({s for s, _, _ in spans})
    dz = {"speakers": letters, "turns": turns(*spans), "lines": list(lines), "talk_s": {}}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('dz', 'diarize', 's1', 'h', 'done', 1, ?, 0)", (json.dumps(dz),))
    jay = V.ensure_person(conn, "Sam", is_self=True)
    if self_state:
        conn.execute("INSERT INTO session_speakers VALUES ('s1', 'J', ?, ?, 0.9, 'dz')", (jay["id"], self_state))
    return conn


def test_the_lab_splits_a_clear_change_and_join_back_undoes_it_for_good(tmp_path):
    from octo.db import row, rows
    conn = _lab_with(tmp_path, meeting(["B", "J", "D"], 0, 15) + meeting(["G", "J", "K"], 15 * 60 + 85, 15))
    second = S.auto(conn, "s1")
    assert second and row(conn, "SELECT by FROM splits")["by"] == "lab"
    first, other = (row(conn, "SELECT * FROM sessions WHERE id=?", i) for i in ("s1", second))
    assert abs(first["end_s"] - (15 * 60 + 40)) < 30 and other["start_s"] == first["end_s"]
    assert S.here(conn, first) == {"by": "lab", "side": "after", "other": second, "at": first["end_s"]}
    assert S.here(conn, other) == {"by": "lab", "side": "before", "other": "s1", "at": 0.0}

    assert S.join_back(conn, second) == "s1"
    got = {r["id"]: (r["end_s"], r["status"]) for r in rows(conn, "SELECT * FROM sessions")}
    assert got == {"s1": (other["end_s"], "active"), second: (other["end_s"], "superseded")}
    assert S.for_recording(conn, "r1") == [] and S.here(conn, row(conn, "SELECT * FROM sessions WHERE id='s1'")) is None
    assert S.auto(conn, "s1") is None  # joined back: the lab leaves it
    S.add(conn, "r1", first["end_s"])  # The owner can still split there themselves
    assert S.for_recording(conn, "r1") == [first["end_s"]] and row(conn, "SELECT by FROM splits")["by"] == "jay"


def test_the_lab_leaves_an_unclear_change_and_what_jay_has_answered(tmp_path):
    for d in ("1", "2", "3"):
        (tmp_path / d).mkdir()
    short = meeting(["B", "J", "D"], 0, 15) + meeting(["G", "J", "K"], 15 * 60 + 40, 15)  # a 40 s pause: only suggested
    conn = _lab_with(tmp_path / "1", short)
    assert S.suggest(turns(*short), {"J"}) and S.auto(conn, "s1") is None

    clear = meeting(["B", "J", "D"], 0, 15) + meeting(["G", "J", "K"], 15 * 60 + 85, 15)
    conn = _lab_with(tmp_path / "2", clear, self_state=None)
    assert S.auto(conn, "s1") is None  # The owner not found yet: they'd count as someone in both parts
    conn = _lab_with(tmp_path / "3", clear)
    conn.execute("UPDATE sessions SET reviewed_at=1")
    assert S.auto(conn, "s1") is None


def test_the_lab_splits_calls_back_to_back_when_the_first_one_ended(tmp_path):
    # 16 Oct 20:16: one call ended and the next began 12.7 s later, nobody in common
    for d in ("1", "2", "3"):
        (tmp_path / d).mkdir()
    calls = meeting(["B", "J", "D"], 0, 15) + meeting(["G", "J", "K"], 15 * 60 + 10, 15)
    assert len(S.suggest(turns(*calls), {"J"})) == 1
    assert S.auto(_lab_with(tmp_path / "1", calls), "s1") is None  # suggested only: nothing says the call ended
    said = [{"start": 15 * 60 - 20, "text": "Thanks, bye!"}, {"start": 15 * 60 + 12, "text": "Hi Omar, can you hear me?"}]
    assert S.auto(_lab_with(tmp_path / "2", calls, lines=said), "s1")
    # 25:19: the owner talks between the calls; the others are silent 70 s
    between = meeting(["B", "J", "D"], 0, 15) + [("J", 15 * 60 + 10, 15 * 60 + 70)] + meeting(["G", "J", "K"], 15 * 60 + 80, 15)
    assert S.auto(_lab_with(tmp_path / "3", between), "s1")


def test_join_back_needs_a_split(tmp_path):
    import pytest
    conn = _lab_with(tmp_path, meeting(["B", "J"], 0, 10))
    with pytest.raises(KeyError):
        S.join_back(conn, "s1")


def _long(tmp_path, sid="s1"):
    """One 70-minute conversation (recording 350-4550 s), as find_conversations left it."""
    import json
    from octo.db import connect
    conn = connect(tmp_path / "m.db")
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('r1', 'device', 'x', 5000, 0)")
    conv = {"start": 350.0, "end": 4550.0, "speech_s": 3900.0, "speech": [[350.0, 1500.0], [1600.0, 3000.0], [3100.0, 4550.0]]}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('fc', 'find_conversations', 'r1', 'h', 'done', 1, ?, 0)", (json.dumps({"conversations": [conv]}),))
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES (?, 'r1', 350, 4550, 3900, 0)",
                 (sid,))
    return conn, conv


def test_two_cuts_make_three_conversations_and_a_rerun_agrees(tmp_path):
    from octo.db import row, rows
    conn, conv = _long(tmp_path)
    second, third = S.split_at(conn, row(conn, "SELECT * FROM sessions WHERE id='s1'"), [1550.0, 3050.0])
    got = rows(conn, "SELECT id, start_s, end_s, speech_s FROM sessions ORDER BY start_s")
    assert got == [{"id": "s1", "start_s": 350.0, "end_s": 1550.0, "speech_s": 1150.0},
                   {"id": second, "start_s": 1550.0, "end_s": 3050.0, "speech_s": 1400.0},
                   {"id": third, "start_s": 3050.0, "end_s": 4550.0, "speech_s": 1450.0}]
    assert S.for_recording(conn, "r1") == [1550.0, 3050.0]
    # find_conversations re-running with the splits gives the same three (sessions are keyed on their start).
    assert [(c["start"], c["end"]) for c in S.apply([conv], S.for_recording(conn, "r1"), 2.0)] == \
        [(350.0, 1550.0), (1550.0, 3050.0), (3050.0, 4550.0)]


def test_join_back_of_the_middle_part_undoes_only_the_cut_at_its_start(tmp_path):
    from octo.db import row, rows
    conn, _ = _long(tmp_path)
    second, third = S.split_at(conn, row(conn, "SELECT * FROM sessions WHERE id='s1'"), [1550.0, 3050.0])
    assert S.join_back(conn, second) == "s1"
    got = {r["id"]: (r["start_s"], r["end_s"], r["status"]) for r in rows(conn, "SELECT * FROM sessions")}
    assert got == {"s1": (350.0, 3050.0, "active"), second: (1550.0, 3050.0, "superseded"), third: (3050.0, 4550.0, "active")}
    assert S.for_recording(conn, "r1") == [3050.0]
    assert S.here(conn, row(conn, "SELECT * FROM sessions WHERE id='s1'"))["other"] == third  # the other cut still stands
    assert S.join_back(conn, third) == "s1" and S.for_recording(conn, "r1") == []


def test_the_split_route_takes_one_point_or_several(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import octo.api as API
    from octo.db import connect, rows
    sid = "ses_20261022_27bb0e89"
    _long(tmp_path, sid)[0].commit()
    monkeypatch.setattr(API, "DATA", tmp_path / "data")
    kicks = []
    app = FastAPI()
    app.include_router(API.make_router(lambda: connect(tmp_path / "m.db"),
                                       processor=type("P", (), {"kick": lambda self: kicks.append(1)})()))
    c = TestClient(app)
    for bad in ([], [10.0], [600.0, 620.0], [600.0, 600.0], [600.0, 4190.0]):  # none; near an end; too close; twice
        r = c.post(f"/api/octo/sessions/{sid}/split", json={"at": bad})
        assert r.status_code == 422, bad
    assert kicks == [] and len(rows(connect(tmp_path / "m.db"), "SELECT id FROM sessions")) == 1

    r = c.post(f"/api/octo/sessions/{sid}/split", json={"at": [2700.0, 1200.0]})  # any order
    assert r.status_code == 200 and r.json()["split_at"] == [1200.0, 2700.0] and len(r.json()["new_sessions"]) == 2
    assert r.json()["new_session"] == r.json()["new_sessions"][0] and kicks == [1]
    spans = [(x["start_s"], x["end_s"]) for x in rows(connect(tmp_path / "m.db"), "SELECT * FROM sessions ORDER BY start_s")]
    assert spans == [(350.0, 1550.0), (1550.0, 3050.0), (3050.0, 4550.0)]

    r = c.post(f"/api/octo/sessions/{sid}/split", json={"at": 600.0})  # a single number, as the phone and older pages send
    assert r.status_code == 200 and r.json()["split_at"] == 600.0 and r.json()["new_session"] and kicks == [1, 1]
    assert [x["end_s"] for x in rows(connect(tmp_path / "m.db"), "SELECT end_s FROM sessions ORDER BY start_s")][:2] == [950.0, 1550.0]
