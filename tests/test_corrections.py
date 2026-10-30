"""The owner's who-said-what fixes applied to a diarize output."""

from octo.corrections import apply, version

DZ = {
    "speakers": ["A", "B", "C"],
    "talk_s": {"A": 4.0, "B": 5.0, "C": 1.0},
    "turns": [{"speaker": "A", "start": 0.0, "end": 4.0}, {"speaker": "B", "start": 4.0, "end": 9.0},
              {"speaker": "C", "start": 9.0, "end": 10.0}],
    "overlapping_turns": [{"speaker": "A", "start": 0.0, "end": 4.3}, {"speaker": "B", "start": 4.0, "end": 9.0},
                          {"speaker": "C", "start": 9.0, "end": 10.0}],
    "lines": [{"id": 0, "speaker": "A", "start": 0.0, "end": 4.0, "text": "a", "uncertain": False},
              {"id": 1, "speaker": "B", "start": 4.0, "end": 6.0, "text": "b1", "uncertain": True},
              {"id": 2, "speaker": "B", "start": 6.0, "end": 9.0, "text": "b2", "uncertain": False},
              {"id": 3, "speaker": "C", "start": 9.0, "end": 10.0, "text": "c", "uncertain": False}],
}


def test_no_fixes_is_the_original():
    assert apply(DZ, []) is DZ and version([]) == "none"


def test_merge_moves_everything_and_drops_the_letter():
    out = apply(DZ, [{"kind": "merge", "from": "C", "into": "B"}])
    assert out["speakers"] == ["A", "B"]
    assert out["talk_s"] == {"A": 4.0, "B": 6.0}
    assert [ln["speaker"] for ln in out["lines"]] == ["A", "B", "B", "B"]
    assert out["turns"][-1] == {"speaker": "B", "start": 4.0, "end": 10.0}  # neighbouring turns joined
    assert DZ["speakers"] == ["A", "B", "C"]  # the stored output is never edited


def test_moving_a_line_moves_its_talk_time_and_clears_the_doubt():
    out = apply(DZ, [{"kind": "line", "line": 1, "to": "A"}])
    assert out["lines"][1]["speaker"] == "A" and out["lines"][1]["uncertain"] is False
    assert out["talk_s"] == {"A": 6.0, "B": 3.0, "C": 1.0}
    assert {"speaker": "A", "start": 0.0, "end": 6.0} in out["turns"]
    # overlapping turns: only B's speech in that span changes hands; A's own overlap stays A's
    assert {"speaker": "A", "start": 0.0, "end": 4.3} in out["overlapping_turns"]


def test_a_speaker_left_with_nothing_disappears():
    out = apply(DZ, [{"kind": "line", "line": 3, "to": "B"}])
    assert out["speakers"] == ["A", "B"]


def test_fixes_apply_in_order():
    fixes = [{"kind": "merge", "from": "C", "into": "B"}, {"kind": "line", "line": 3, "to": "A"}]
    assert apply(DZ, fixes)["lines"][3]["speaker"] == "A"
    assert version(fixes) != version(fixes[:1])


def test_two_speakers_confirmed_as_one_person_become_one(tmp_path):
    import json

    from octo.corrections import for_run, merge_same_person
    from octo.db import connect

    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('r1', 'phone', 'x', 9, 0)")
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('s1', 'r1', 0, 9, 5, 0)")
    for pid in ("jay", "nisha"):
        conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES (?, ?, ?, 0)", (pid, pid, int(pid == "jay")))
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, upstream, params, output, queued_at) "
                 "VALUES ('dz', 'diarize', 's1', 'h', 'done', 1, '[]', '{}', ?, 0)", (json.dumps(DZ),))
    people = {"A": "jay", "B": "jay", "C": "nisha"}  # pyannote split the owner into A and B
    for letter, person in people.items():
        conn.execute("INSERT INTO session_speakers (session_id, speaker, person_id, state, diarize_run) "
                     "VALUES ('s1', ?, ?, 'confirmed', 'dz')", (letter, person))
    assert merge_same_person(conn, "s1") == [("A", "B")]  # into B: more talk time (5 s vs 4 s)
    assert apply(DZ, for_run(conn, "s1", "dz"))["speakers"] == ["B", "C"]
    assert merge_same_person(conn, "s1") == []  # done once; nothing to add again



def _two_named(tmp_path, same_person: str, is_self: int):
    import json

    from octo.db import connect
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('r1', 'phone', 'x', 9, 0)")
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('s1', 'r1', 0, 9, 5, 0)")
    conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES ('p', ?, ?, 0), ('q', 'Other', 0, 0)", (same_person, is_self))
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, upstream, params, output, queued_at) "
                 "VALUES ('dz', 'diarize', 's1', 'h', 'done', 1, '[]', '{}', ?, 0)", (json.dumps(DZ),))
    for letter, person in {"A": "p", "B": "p", "C": "q"}.items():
        conn.execute("INSERT INTO session_speakers (session_id, speaker, person_id, state, diarize_run) "
                     "VALUES ('s1', ?, ?, 'confirmed', 'dz')", (letter, person))
    return conn


def test_two_people_who_share_a_name_are_asked_about_not_merged(tmp_path):  # review 4 Oct, M22
    import numpy as np

    from octo.corrections import merge_same_person, same_person_asks
    conn = _two_named(tmp_path, "Asha", 0)
    assert merge_same_person(conn, "s1") == []  # no voiceprints to compare: ask, don't merge
    assert same_person_asks(conn, "s1") == [{"name": "Asha", "letters": ["A", "B"]}]
    v = np.ones(4, np.float32)
    for letter in ("A", "B"):  # their voices agree: the same person after all
        conn.execute("INSERT INTO voiceprints (id, person_id, embedding, seconds, source, created_at) VALUES (?, 'p', ?, 5, ?, 0)",
                     (f"vp{letter}", v.tobytes(), f"label:s1:{letter}"))
    assert merge_same_person(conn, "s1") == [("A", "B")] and same_person_asks(conn, "s1") == []


def test_fixing_a_wrong_yes_takes_the_merge_back(tmp_path):  # M22
    from octo.corrections import for_run, merge_same_person
    conn = _two_named(tmp_path, "Sam", 1)
    assert merge_same_person(conn, "s1") == [("A", "B")]
    conn.execute("UPDATE session_speakers SET person_id='q' WHERE speaker='A'")  # A was someone else
    merge_same_person(conn, "s1")
    assert apply(DZ, for_run(conn, "s1", "dz"))["speakers"] == ["A", "B", "C"]  # A's speech is A's again


def test_a_name_carries_to_a_new_run_only_where_most_of_its_talk_went():  # M32
    from octo.stages import remap_speakers
    old = [{"speaker": "A", "start": 0, "end": 10}, {"speaker": "B", "start": 10, "end": 20}]
    assert remap_speakers(old, [{"speaker": "X", "start": 0, "end": 10}, {"speaker": "Y", "start": 10, "end": 20}]) == {"A": "X", "B": "Y"}
    # B's talk now split three ways: none of them has most of it, so B is asked about again.
    split = [{"speaker": "X", "start": 0, "end": 10}, {"speaker": "Y", "start": 10, "end": 13},
             {"speaker": "Z", "start": 13, "end": 16}, {"speaker": "W", "start": 16, "end": 20}]
    assert remap_speakers(old, split) == {"A": "X"}
