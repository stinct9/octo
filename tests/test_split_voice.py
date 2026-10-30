"""One speaker who is really two known people (people plan 4.2; 8 Oct 21:30, two colleagues heard as one)."""

import json

import numpy as np

import octo.pipeline as P
from octo import RATE
from octo import corrections as C
from octo import voice as V
from octo.db import connect, row, rows


class ByLevel:
    """A stand-in embedder: a piece of audio at level 1 sounds like `one`, at level 2 like `two`."""

    def __init__(self, one, two):
        self.one, self.two = one, two

    def embed(self, samples):
        return self.one if float(np.mean(samples)) < 1.5 else self.two

    def embed_turns(self, samples, turns):
        pieces = [samples[int(a * RATE):int(b * RATE)] for a, b in turns]
        return (self.embed(np.concatenate(pieces)), sum(b - a for a, b in turns)) if pieces else None


ANDY, BOB = np.array([1, 0, 0], dtype=np.float32), np.array([0, 1, 0], dtype=np.float32)
PRINTS = [{"person_id": "per_andy", "name": "Bram", "is_self": False, "embedding": ANDY},
          {"person_id": "per_bob", "name": "Bob", "is_self": False, "embedding": BOB}]


def audio(*spans):
    """Seconds of level-1 (Bram) or level-2 (Bob) audio: spans of (start, end, level)."""
    out = np.zeros(int(max(e for _, e, _ in spans) * RATE), dtype=np.float32)
    for a, b, level in spans:
        out[int(a * RATE):int(b * RATE)] = level
    return out


def test_a_speaker_who_is_two_known_people_is_split_along_their_voices():
    x = audio((0, 60, 1), (60, 90, 2), (90, 120, 1))
    got = V.split_by_voice(ByLevel(ANDY, BOB), x, [(0, 40), (40, 75), (75, 120)], PRINTS)
    assert [p["name"] for p in got] == ["Bram", "Bob"]
    (a, b), = got[1]["spans"]
    assert abs(a - 60) <= V.SPLIT_PIECE_S and abs(b - 90) <= V.SPLIT_PIECE_S  # to within a piece
    covered = sum(b - a for p in got for a, b in p["spans"])
    assert abs(covered - 120) < 0.01  # every turn goes to one of them


def test_one_person_or_a_little_of_someone_else_is_not_split():
    one = V.split_by_voice(ByLevel(ANDY, BOB), audio((0, 120, 1)), [(0, 120)], PRINTS)
    little = V.split_by_voice(ByLevel(ANDY, BOB), audio((0, 112, 1), (112, 120, 2)), [(0, 120)], PRINTS)
    assert one is None and little is None  # 8 s of Bob: under SPLIT_MIN_S (and 7%)


def test_the_split_moves_turns_and_lines_and_comes_after_its_letter():
    dz = {"speakers": ["A", "B"], "talk_s": {},
          "turns": [{"speaker": "A", "start": 0, "end": 10}, {"speaker": "B", "start": 10, "end": 40}],
          "overlapping_turns": [{"speaker": "B", "start": 10, "end": 40}],
          "lines": [{"id": 0, "speaker": "B", "start": 11, "end": 19}, {"id": 1, "speaker": "B", "start": 21, "end": 29},
                    {"id": 2, "speaker": "B", "start": 31, "end": 39}]}
    out = C.apply(dz, [{"kind": "split", "letter": "B", "into": "C", "spans": [[20, 30]]}])
    assert out["speakers"] == ["A", "B", "C"]
    assert [(t["speaker"], t["start"], t["end"]) for t in out["turns"]] == [("A", 0, 10), ("B", 10, 20), ("C", 20, 30), ("B", 30, 40)]
    assert [ln["speaker"] for ln in out["lines"]] == ["B", "C", "B"]
    assert out["talk_s"] == {"A": 10, "B": 20, "C": 10}
    assert C.next_letter({"A", "B", "C"}) == "D" and C.next_letter({chr(65 + i) for i in range(26)}) == "AA"


def _session(tmp_path):
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('r', 'device', 'x', 100, 0)")
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('s', 'r', 0, 100, 90, 0)")
    dz = {"speakers": ["A", "B"], "talk_s": {}, "lines": [],
          "turns": [{"speaker": "A", "start": 0, "end": 30}, {"speaker": "B", "start": 30, "end": 100}]}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('dz', 'diarize', 's', 'h', 'done', 1, ?, 0)", (json.dumps(dz),))
    for pid, name, me in (("per_jay", "Sam", 1), ("per_andy", "Bram", 0), ("per_bob", "Bob", 0)):
        conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES (?, ?, ?, 0)", (pid, name, me))
    return conn


def _identify(conn, rid):
    m = lambda pid, name, sim: {"person_id": pid, "name": name, "similarity": sim, "margin": 0.3, "ambiguous": False}
    out = {"diarize_run": "dz", "jay_present": True, "speakers": [
        {"speaker": "A", "match": m("per_jay", "Sam", 0.86), "speech_s": 30, "self_similarity": 0.86},
        {"speaker": "B", "match": m("per_andy", "Bram", 0.89), "speech_s": 70, "self_similarity": 0.2,
         "split": [{"person_id": "per_andy", "name": "Bram", "spans": [[30, 70]], "match": m("per_andy", "Bram", 0.87)},
                   {"person_id": "per_bob", "name": "Bob", "spans": [[70, 100]], "match": m("per_bob", "Bob", 0.88)}]}]}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, output, queued_at) "
                 "VALUES (?, 'identify', 's', 'h', 'done', ?, 0)", (rid, json.dumps(out)))
    return row(conn, "SELECT * FROM stage_runs WHERE id=?", rid)


def test_identify_splits_an_unanswered_speaker_and_names_each_part(tmp_path):
    conn = _session(tmp_path)
    P._apply_identify(conn, _identify(conn, "id1"))
    fixes = C.for_run(conn, "s", "dz")
    assert [(f["kind"], f["letter"], f["into"], f["spans"], f["by"]) for f in fixes] == [("split", "B", "C", [[70, 100]], "lab")]
    who = {r["speaker"]: (r["person_id"], r["state"]) for r in rows(conn, "SELECT * FROM session_speakers")}
    assert who == {"A": ("per_jay", "auto"), "B": ("per_andy", "suggested"), "C": ("per_bob", "suggested")}
    P._apply_identify(conn, _identify(conn, "id2"))  # identify again: the split isn't made twice
    assert len(C.for_run(conn, "s", "dz")) == 1


def test_a_speaker_jay_answered_for_is_not_split(tmp_path):
    conn = _session(tmp_path)
    conn.execute("INSERT INTO session_speakers VALUES ('s', 'B', 'per_andy', 'confirmed', NULL, 'dz')")
    P._apply_identify(conn, _identify(conn, "id1"))
    assert C.for_run(conn, "s", "dz") == []


def _identify_merge(conn, rid, merges, speakers=None):
    m = lambda pid, name, sim: {"person_id": pid, "name": name, "similarity": sim, "margin": 0.3, "ambiguous": False}
    out = {"diarize_run": "dz", "jay_present": True, "merges": merges, "speakers": speakers or [
        {"speaker": "A", "match": m("per_andy", "Bram", 0.8), "speech_s": 30, "self_similarity": 0.2},
        {"speaker": "B", "match": m("per_andy", "Bram", 0.82), "speech_s": 70, "self_similarity": 0.2}]}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, output, queued_at) "
                 "VALUES (?, 'identify', 's', 'h', 'done', ?, 0)", (rid, json.dumps(out)))
    return row(conn, "SELECT * FROM stage_runs WHERE id=?", rid)


ANDY_TWICE = [{"from": "A", "into": "B", "person_id": "per_andy"}]


def test_one_known_person_heard_as_two_speakers_is_merged_and_an_undo_sticks(tmp_path):
    conn = _session(tmp_path)
    P._apply_identify(conn, _identify_merge(conn, "id1", ANDY_TWICE))
    fixes = C.for_run(conn, "s", "dz")
    assert [(f["kind"], f["from"], f["into"], f["by"]) for f in fixes] == [("merge", "A", "B", "lab")]
    dz = json.loads(row(conn, "SELECT output FROM stage_runs WHERE id='dz'")["output"])
    assert C.apply(dz, fixes)["speakers"] == ["B"]
    P._apply_identify(conn, _identify_merge(conn, "id2", ANDY_TWICE))
    assert len(C.for_run(conn, "s", "dz")) == 1  # not twice
    assert C.undo_last(conn, "s", "dz")  # The owner: they're two people after all
    P._apply_identify(conn, _identify_merge(conn, "id3", ANDY_TWICE))
    assert [f["kind"] for f in C.for_run(conn, "s", "dz")] == ["declined"]  # and identify doesn't merge them again
    assert not C.undo_last(conn, "s", "dz")  # nothing left to undo: the memory of it isn't a fix


def test_no_merge_against_jays_answers_and_the_one_he_confirmed_is_kept(tmp_path):
    conn = _session(tmp_path)
    conn.execute("INSERT INTO session_speakers VALUES ('s', 'A', 'per_bob', 'confirmed', NULL, 'dz')")
    P._apply_identify(conn, _identify_merge(conn, "id1", ANDY_TWICE))
    assert C.for_run(conn, "s", "dz") == []  # The owner said A is Bob
    conn.execute("UPDATE session_speakers SET person_id='per_andy' WHERE speaker='A'")
    P._apply_identify(conn, _identify_merge(conn, "id2", ANDY_TWICE))
    assert [(f["from"], f["into"]) for f in C.for_run(conn, "s", "dz")] == [("B", "A")]  # A, which he confirmed, stays


def test_an_undone_lab_split_is_not_made_again(tmp_path):
    conn = _session(tmp_path)
    P._apply_identify(conn, _identify(conn, "id1"))
    assert C.undo_last(conn, "s", "dz")
    P._apply_identify(conn, _identify(conn, "id2"))
    assert [f["kind"] for f in C.for_run(conn, "s", "dz")] == ["declined"]


def test_three_speakers_of_one_person_all_end_up_as_one(tmp_path):
    conn = _session(tmp_path)
    m = {"person_id": "per_andy", "name": "Bram", "similarity": 0.8, "margin": 0.3, "ambiguous": False}
    speakers = [{"speaker": x, "match": m, "speech_s": t, "self_similarity": 0.2} for x, t in (("A", 30), ("B", 70), ("C", 5))]
    merges = [{"from": "A", "into": "B", "person_id": "per_andy"}, {"from": "C", "into": "B", "person_id": "per_andy"}]
    P._apply_identify(conn, _identify_merge(conn, "id1", merges, speakers))
    assert sorted((f["from"], f["into"]) for f in C.for_run(conn, "s", "dz")) == [("A", "B"), ("C", "B")]


def test_someone_known_from_two_conversations_is_named_without_asking(tmp_path):
    """People plan 4.4: the C4 bar for others, once their voice is known from two conversations."""
    conn = _session(tmp_path)
    vec = np.ones(3, dtype=np.float32).tobytes()
    def prints_from(person, *sessions):
        conn.execute("DELETE FROM voiceprints WHERE person_id=?", (person,))
        for n, s_ in enumerate(sessions):
            conn.execute("INSERT INTO voiceprints (id, person_id, embedding, seconds, source, created_at, model) VALUES "
                         "(?, ?, ?, 30, ?, 0, ?)", (f"vp_{person}_{n}", person, vec, f"label:{s_}:A", V.EMBEDDING_MODEL))
    m = lambda sim, margin: {"person_id": "per_andy", "name": "Bram", "similarity": sim, "margin": margin, "ambiguous": False}
    state = lambda: row(conn, "SELECT state FROM session_speakers WHERE speaker='B'")["state"]
    prints_from("per_andy", "ses_one")
    P._suggest(conn, "s", "B", m(0.85, 0.4), "dz")
    assert state() == "suggested"  # known from one conversation only
    prints_from("per_andy", "ses_one", "ses_one", "ses_two")
    P._suggest(conn, "s", "B", m(0.85, 0.4), "dz")
    assert state() == "auto"
    P._suggest(conn, "s", "B", m(0.68, 0.4), "dz")
    assert state() == "suggested"  # under the bar
    P._suggest(conn, "s", "B", m(0.85, 0.2), "dz")
    assert state() == "suggested"  # too close to someone else
