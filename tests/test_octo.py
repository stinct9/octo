"""Conversation lab: grouping, silence filter, versioned stage runs, failures, recovery, conversion."""

import json
import shutil
import subprocess
import threading
import time
from dataclasses import replace

import numpy as np
import pytest

import octo.pipeline as P
import octo.worker as W
from octo import audio as A
from octo.db import connect, row, rows
from octo.asr import overlap
from octo.stages import STAGES, Stage, group_speech


# ---- pure functions --------------------------------------------------------------------------

def test_group_speech_merges_short_gaps_and_drops_blips():
    speech = [(10, 12), (40, 45), (300, 301), (600, 603), (604, 606)]
    got = group_speech(speech, gap_s=180, min_speech_s=2)
    assert [(c["start"], c["end"]) for c in got] == [(10, 45), (600, 606)]  # 300-301 is a 1 s blip
    assert got[0]["speech_s"] == 7.0
    assert got[0]["speech"] == [[10, 12], [40, 45]]


def test_overlap():
    assert overlap((0, 10), [[2, 4], [8, 20]]) == 4
    assert overlap((0, 1), [[5, 6]]) == 0


# ---- pipeline with cheap fake stages ---------------------------------------------------------

CALLS: list[tuple[str, str]] = []


def fake_fc(inputs, params):
    CALLS.append(("fc", params.get("marker", "")))
    convs = params.get("convs", [[1.0, 3.0], [10.0, 12.0]])
    return {"speech_s": 4.0, "conversations": [
        {"start": a, "end": b, "speech_s": b - a, "speech": [[a, b]]} for a, b in convs]}


def fake_tr(inputs, params):
    CALLS.append(("tr", str(inputs["start_s"])))
    if params.get("boom"):
        raise RuntimeError("model exploded")
    return {"language": "en", "model": "fake", "segments": [{"id": 0, "start": 0, "end": 1, "text": "hi",
            "avg_logprob": -0.1, "no_speech_prob": 0.0, "words": []}], "rejected_as_silence": []}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    CALLS.clear()
    monkeypatch.setattr(P, "HOME", tmp_path)
    monkeypatch.setattr(P, "AUDIO", tmp_path / "audio")
    monkeypatch.setattr(W, "HOME", tmp_path)
    stages = {
        "find_conversations": Stage("find_conversations", 1, "recording", None, {}, fake_fc),
        "transcribe": Stage("transcribe", 1, "session", "find_conversations", {}, fake_tr),
    }
    monkeypatch.setattr(P, "STAGES", stages)
    monkeypatch.setattr(W, "STAGES", stages)
    conn = connect(tmp_path / "t.db")

    def execute(run_id):  # the worker, in-process, against the test database
        run = row(conn, "SELECT * FROM stage_runs WHERE id=?", run_id)
        conn.execute("UPDATE stage_runs SET status='running' WHERE id=?", (run_id,))
        try:
            out = stages[run["stage"]].fn(W.inputs_for(conn, run), json.loads(run["params"]))
        except Exception as e:
            conn.execute("UPDATE stage_runs SET status='failed', error=? WHERE id=?", (str(e), run_id))
            return 1, str(e)
        conn.execute("UPDATE stage_runs SET status='done', output=? WHERE id=?", (json.dumps(out), run_id))
        return 0, ""

    wav = tmp_path / "in.wav"
    A.write(wav, np.zeros(16000 * 20, np.float32))
    return {"conn": conn, "stages": stages, "run": lambda: P.run_pending(conn, log=lambda _: None, execute=execute),
            "wav": wav, "mp": monkeypatch}


def set_stage(lab, name, **changes):
    lab["stages"][name] = replace(lab["stages"][name], **changes)


def test_end_to_end_and_idempotent(lab):
    P.add_recording(lab["conn"], lab["wav"], "bench")
    assert lab["run"]() == {"done": 3, "failed": 0, "skipped": 0, "busy": 0}  # 1 find + 2 transcribes
    assert len(rows(lab["conn"], "SELECT * FROM sessions WHERE status='active'")) == 2
    assert lab["run"]() == {"done": 0, "failed": 0, "skipped": 0, "busy": 0}  # nothing changed, nothing re-runs


def test_the_same_memo_added_twice_at_once_is_added_once(lab, tmp_path):  # audit 14 Oct
    store = A.store
    lab["mp"].setattr(A, "store", lambda *a: (time.sleep(0.3), store(*a)))  # both past the hash before either stores
    got = []

    def add():
        try:
            got.append(P.add_recording(connect(tmp_path / "t.db"), lab["wav"], "phone"))
        except P.DuplicateRecording:
            got.append("dup")
    ts = [threading.Thread(target=add) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert sorted(got)[0] == "dup" and len(rows(lab["conn"], "SELECT id FROM recordings")) == 1


def test_no_step_starts_while_a_deploy_changes_the_code(lab, tmp_path):  # audit 14 Oct
    import os

    from octo import deploy as DP
    lab["mp"].setattr(P, "ROOT", tmp_path)
    P.add_recording(lab["conn"], lab["wav"], "bench")
    (tmp_path / DP.PAUSE).write_text(f"{os.getpid()}\n")
    assert lab["run"]()["done"] == 0 and row(lab["conn"], "SELECT 1 FROM stage_runs WHERE status='queued'")
    (tmp_path / DP.PAUSE).unlink()
    assert lab["run"]()["done"] == 3


def test_finding_conversations_again_redoes_only_the_conversations_that_changed(lab):
    # 4 Oct (review M37): a split in one conversation used to re-run every conversation in the
    # recording, rated reviews included. A conversation found exactly as before keeps its work.
    P.add_recording(lab["conn"], lab["wav"], "bench")
    lab["run"]()
    conn = lab["conn"]
    rec = rows(conn, "SELECT id FROM recordings")[0]["id"]
    first = {s["id"]: P.current(conn, s["id"], "transcribe")["id"] for s in rows(conn, "SELECT id FROM sessions")}
    CALLS.clear()
    set_stage(lab, "find_conversations", version=2)  # same boundaries
    assert lab["run"]() == {"done": 1, "failed": 0, "skipped": 0, "busy": 0}
    assert [c[0] for c in CALLS] == ["fc"]
    for sid, tr in first.items():
        cur = P.current(conn, sid, "transcribe")
        assert cur["id"] == tr and not cur["stale"]
    CALLS.clear()
    set_stage(lab, "find_conversations", version=3, params={"convs": [[1.0, 3.0], [10.0, 13.0]]})  # the second ends later
    assert lab["run"]() == {"done": 2, "failed": 0, "skipped": 0, "busy": 0}
    assert [c[0] for c in CALLS] == ["fc", "tr"]  # transcribe waited for the new boundaries
    fc = P.current(conn, rec, "find_conversations")
    later = row(conn, "SELECT id FROM sessions WHERE start_s=10.0")["id"]
    assert json.loads(P.current(conn, later, "transcribe")["upstream"]) == [fc["id"]]
    assert lab["run"]() == {"done": 0, "failed": 0, "skipped": 0, "busy": 0}  # and it settles


def test_changed_boundaries_supersede_old_sessions(lab):
    P.add_recording(lab["conn"], lab["wav"], "bench")
    lab["run"]()
    set_stage(lab, "find_conversations", version=2, params={"convs": [[1.0, 3.0], [15.0, 18.0]]})
    lab["run"]()
    got = {r["start_s"]: r["status"] for r in rows(lab["conn"], "SELECT start_s, status FROM sessions")}
    assert got == {1.0: "active", 10.0: "superseded", 15.0: "active"}


def test_failure_is_reported_not_looped(lab):
    P.add_recording(lab["conn"], lab["wav"], "bench")
    set_stage(lab, "transcribe", params={"boom": True})
    counts = lab["run"]()
    assert counts["failed"] == 2  # both conversations tried; neither retried in the same pass
    failed = rows(lab["conn"], "SELECT * FROM stage_runs WHERE status='failed'")
    assert len(failed) == 2 and all("model exploded" in f["error"] for f in failed)
    assert lab["run"]()["failed"] == 0  # too soon to try again


def test_one_failure_does_not_hold_up_the_rest(lab):
    def tr_first_fails(inputs, params):
        if inputs["start_s"] == 1.0:
            raise RuntimeError("timed out")
        return fake_tr(inputs, params)
    set_stage(lab, "transcribe", fn=tr_first_fails)
    P.add_recording(lab["conn"], lab["wav"], "bench")
    assert lab["run"]() == {"done": 2, "failed": 1, "skipped": 0, "busy": 0}  # find + the other conversation


def test_a_failed_run_is_retried_with_a_growing_wait_then_given_up(lab):
    conn = lab["conn"]
    set_stage(lab, "transcribe", fn=lambda i, p: (_ for _ in ()).throw(RuntimeError("x"))
              if i["start_s"] == 1.0 else fake_tr(i, p))
    P.add_recording(conn, lab["wav"], "bench")
    lab["run"]()
    fails = lambda: rows(conn, "SELECT finished_at FROM stage_runs WHERE status='failed'")
    assert len(fails()) == 1
    age = lambda s: conn.execute("UPDATE stage_runs SET finished_at=finished_at-? WHERE status='failed'", (s,))
    age(P.RETRY_AFTER_S + 1)
    lab["run"]()
    assert len(fails()) == 2  # second try after 15 min
    age(P.RETRY_AFTER_S + 1)
    lab["run"]()
    assert len(fails()) == 2  # the third waits twice as long
    age(P.RETRY_AFTER_S)
    lab["run"]()
    assert len(fails()) == 3
    age(10 * 24 * 3600)
    lab["run"]()
    assert len(fails()) == 3  # given up until asked
    assert P.retry(conn) == 3
    lab["run"]()
    assert len(fails()) == 1 and len(rows(conn, "SELECT id FROM stage_runs WHERE status='retried'")) == 3


def test_a_queued_run_with_outdated_inputs_is_not_run(lab):
    """Switching a step's model (or bumping its version) while work is queued: the old runs are
    retired and only the new ones run (2 Oct: six qwen3:8b insight runs queued before the switch)."""
    conn = lab["conn"]
    P.add_recording(conn, lab["wav"], "bench")
    P.plan(conn)  # find_conversations queued with version 1
    set_stage(lab, "find_conversations", version=2)
    counts = lab["run"]()
    assert counts["skipped"] == 1 and counts["done"] == 3  # the v1 run retired; v2 + 2 transcribes ran
    assert {r["status"] for r in rows(conn, "SELECT status FROM stage_runs WHERE stage='find_conversations'")} == {"obsolete", "done"}


def test_crash_recovery_requeues_running(lab):
    conn = lab["conn"]
    P.add_recording(conn, lab["wav"], "bench")
    P.plan(conn)
    conn.execute("UPDATE stage_runs SET status='running'")  # the process died mid-stage
    assert P.recover(conn) == 1
    assert lab["run"]()["done"] == 3


# ---- conversion (the phone-memo path) --------------------------------------------------------

@pytest.mark.skipif(not shutil.which("afconvert"), reason="macOS only")
def test_m4a_converts_to_16k_mono_wav(tmp_path):
    src = tmp_path / "tone.wav"
    t = np.arange(16000 * 2) / 16000
    A.write(src, (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32))
    memo = tmp_path / "memo.m4a"
    subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", str(src), str(memo)], check=True)
    out = tmp_path / "out.wav"
    A.to_wav(memo, out)
    assert A.is_board_wav(out)
    assert abs(A.duration(out) - 2.0) < 0.1


# ---- words → speakers ------------------------------------------------------------------------

from octo.stages import assign_words, utterances


def word(w, s, e, p=0.9):
    return {"w": w, "start": s, "end": e, "p": p}


def test_words_follow_the_speaker_turns():
    turns = [("A", 0.0, 2.0), ("B", 2.1, 4.0)]
    words = [word("hello", 0.1, 0.5), word("there", 0.6, 1.0), word("hi", 2.2, 2.5), word("back", 2.6, 3.0)]
    lines = utterances(assign_words(words, turns))
    assert [(l["speaker"], l["text"]) for l in lines] == [("A", "hello there"), ("B", "hi back")]
    assert not any(l["uncertain"] for l in lines)


def test_word_in_a_small_gap_goes_to_nearest_turn_but_is_marked_unsure():
    turns = [("A", 0.0, 1.0), ("B", 1.5, 3.0)]
    got = assign_words([word("um", 1.05, 1.2)], turns)
    assert got[0]["speaker"] == "A" and got[0]["sure"] is False


def test_word_far_from_any_turn_has_no_speaker():
    got = assign_words([word("hm", 10.0, 10.2)], [("A", 0.0, 1.0)])
    assert got[0]["speaker"] is None


def test_zero_length_word_inside_a_turn_is_that_speakers():
    # deep inside a long turn: 4 s from either edge, which used to leave it with no speaker
    got = assign_words([word("so", 5.0, 5.0)], [("A", 1.0, 9.0)])
    assert got[0]["speaker"] == "A" and got[0]["sure"] is True


FILL = {"fill_min_s": 1.0, "fill_pause_s": 1.0, "fill_min_similarity": 0.5, "fill_min_margin": 0.10}


def test_a_missed_stretch_goes_to_the_speaker_whose_voice_it_matches():
    from octo.stages import _uncovered, fill_by_voice
    words = [{**word("one", 0.2, 0.5), "speaker": "A", "sure": True},
             word("two", 3.0, 3.5), word("three", 3.6, 4.4),  # pyannote heard no one here
             {**word("four", 6.0, 6.4), "speaker": "A", "sure": True}]
    for w in words:
        w.setdefault("speaker", None)
    cents = {"A": vec(1, 0), "B": vec(0, 1)}
    got, filled = fill_by_voice(words, cents, lambda s, e: vec(0.2, 1), FILL)  # sounds like B
    assert [w["speaker"] for w in got] == ["A", "B", "B", "A"]
    assert got[1]["by_voice"] and filled == [("B", 3.0, 4.4)]
    lines = utterances(got)
    assert [l["speaker"] for l in lines] == ["A", "B", "A"] and lines[1]["by_voice"]
    # a filled stretch never overlaps an existing turn
    assert _uncovered([("B", 3.0, 4.4)], [("A", 0.0, 3.2)]) == [("B", 3.2, 4.4)]


def test_a_stretch_that_matches_no_one_clearly_stays_unassigned():
    from octo.stages import fill_by_voice
    words = [{**word("two", 3.0, 3.5), "speaker": None}, {**word("three", 3.6, 4.4), "speaker": None}]
    cents = {"A": vec(1, 0), "B": vec(0, 1)}
    close_call, _ = fill_by_voice(words, cents, lambda s, e: vec(1, 1), FILL)  # equally A and B
    weak, _ = fill_by_voice(words, {"A": vec(1, 0)}, lambda s, e: vec(0.3, 1), FILL)  # similarity 0.29
    short, _ = fill_by_voice(words[:1], cents, lambda s, e: vec(0, 1), FILL)  # 0.5 s: too short to judge
    assert all(w["speaker"] is None for w in close_call + weak + short)


def test_low_probability_words_flag_the_line():
    lines = utterances(assign_words([word("maybe", 0.1, 0.4, p=0.1), word("so", 0.5, 0.6)], [("A", 0.0, 1.0)]))
    assert lines[0]["uncertain"] is True


# ---- voiceprints, naming, D10 ----------------------------------------------------------------

from octo import voice as V
from octo.stages import remap_speakers


def vec(*xs):
    v = np.array(xs, dtype=np.float32)
    return v / np.linalg.norm(v)


PRINTS = [
    {"person_id": "p1", "name": "Rishi", "is_self": True, "embedding": vec(1, 0, 0)},
    {"person_id": "p2", "name": "Anil", "is_self": False, "embedding": vec(0.8, 0.6, 0)},
    {"person_id": "p3", "name": "Amma", "is_self": False, "embedding": vec(0, 0, 1)},
]


def test_clear_match_is_named():
    m = V.match(vec(0, 0.05, 1), PRINTS)
    assert m["name"] == "Amma" and not m["ambiguous"]


def test_close_call_is_ambiguous_not_silently_one_person():
    m = V.match(vec(0.95, 0.3, 0), PRINTS)  # near both Rishi and Anil
    assert m["ambiguous"] and m["name"] in ("Rishi", "Anil") and m["alternatives"]


def test_stranger_is_not_named():
    assert V.match(vec(0, 1, -1), PRINTS) is None


@pytest.mark.parametrize("sims,enrolled,expected", [
    ([0.90], True, True),     # clearly the owner
    ([0.62, 0.1], True, True),  # generous "present"
    ([0.50], True, None),     # in between: keep, flag
    ([0.30, 0.2], True, False),  # clearly absent
    ([0.30], False, None),    # The owner not enrolled: never discard
    ([], True, None),         # too little speech to tell
])
def test_jay_presence_is_asymmetric(sims, enrolled, expected):
    assert V.jay_presence(sims, enrolled) is expected


def test_labels_carry_across_rediarization_by_timing():
    old = [{"speaker": "A", "start": 0, "end": 5}, {"speaker": "B", "start": 5, "end": 9}]
    new = [{"speaker": "A", "start": 0, "end": 4.8}, {"speaker": "C", "start": 4.8, "end": 9}]
    assert remap_speakers(old, new) == {"A": "A", "B": "C"}


def _identify_run(conn, sid, jay, dz_run="dz1", speakers=None):
    out = {"speakers": speakers or [{"speaker": "A", "match": None, "speech_s": 5, "self_similarity": None}],
           "diarize_run": dz_run, "jay_present": jay}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, output, queued_at) "
                 "VALUES (?, 'identify', ?, 'h', 'done', ?, 0)", (f"r{jay}{sid}", sid, json.dumps(out)))
    return row(conn, "SELECT * FROM stage_runs WHERE id=?", f"r{jay}{sid}")


def test_d10_discards_restores_and_respects_keep(lab, monkeypatch):
    monkeypatch.setattr(P, "KEEP_ONLY_JAYS", True)  # the filter as designed (paused for now: see below)
    conn = lab["conn"]
    P.add_recording(conn, lab["wav"], "bench")
    lab["run"]()
    sid = rows(conn, "SELECT id FROM sessions")[0]["id"]
    status = lambda: row(conn, "SELECT status FROM sessions WHERE id=?", sid)["status"]
    P._apply_identify(conn, _identify_run(conn, sid, False))
    assert status() == "discarded"
    P._apply_identify(conn, _identify_run(conn, sid, True))  # better voiceprint later → back
    assert status() == "active"
    conn.execute("UPDATE sessions SET kept=1 WHERE id=?", (sid,))
    P._apply_identify(conn, _identify_run(conn, sid + "x", False) | {"target": sid})
    assert status() == "active"  # The owner said keep: never auto-discarded again


def test_with_d10_paused_nothing_is_discarded_and_what_was_comes_back(lab, monkeypatch):
    conn = lab["conn"]
    P.add_recording(conn, lab["wav"], "bench")
    lab["run"]()
    sid = rows(conn, "SELECT id FROM sessions")[0]["id"]
    monkeypatch.setattr(P, "KEEP_ONLY_JAYS", True)
    P._apply_identify(conn, _identify_run(conn, sid, False))
    assert row(conn, "SELECT status FROM sessions WHERE id=?", sid)["status"] == "discarded"
    monkeypatch.setattr(P, "KEEP_ONLY_JAYS", False)
    P._apply_identify(conn, _identify_run(conn, sid + "x", False) | {"target": sid})
    assert row(conn, "SELECT status, note FROM sessions WHERE id=?", sid) == {"status": "active", "note": P.PAUSED_NOTE}


def test_forget_removes_voiceprints_and_labels(lab):
    conn = lab["conn"]
    P.add_recording(conn, lab["wav"], "bench")
    lab["run"]()
    sid = rows(conn, "SELECT id FROM sessions")[0]["id"]
    amma = V.ensure_person(conn, "Amma")
    V.add_voiceprint(conn, amma["id"], vec(0, 0, 1), 10.0, "test")
    conn.execute("INSERT INTO session_speakers VALUES (?, 'B', ?, 'confirmed', NULL, 'dz')", (sid, amma["id"]))
    before = V.voiceprints_version(conn)
    assert V.forget(conn, "amma")  # case-insensitive
    assert V.voiceprints_version(conn) != before  # identify will re-run everywhere
    assert rows(conn, "SELECT * FROM voiceprints") == []
    assert row(conn, "SELECT state, person_id FROM session_speakers WHERE session_id=?", sid) == {"state": "unknown", "person_id": None}


def test_highpass_removes_mic_drift_but_keeps_voice_band():
    import numpy as np
    from octo import RATE
    from octo import audio as A

    t = np.arange(RATE * 5) / RATE
    drift = 0.3 * np.sin(2 * np.pi * 2 * t)  # the PDM mic's slow wander, < 5 Hz
    voice = 0.01 * np.sin(2 * np.pi * 300 * t)
    y = A.highpass((drift + voice).astype(np.float32))
    mid = slice(RATE, 4 * RATE)  # away from the filter's edges
    assert np.std(y[mid] - voice[mid]) < 0.002  # drift gone
    assert abs(np.std(y[mid]) / np.std(voice[mid]) - 1) < 0.05  # voice untouched


def test_playback_is_brought_up_to_a_listening_level_without_clipping():
    """13 Oct: "hear them" needed the phone at full volume; the recorder's speech is at -44 to -61 dBFS."""
    import numpy as np
    from octo import RATE
    from octo import audio as A

    def loud(x):  # the loud parts, as for_listening measures them
        f = x[:len(x) // 800 * 800].reshape(-1, 800)
        return 20 * np.log10(np.percentile(np.sqrt((f.astype(np.float64) ** 2).mean(1)), 90))

    t = np.arange(RATE * 10) / RATE
    speech = (10 ** (-50 / 20) * np.sqrt(2) * np.sin(2 * np.pi * 300 * t) * (np.sin(2 * np.pi * 0.5 * t) > 0)).astype(np.float32)
    bang = speech.copy()
    bang[5 * RATE:5 * RATE + 400] += 0.2  # a door at -14 dBFS: at +32 dB it would be far over full scale
    for x in (speech, bang):
        y = A.for_listening(x)
        assert len(y) == len(x)  # same timing: line times still point at the same words
        assert abs(loud(y) - A.LISTEN_DBFS) < 1.0
        assert np.abs(y).max() <= A.LISTEN_CEILING + 1e-3
    y = A.for_listening(bang)
    away = slice(2 * RATE, 4 * RATE)  # the speech away from the bang keeps its gain
    assert abs(np.std(y[away]) / np.std(A.for_listening(speech)[away]) - 1) < 0.01
    memo = (0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)  # already loud: never turned down
    assert abs(np.std(A.for_listening(memo)) / np.std(memo) - 1) < 0.01
    assert abs(loud(A.for_listening(speech * 1e-4)) - (-50 - 80 + A.LISTEN_MAX_GAIN_DB)) < 1.0  # near-silence: capped


def _dz(turns, overlapping, lines):
    speakers = sorted({t[0] for t in turns})
    return {"speakers": speakers,
            "talk_s": {s: round(sum(e - b for k, b, e in turns if k == s), 1) for s in speakers},
            "turns": [{"speaker": k, "start": b, "end": e} for k, b, e in turns],
            "overlapping_turns": [{"speaker": k, "start": b, "end": e} for k, b, e in overlapping],
            "lines": [{"speaker": k, "text": t} for k, t in lines]}


def test_numbers_counts_floors_share_and_questions():
    from octo.stages import count

    # A speaks twice in a row (one floor), B answers, A again.
    turns = [("A", 0, 4), ("A", 4.5, 6), ("B", 6.2, 8), ("A", 8.3, 10)]
    n = count(_dz(turns, turns, [("A", "Shall we go on Sunday? Or Saturday?"), ("B", "Sunday works."),
                                 ("A", "Okay.")]), {"min_overlap_s": 0.3})
    a, b = n["speakers"]["A"], n["speakers"]["B"]
    assert (a["turns"], b["turns"]) == (2, 1)
    assert a["longest_turn_s"] == 5.5  # speech only: the 0.5 s pause inside the floor isn't talking
    assert a["questions"] == 2 and b["questions"] == 0
    assert abs(a["share"] + b["share"] - 1) < 0.01
    assert n["speaker_changes"] == 2
    assert a["interrupted_others"] == b["interrupted_others"] == 0  # no overlap, no interruptions


def test_numbers_count_fillers_but_not_backchannels():
    from octo.stages import count

    turns = [("A", 0, 4), ("B", 4.2, 8)]
    n = count(_dz(turns, turns, [("A", "So ah we, you know, kind of um turned it on."),
                                 ("B", "Uh huh. Uh-huh. I mean, like, it works.")]), {"min_overlap_s": 0.3})
    a, b = n["speakers"]["A"], n["speakers"]["B"]
    assert a["fillers"] == 4 and a["fillers_per_100_words"] == round(100 * 4 / 11, 1)  # 11 words
    assert b["fillers"] == 0  # "uh huh" is a backchannel; "I mean" and "like" often carry meaning


def test_numbers_takeover_is_an_interruption_but_backchannel_and_edges_are_not():
    from octo.stages import count

    p = {"min_overlap_s": 0.3}
    # B starts at 5 while A talks until 6, and A stops: B took over -> B interrupted A.
    takeover = [("A", 0, 6), ("B", 5, 9)]
    n = count(_dz([("A", 0, 5), ("B", 5, 9)], takeover, []), p)
    assert n["speakers"]["B"]["interrupted_others"] == 1 and n["speakers"]["A"]["was_interrupted"] == 1
    # B says "mm" at 3-3.5 while A keeps talking to 8: backchannel, not an interruption.
    backchannel = [("A", 0, 8), ("B", 3, 3.5)]
    n = count(_dz([("A", 0, 8), ("B", 3, 3.5)], backchannel, []), p)
    assert n["speakers"]["B"]["interrupted_others"] == 0
    # Overlap of 0.2 s at a normal handover is within turn-edge error: not counted.
    edge = [("A", 0, 5.2), ("B", 5.0, 9)]
    n = count(_dz([("A", 0, 5), ("B", 5, 9)], edge, []), p)
    assert n["speakers"]["B"]["interrupted_others"] == 0


def test_insights_wait_until_jay_is_confirmed_and_no_suggestion_is_pending(lab):
    from octo.planning import settled_speakers as _settled_speakers

    conn = lab["conn"]
    P.add_recording(conn, lab["wav"], "bench")
    lab["run"]()
    sid = rows(conn, "SELECT id FROM sessions")[0]["id"]
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('dz1', 'diarize', ?, 'h', 'done', 1, ?, 0)", (sid, json.dumps({"speakers": ["A", "B", "C"]})))
    jay, nisha = V.ensure_person(conn, "Sam", is_self=True), V.ensure_person(conn, "Nisha")
    put = lambda spk, pid, state: conn.execute("INSERT OR REPLACE INTO session_speakers VALUES (?, ?, ?, ?, NULL, 'dz1')",
                                               (sid, spk, pid, state))
    put("A", nisha["id"], "suggested")
    put("B", jay["id"], "suggested")
    put("C", None, "unknown")
    assert _settled_speakers(conn, sid) is None  # nothing confirmed
    put("B", jay["id"], "confirmed")
    assert _settled_speakers(conn, sid) is None  # "Nisha?" still waiting for an answer
    put("A", nisha["id"], "confirmed")
    got = _settled_speakers(conn, sid)
    assert got == [{"speaker": "A", "name": "Nisha", "self": False}, {"speaker": "B", "name": "Sam", "self": True},
                   {"speaker": "C", "name": "Speaker C", "self": False}]  # an unnamed voice doesn't block
    conn.execute("UPDATE session_speakers SET diarize_run='old' WHERE speaker='B'")
    assert _settled_speakers(conn, sid) is None  # labels from an older diarize run don't count


def test_lines_break_at_pauses_and_after_long_sentences():
    from octo.stages import utterances

    ws = [word("one", 0, 0.3), word("two.", 0.4, 0.7), word("three", 2.0, 2.3)]  # 1.3 s pause
    assert [l["text"] for l in utterances([{**w, "speaker": "A", "sure": True} for w in ws])] == ["one two.", "three"]
    long = [{**word(f"w{i}" + ("." if i == 31 else ""), i * 0.3, i * 0.3 + 0.2), "speaker": "A", "sure": True}
            for i in range(40)]
    lines = utterances(long)
    assert len(lines) == 2 and lines[0]["text"].endswith("w31.")  # waits for the sentence to end


def test_lab_api_lists_conversations_with_what_is_left_to_do(lab, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import octo.api as API

    conn = lab["conn"]
    tmp = P.HOME
    import octo.privacy as PV
    lab["mp"].setattr(API, "DATA", tmp / "data")
    lab["mp"].setattr(PV, "HOME", tmp)
    P.add_recording(conn, lab["wav"], "phone")
    lab["run"]()
    sid = rows(conn, "SELECT id FROM sessions ORDER BY start_s")[0]["id"]
    dz = {"speakers": ["A", "B"], "talk_s": {"A": 6.0, "B": 4.0},
          "turns": [{"speaker": "A", "start": 0.0, "end": 6.0}, {"speaker": "B", "start": 6.0, "end": 10.0}],
          "lines": [{"id": 0, "speaker": "A", "start": 0.0, "end": 6.0, "text": "Shall we go to Mysuru on Saturday then?", "uncertain": False},
                    {"id": 1, "speaker": "B", "start": 6.0, "end": 10.0, "text": "Saturday works.", "uncertain": False}]}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('dz1', 'diarize', ?, 'h', 'done', 1, ?, 0)", (sid, json.dumps(dz)))
    jay, nisha = V.ensure_person(conn, "Sam", is_self=True), V.ensure_person(conn, "Nisha")
    conn.execute("INSERT INTO session_speakers VALUES (?, 'A', ?, 'suggested', 0.8, 'dz1')", (sid, nisha["id"]))
    conn.execute("INSERT INTO session_speakers VALUES (?, 'B', ?, 'confirmed', NULL, 'dz1')", (sid, jay["id"]))

    kicks = []
    app = FastAPI()
    app.include_router(API.make_router(lambda: connect(tmp / "t.db"),  # one connection per request, as in production
                                       processor=type("P", (), {"kick": lambda self: kicks.append(1)})()))
    c = TestClient(app)
    listed = {s["id"]: s for s in c.get("/api/octo/sessions").json()}
    s = listed[sid]
    assert s["todo"] == {"kind": "confirm"}
    assert [sp["name"] for sp in s["speakers"]] == ["Nisha?", "Sam"]
    assert s["preview"] == "Shall we go to Mysuru on Saturday then?"

    conn.execute("UPDATE session_speakers SET state='confirmed' WHERE speaker='A'")
    assert c.get("/api/octo/sessions").json()[0]["todo"]["kind"] in ("processing", "confirm")  # sorted newest first
    assert listed and c.get(f"/api/octo/sessions/{sid}").json()["todo"]["stage"] == "insight"
    finding = {"principle": 8, "title": "t", "observation": "o", "quotes": [], "unverified_quotes": 0, "flags": [],
               "try_next_time": "Ask first."}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('in1', 'insight', ?, 'h', 'done', 1, ?, 0)",
                 (sid, json.dumps({"model": "m", "rubric": "r", "skipped": None, "conduct": [finding], "no_moment": [],
                                   "dropped": [], "beneath": [], "seconds": 1.0})))
    d = c.get(f"/api/octo/sessions/{sid}").json()
    assert d["todo"] == {"kind": "review"}  # one question waits (since 6 Oct)
    jay = d["speakers"][1].pop("person_id")
    assert jay.startswith("per_")  # his page (octo/person.py)
    assert d["speakers"][1] == {"letter": "B", "name": "Sam", "state": "confirmed", "self": True, "alternatives": [],
                                "talk_s": 4.0, "share": 0.4}
    assert len(d["lines"]) == 2 and d["insights"]["conduct"][0]["principle"] == 8
    found = c.get("/api/octo/search", params={"q": "saturday", "who": "you"}).json()["results"]  # B12
    assert [(r["line"], r["who"]) for r in found] == [(1, "Sam")]
    assert c.get("/api/octo/search", params={"q": "saturday", "who": "everyone"}).status_code == 422

    r = c.get(f"/api/octo/sessions/{sid}/audio", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    assert c.get("/api/octo/sessions/ses_20990101_deadbeef").status_code == 404
    assert c.get("/api/octo/sessions/..%2F..%2Fetc").status_code == 404


    # --- writes (U2) ---
    post = lambda path, body, **h: c.post(path, json=body, headers=h)
    conn.execute("UPDATE session_speakers SET state='suggested' WHERE speaker='A'")
    assert post(f"/api/octo/sessions/{sid}/speakers/A", {"action": "confirm"}, origin="https://evil.example").status_code == 403
    assert c.post(f"/api/octo/sessions/{sid}/speakers/A", content='{"action":"confirm"}',
                  headers={"content-type": "text/plain"}).status_code == 415  # what a cross-site form can send
    assert post(f"/api/octo/sessions/{sid}/speakers/Z", {"action": "confirm"}).status_code == 404
    r = post(f"/api/octo/sessions/{sid}/speakers/A", {"action": "reject"})
    assert r.status_code == 200 and r.json()["speakers"][0]["state"] == "unknown" and kicks == [1]
    P._apply_identify(conn, _identify_run(conn, sid, True, dz_run="dz1", speakers=[
        {"speaker": "A", "match": {"person_id": nisha["id"], "name": "Nisha", "similarity": 0.8, "ambiguous": False},
         "speech_s": 6, "self_similarity": 0.1}]))
    assert row(conn, "SELECT state FROM session_speakers WHERE speaker='A'")["state"] == "rejected"  # stays rejected
    assert post(f"/api/octo/sessions/{sid}/speakers/A", {"action": "confirm"}).status_code == 409
    assert post(f"/api/octo/sessions/{sid}/speakers/A", {"action": "name", "name": "<script>"}).status_code == 422

    # The answer is saved at once; the voiceprint follows in the background (9 Oct: a refresh while it was
    # being made asked the same question again).
    ran, done = [], threading.Event()
    def fake_run(cmd, **kw):
        ran.append(cmd[3:])
        done.set()
        return subprocess.CompletedProcess(cmd, 0, "", "")
    with monkeypatch.context() as m:
        m.setattr(API.subprocess, "run", fake_run)
        r = post(f"/api/octo/sessions/{sid}/speakers/A", {"action": "name", "name": "Asha"})
        assert r.status_code == 200 and r.json()["speakers"][0]["state"] == "confirmed"
        assert row(conn, "SELECT p.name FROM session_speakers ss JOIN people p ON p.id = ss.person_id "
                         "WHERE ss.speaker='A'")["name"] == "Asha"
        assert done.wait(5) and ran == [["label", sid, "A", "Asha", "--print-only"]]

    assert post("/api/octo/people/forget", {"name": "Nisha", "confirm": "Gunj"}).status_code == 422
    assert post("/api/octo/people/forget", {"name": "Sam", "confirm": "sam"}).status_code == 409
    assert post("/api/octo/people/forget", {"name": "Nisha", "confirm": "nisha"}).json() == {"forgotten": "Nisha"}
    assert V.person_by_name(conn, "Nisha") is None

    # --- rating insights (U5) ---
    fb = lambda body: post(f"/api/octo/sessions/{sid}/feedback", {"run": "in1", **body})
    assert fb({"item": "p8", "verdict": "maybe"}).status_code == 422
    assert fb({"item": "p3", "verdict": "right"}).status_code == 404  # not an insight in this run
    assert post(f"/api/octo/sessions/{sid}/feedback", {"run": "dz1", "item": "p8"}).status_code == 404
    r = fb({"item": "p8", "verdict": "right", "novelty": "new"}).json()
    assert r["todo"] == {"kind": "review"}  # rated item by item (the rule before 6 Oct): one left
    fb({"item": "t8", "verdict": "partly"})
    r = fb({"item": "t8", "novelty": "knew", "acted": True, "note": "Asked first on Wednesday."}).json()
    assert r["todo"] == {"kind": "reviewed"}
    assert r["feedback"]["t8"] == {"verdict": "partly", "novelty": "knew", "useful": None, "acted": True,
                                   "note": "Asked first on Wednesday.", "let_go": False}
    assert row(conn, "SELECT reviewed_at FROM sessions WHERE id=?", sid)["reviewed_at"] is not None
    assert c.get("/api/octo/progress").json()["counts"]["checks"]["acted"]["value"] == 1

    # --- fixing who said what (U2b) ---
    r = post(f"/api/octo/sessions/{sid}/lines/0", {"to": "B"}).json()
    assert r["lines"][0]["speaker"] == "B" and r["corrections"] == 1
    assert [sp["letter"] for sp in r["speakers"]] == ["B"]  # A had only that line
    assert post(f"/api/octo/sessions/{sid}/lines/99", {"to": "B"}).status_code == 404
    assert post(f"/api/octo/sessions/{sid}/corrections/undo", {}).json()["corrections"] == 0
    assert post(f"/api/octo/sessions/{sid}/corrections/undo", {}).status_code == 404
    r = post(f"/api/octo/sessions/{sid}/speakers/A/merge", {"into": "B"}).json()
    assert [sp["letter"] for sp in r["speakers"]] == ["B"] and r["turns"] == [{"speaker": "B", "start": 0.0, "end": 10.0}]
    assert post(f"/api/octo/sessions/{sid}/speakers/B/merge", {"into": "B"}).status_code == 404

    # --- privacy ---
    assert post(f"/api/octo/sessions/{sid}/exclude", {"letter": "B", "left_out": True}).status_code == 409  # that's the owner
    assert post(f"/api/octo/sessions/{sid}/delete", {"confirm": "yes"}).status_code == 422
    assert post(f"/api/octo/sessions/{sid}/delete", {"confirm": "delete", "work": True}).json() == {"deleted": sid, "parts": [sid]}
    assert c.get(f"/api/octo/sessions/{sid}").status_code == 404
    assert sid not in {s["id"] for s in c.get("/api/octo/sessions").json()}

def test_only_one_runner_at_a_time(lab):
    import fcntl

    P.add_recording(lab["conn"], lab["wav"], "bench")
    with open(P._lock_path(lab["conn"]), "w") as held:  # e.g. `octo process` in a terminal
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert lab["run"]() == {"done": 0, "failed": 0, "skipped": 0, "busy": 1}
        assert CALLS == []  # nothing ran twice
    assert lab["run"]()["done"] == 3  # the lock is free again



def test_upload_adds_a_recording_once_and_shows_it_incoming(lab):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import octo.api as API

    tmp = P.HOME
    lab["mp"].setattr(API, "DATA", tmp / "data")
    kicks = []
    app = FastAPI()
    app.include_router(API.make_router(lambda: connect(tmp / "t.db"),
                                       processor=type("P", (), {"kick": lambda self: kicks.append(1)})()))
    c = TestClient(app)
    body = lab["wav"].read_bytes()
    up = lambda name="memo.wav", **h: c.post(f"/api/octo/recordings?name={name}&source=phone&modified=1790000000000",
                                              content=body, headers={"x-octo-upload": "1", **h})
    assert c.post("/api/octo/recordings?name=memo.wav", content=body).status_code == 403  # no custom header
    assert up(origin="https://evil.example").status_code == 403
    assert up(name="notes.pdf").status_code == 415
    r = up()
    assert r.status_code == 200 and r.json()["started_at"] == 1790000000  # the browser's file date (no MP4 header)
    assert kicks == [1]
    assert up().status_code == 409  # the same memo twice
    assert list((tmp / "data" / "uploads").iterdir()) == []  # temporary upload removed
    inc = c.get("/api/octo/incoming").json()
    assert [(i["name"], i["state"]) for i in inc] == [("memo.wav", "processing")]
    lab["run"]()  # the pipeline finds its conversations
    assert c.get("/api/octo/incoming").json() == []  # it has conversations now
    import octo.privacy as PV
    lab["mp"].setattr(PV, "HOME", tmp)
    lab["mp"].setattr(PV, "AUDIO", tmp / "audio")
    conn = connect(tmp / "t.db")
    for sess in rows(conn, "SELECT id FROM sessions"):
        PV.delete_session(conn, sess["id"])
    assert c.get("/api/octo/incoming").json() == []  # deleted on purpose: not "no conversation found"
    st = c.get("/api/octo/status").json()
    assert st.pop("free_gb") > 0
    assert st.pop("privacy")["review"]  # what the privacy line names (test_providers has the cases)
    assert st == {"processing": False, "current": None, "queued": 0, "code_changed": False, "cant_save": None,
                  "queue_empty_at": None, "queue_steps": 0}


def test_a_run_deleted_while_it_runs_is_dropped_not_a_crash(lab):
    P.add_recording(lab["conn"], lab["wav"], "bench")
    conn = lab["conn"]

    vanished = []

    def execute_then_vanish(run_id):  # The owner deletes the conversation mid-run, once
        if not vanished:
            vanished.append(run_id)
            conn.execute("DELETE FROM stage_runs WHERE id=?", (run_id,))
            return 0, ""
        conn.execute("UPDATE stage_runs SET status='done', output=? WHERE id=?",
                     (json.dumps(lab["stages"][row(conn, "SELECT stage FROM stage_runs WHERE id=?", run_id)["stage"]].fn(
                         W.inputs_for(conn, row(conn, "SELECT * FROM stage_runs WHERE id=?", run_id)), {})), run_id))
        return 0, ""

    counts = P.run_pending(conn, log=lambda _: None, execute=execute_then_vanish)
    assert counts["skipped"] == 1 and counts["failed"] == 0 and counts["done"] == 3  # re-planned and carried on


def test_a_failed_step_shows_as_failed_not_forever_processing(lab):
    from octo.sessions import todo

    conn = lab["conn"]
    P.add_recording(conn, lab["wav"], "bench")
    lab["run"]()
    sid = rows(conn, "SELECT id FROM sessions")[0]["id"]
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, error, queued_at) "
                 "VALUES ('x1', 'insight', ?, 'h', 'failed', 0, 'URLError: <urlopen error [Errno 61] Connection refused>', ?)",
                 (sid, time.time()))
    t = todo(conn, sid)
    assert t["kind"] == "failed" and t["stage"] == "insight" and "Ollama" in t["hint"] and t["auto_retry"] is True


def test_rejecting_or_relabelling_a_speaker_takes_its_voiceprint_with_it(lab):
    conn = lab["conn"]
    nisha = V.ensure_person(conn, "Nisha")
    V.add_voiceprint(conn, nisha["id"], vec(1, 0, 0), 30.0, "label:ses_x:A")  # actually her mother
    V.add_voiceprint(conn, nisha["id"], vec(0, 1, 0), 30.0, "label:ses_y:B")
    assert V.drop_label(conn, "ses_x", "A") == 1
    assert [r["source"] for r in rows(conn, "SELECT source FROM voiceprints")] == ["label:ses_y:B"]


def test_rename_a_person_everywhere_or_merge_two_who_are_the_same(lab):
    conn = lab["conn"]
    mom = V.ensure_person(conn, "Nisha's Mom")
    V.add_voiceprint(conn, mom["id"], vec(1, 0, 0), 30.0, "label:s1:A")
    assert V.rename(conn, "nisha's mom", "Asha")[0] == "renamed"
    assert V.person_by_name(conn, "Asha")["id"] == mom["id"] and not V.person_by_name(conn, "Nisha's Mom")
    twin = V.ensure_person(conn, "Asha Aunty")
    V.add_voiceprint(conn, twin["id"], vec(0, 1, 0), 20.0, "label:s2:B")
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('r2', 'phone', 'x', 9, 0)")
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('s2', 'r2', 0, 9, 5, 0)")
    conn.execute("INSERT INTO session_speakers VALUES ('s2', 'B', ?, 'confirmed', NULL, 'dz')", (twin["id"],))
    assert V.rename(conn, "Asha Aunty", "Asha")[0] == "merged"
    assert not V.person_by_name(conn, "Asha Aunty")
    assert len(rows(conn, "SELECT * FROM voiceprints WHERE person_id=?", mom["id"])) == 2
    assert row(conn, "SELECT person_id FROM session_speakers WHERE session_id='s2'")["person_id"] == mom["id"]


def test_quick_earlier_steps_run_before_a_long_insight_queued_first(tmp_path):
    conn = connect(tmp_path / "q.db")  # the real pipeline's stage order, not the lab fixture's
    for rid, stage, at in (("i1", "insight", 1.0), ("d1", "identify", 2.0), ("d2", "identify", 3.0)):
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, queued_at) VALUES (?, ?, 's', 'h', 'queued', ?)",
                     (rid, stage, at))
    assert P.next_run(conn)["id"] == "d1"


def test_voiceprints_from_another_model_are_never_compared_and_can_be_rebuilt(lab, monkeypatch):
    import numpy as np
    from octo import audio as A
    conn = lab["conn"]
    jay = V.ensure_person(conn, "Sam", is_self=True)
    V.add_voiceprint(conn, jay["id"], vec(1, 0, 0), 30.0, "enrol:enrol_jay.wav")
    V.add_voiceprint(conn, jay["id"], vec(0, 1, 0), 9.0, "label:gone:A")
    before = V.voiceprints_version(conn)
    monkeypatch.setattr(V, "EMBEDDING_MODEL", "some/newer-model")
    assert V.all_voiceprints(conn) == [] and V.voiceprints_version(conn) != before  # identify re-runs, matches nothing old

    root = P.HOME
    A.write(root / "audio" / "enrol_jay.wav", np.full(16000 * 10, 0.1, np.float32))
    monkeypatch.setattr(V, "speech_only", lambda x: x)
    fake = type("E", (), {"embed": lambda self, x: vec(0, 0, 1)})()
    r = V.reprint(conn, root, embedder=fake)
    assert r == {"rebuilt": ["enrol:enrol_jay.wav"], "missing": ["label:gone:A"]}
    assert [round(float(v["embedding"][2]), 2) for v in V.all_voiceprints(conn)] == [1.0]
    assert V.reprint(conn, root, embedder=fake)["rebuilt"] == []  # already done


def test_language_comes_from_the_speechiest_windows_and_only_jays_languages():
    import numpy as np
    from octo.asr import louder, speech_windows, vote_language
    spans = [[0.0, 0.5], [40.0, 59.0], [61.0, 89.0], [130.0, 131.0]]
    assert speech_windows(spans, n=2) == [60.0, 30.0]  # not the near-silent opening
    # Norwegian can win a single window of quiet audio; it can't win here.
    lang, votes = vote_language([{"nn": 0.6, "en": 0.3}, {"en": 0.9}, {"hi": 0.5, "en": 0.4}], ["en", "hi", "kn"])
    assert lang == "en" and "nn" not in votes
    quiet = (np.sin(np.arange(16000 * 2) * 2 * np.pi * 200 / 16000) * 0.003).astype(np.float32)  # about -54 dBFS
    x = louder(quiet, [], {"highpass_hz": 80.0, "normalize_dbfs": -20.0, "max_gain_db": 40.0})
    assert abs(20 * np.log10(np.sqrt(np.mean(x[1600:-1600] ** 2))) + 20) < 1.5


def test_short_voiceprints_are_ignored_and_each_person_stays_bounded(lab):
    import numpy as np
    conn = lab["conn"]
    g = V.ensure_person(conn, "Nisha")
    V.add_voiceprint(conn, g["id"], vec(1, 0, 0), 3.5, "label:a:A")  # a few seconds: unreliable
    assert V.all_voiceprints(conn) == []
    rng = np.random.default_rng(0)
    for i in range(V.MAX_PRINTS + 3):
        V.add_voiceprint(conn, g["id"], rng.normal(size=8).astype(np.float32), 20.0, f"label:s{i}:A")
    V.add_voiceprint(conn, g["id"], rng.normal(size=8).astype(np.float32), 30.0, "enrol:g.wav")
    assert V.prune(conn, g["id"]) == 4
    kept = rows(conn, "SELECT source FROM voiceprints WHERE person_id=? AND seconds >= ?", g["id"], V.MIN_PRINT_S)
    assert len(kept) == V.MAX_PRINTS and "enrol:g.wav" in {k["source"] for k in kept}


def test_the_server_says_when_its_code_is_older_than_the_files():
    from octo.api import code_changed
    assert code_changed(since=0) is True
    assert code_changed(since=time.time() + 3600) is False


def test_identify_leaves_alone_a_conversation_where_jay_answered_for_everyone(lab):
    from octo.planning import voiceprints_input as _voiceprints_version
    conn = lab["conn"]
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('rec_1', 'file', 'x.wav', 60, ?)", (time.time(),))
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('ses_1', 'rec_1', 0, 60, 50, 0)")
    dz = {"speakers": ["A", "B"], "turns": [{"speaker": "A", "start": 0, "end": 5}, {"speaker": "B", "start": 5, "end": 9}],
          "lines": [], "talk_s": {"A": 5, "B": 4}}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('dz1', 'diarize', 'ses_1', 'h', 'done', 1, ?, 0)", (json.dumps(dz),))
    old = {"voiceprints": "old-set", "match": [0.6, 0.15]}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, upstream, params, queued_at) "
                 "VALUES ('id1', 'identify', 'ses_1', 'h2', 'done', 1, '[\"dz1\"]', ?, 0)", (json.dumps(old),))
    jay = V.ensure_person(conn, "Sam")
    conn.execute("INSERT INTO session_speakers VALUES ('ses_1', 'A', ?, 'confirmed', 0.9, 'dz1')", (jay["id"],))
    conn.execute("INSERT INTO session_speakers VALUES ('ses_1', 'B', NULL, 'unknown', NULL, 'dz1')")
    assert _voiceprints_version(conn, "ses_1")["voiceprints"] != "old-set"  # B could still be named
    conn.execute("UPDATE recordings SET created_at=? WHERE id='rec_1'", (time.time() - 20 * 86400,))
    assert _voiceprints_version(conn, "ses_1") == old  # 20 days old: keeps its answers (M11)
    conn.execute("UPDATE recordings SET created_at=? WHERE id='rec_1'", (time.time(),))
    conn.execute("UPDATE session_speakers SET state='rejected' WHERE speaker='B'")
    assert _voiceprints_version(conn, "ses_1") == old  # every speaker answered: nothing to re-run
    conn.execute("INSERT INTO corrections VALUES ('fix1', 'ses_1', 'dz1', 'merge', '{\"from\": \"B\", \"into\": \"A\"}', 0)")
    conn.execute("DELETE FROM session_speakers WHERE speaker='B'")
    assert _voiceprints_version(conn, "ses_1") == old  # B merged into A: still everyone


def test_identify_waits_while_jay_is_naming_people_and_skips_fragments(lab):
    from octo.planning import REIDENTIFY_SETTLE_S, voiceprints_input, voiceprints_settle_at
    conn = lab["conn"]
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('rec_1', 'file', 'x.wav', 60, ?)", (time.time(),))
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('ses_1', 'rec_1', 0, 60, 50, 0)")
    dz = {"speakers": ["A"], "turns": [{"speaker": "A", "start": 0, "end": 5}], "lines": [], "talk_s": {"A": 5}}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('dz1', 'diarize', 'ses_1', 'h', 'done', 1, ?, 0)", (json.dumps(dz),))
    old = {"voiceprints": "old-set", "match": [0.6, 0.15]}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, upstream, params, queued_at) "
                 "VALUES ('id1', 'identify', 'ses_1', 'h2', 'done', 1, '[\"dz1\"]', ?, 0)", (json.dumps(old),))
    g = V.ensure_person(conn, "Nisha")
    V.add_voiceprint(conn, g["id"], vec(1, 0, 0), 3.5, "label:a:A")  # too short to count: doesn't hold anything back
    assert voiceprints_settle_at(conn) is None and P.next_due(conn) is None
    V.add_voiceprint(conn, g["id"], vec(1, 0, 0), 20.0, "label:a:B")
    settle = voiceprints_settle_at(conn)
    assert settle is not None and abs(P.next_due(conn) - settle) < 1  # the server wakes when it settles
    assert voiceprints_input(conn, "ses_1") == old  # just named: wait for the next name
    after = settle + 1
    assert voiceprints_settle_at(conn, after) is None
    conn.execute("UPDATE voiceprints SET created_at=created_at-?", (REIDENTIFY_SETTLE_S + 1,))
    assert voiceprints_input(conn, "ses_1")["voiceprints"] != "old-set"  # settled: B could now be named
    conn.execute("UPDATE sessions SET speech_s=12 WHERE id='ses_1'")
    assert voiceprints_input(conn, "ses_1") == old  # a fragment keeps its answers


def test_an_insight_run_gets_what_earlier_runs_of_that_conversation_worked_out(tmp_path):
    conn = connect(tmp_path / "m.db")
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('rec_1', 'file', 'x.wav', 60, 0)")
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('ses_1', 'rec_1', 0, 60, 50, 0)")
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('ses_2', 'rec_1', 70, 90, 10, 0)")
    for rid, target, memo, at in [("i1", "ses_1", {"k1": 1, "k2": 1}, 1), ("i2", "ses_1", {"k2": 2}, 2), ("i3", "ses_2", {"k9": 9}, 3)]:
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, output, queued_at, finished_at) "
                     "VALUES (?, 'insight', ?, 'h', 'done', ?, 0, ?)", (rid, target, json.dumps({"memo": memo}), at))
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, queued_at) VALUES ('new', 'insight', 'ses_1', 'h2', 'queued', 0)")
    got = W.inputs_for(conn, row(conn, "SELECT * FROM stage_runs WHERE id='new'"))
    assert got["memo"] == {"k1": 1, "k2": 2}  # newest wins; never another conversation's


# ---- speakers and who-said-what as two stages (A2, 2 Oct) -------------------------------------

def fake_sp(inputs, params):
    CALLS.append(("sp", str(inputs["start_s"])))
    return {"model": "fake", "clustering_threshold": 0.7, "speakers": ["A"],
            "turns": [{"speaker": "A", "start": 0.0, "end": 2.0}], "overlapping_turns": []}


def fake_dz(inputs, params):
    CALLS.append(("dz", str(inputs["start_s"])))
    assert set(inputs["upstream"][0]) >= {"segments"} and set(inputs["upstream"][1]) >= {"turns"}  # transcribe, then speakers
    return {"speakers": ["A"], "turns": [], "lines": []}


def test_a_new_transcription_redoes_who_said_what_but_not_the_speakers(lab):
    lab["stages"]["speakers"] = Stage("speakers", 1, "session", "find_conversations", {}, fake_sp)
    lab["stages"]["diarize"] = Stage("diarize", 1, "session", ("transcribe", "speakers"), {}, fake_dz)
    P.add_recording(lab["conn"], lab["wav"], "bench")
    assert lab["run"]()["done"] == 7  # find + 2 × (transcribe, speakers, diarize)
    CALLS.clear()
    set_stage(lab, "transcribe", version=2)
    lab["run"]()
    assert sorted(c[0] for c in CALLS) == ["dz", "dz", "tr", "tr"]
    CALLS.clear()
    set_stage(lab, "speakers", version=2)
    lab["run"]()
    assert sorted(c[0] for c in CALLS) == ["dz", "dz", "sp", "sp"]


def test_who_said_what_builds_lines_from_the_transcript_and_the_speaker_turns(tmp_path):
    from octo.stages import diarize
    wav = tmp_path / "c.wav"
    A.write(wav, np.zeros(16000 * 10, np.float32))
    tr = {"segments": [{"words": [word("hello", 0.5, 0.9), word("there", 1.0, 1.4),
                                  word("missed", 6.0, 6.5), word("bit", 6.6, 7.4)]}]}
    sp = {"model": "pyannote/x", "clustering_threshold": 0.705, "speakers": ["A", "B"],
          "turns": [{"speaker": "A", "start": 0.0, "end": 2.0}, {"speaker": "B", "start": 3.0, "end": 5.0}],
          "overlapping_turns": [{"speaker": "A", "start": 0.0, "end": 2.0}, {"speaker": "B", "start": 3.0, "end": 5.0}]}

    class Emb:  # B's turn and the missed stretch sound alike
        def embed_turns(self, samples, turns):
            return (vec(1, 0) if turns[0][0] < 1 else vec(0, 1)), 2.0

        def embed(self, samples):
            return vec(0.1, 1)

    out = diarize({"path": wav, "start_s": 0.0, "end_s": 10.0, "upstream": [tr, sp]}, STAGES["diarize"].params, embedder=Emb)
    assert [(ln["speaker"], ln["text"]) for ln in out["lines"]] == [("A", "hello there"), ("B", "missed bit")]
    assert out["lines"][1]["by_voice"] and out["model"] == "pyannote/x"
    assert {"speaker": "B", "start": 6.0, "end": 7.4} in out["turns"] and out["talk_s"] == {"A": 2.0, "B": 3.4}


def test_jays_fixes_carry_to_a_new_diarize_run_by_timing(lab):
    from octo import corrections as C
    conn = lab["conn"]
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('rec_1', 'file', 'x.wav', 60, 0)")
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('ses_1', 'rec_1', 0, 60, 50, 0)")
    line = lambda i, spk, s, e: {"id": i, "speaker": spk, "start": s, "end": e, "text": "x"}
    old = {"turns": [{"speaker": "A", "start": 0, "end": 10}, {"speaker": "B", "start": 10, "end": 20},
                     {"speaker": "C", "start": 20, "end": 30}],
           "lines": [line(0, "A", 0, 10), line(1, "B", 10, 20), line(2, "C", 20, 30)]}
    # The new run named them in a different order and split the first line in two.
    new = {"turns": [{"speaker": "B", "start": 0, "end": 10}, {"speaker": "A", "start": 10, "end": 20},
                     {"speaker": "C", "start": 20, "end": 30}],
           "lines": [line(0, "B", 0, 4), line(1, "B", 4, 10), line(2, "A", 10, 20), line(3, "C", 20, 30)]}
    for rid, out in (("dz_old", old), ("dz_new", new)):
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, output, queued_at) "
                     "VALUES (?, 'diarize', 'ses_1', ?, 'done', ?, 0)", (rid, rid, json.dumps(out)))
    C.add(conn, "ses_1", "dz_old", "merge", {"from": "C", "into": "B"})
    C.add(conn, "ses_1", "dz_old", "line", {"line": 1, "to": "A"})  # line 1 is still one line
    C.add(conn, "ses_1", "dz_old", "line", {"line": 0, "to": "B"})  # line 0 was split: no counterpart
    C.add(conn, "ses_1", "dz_old", "exclude", {"letter": "C"})
    assert C.carry(conn, "ses_1", "dz_old", "dz_new") == 3
    got = [(f["kind"], {k: v for k, v in f.items() if k not in ("id", "kind")}) for f in C.for_run(conn, "ses_1", "dz_new")]
    assert got == [("merge", {"from": "C", "into": "A"}), ("line", {"line": 2, "to": "B"}), ("exclude", {"letter": "C"})]
    assert C.carry(conn, "ses_1", "dz_old", "dz_new") == 0  # never twice


# ---- recordings stored as FLAC (A5, 2 Oct) ---------------------------------------------------

def test_flac_and_wav_hold_the_same_samples_and_rewrites_change_nothing(tmp_path):
    rng = np.random.default_rng(0)
    pcm = rng.integers(-32768, 32767, 16000 * 3, dtype=np.int16)
    for name in ("a.wav", "a.flac"):
        A.write_pcm(tmp_path / name, pcm)
        assert np.array_equal(A.read_pcm(tmp_path / name), pcm)
        assert np.array_equal(A.read_pcm(tmp_path / name, 1.0, 2.0), pcm[16000:32000])  # a slice, by seeking
        assert A.duration(tmp_path / name) == 3.0
        A.write(tmp_path / name, A.read(tmp_path / name))  # read then write: the same samples (not 32767/32768 quieter)
        assert np.array_equal(A.read_pcm(tmp_path / name), pcm)


def test_a_new_recording_is_stored_as_flac(lab):
    rec = P.add_recording(lab["conn"], lab["wav"], "bench")
    path = row(lab["conn"], "SELECT path FROM recordings WHERE id=?", rec)["path"]
    assert path.endswith(".flac") and np.array_equal(A.read_pcm(P.HOME / path), A.read_pcm(lab["wav"]))


def test_compact_audio_swaps_wav_for_identical_flac(lab):
    conn = lab["conn"]
    rng = np.random.default_rng(1)
    wav = P.HOME / "audio" / "recordings" / "rec_old.wav"
    A.write_pcm(wav, rng.integers(-2000, 2000, 16000 * 2, dtype=np.int16))
    before = A.read_pcm(wav)
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('rec_old', 'file', ?, 2, 0)",
                 (str(wav.relative_to(P.HOME)),))
    with open(P._lock_path(conn), "w") as held:  # the pipeline is busy: leave the files alone
        import fcntl
        fcntl.flock(held, fcntl.LOCK_EX)
        assert P.compact_audio(conn, log=lambda _: None)["busy"] == 1 and wav.exists()
    got = P.compact_audio(conn, log=lambda _: None)
    assert got["converted"] == 1 and not wav.exists()
    path = P.HOME / row(conn, "SELECT path FROM recordings WHERE id='rec_old'")["path"]
    assert path.suffix == ".flac" and np.array_equal(A.read_pcm(path), before)
    assert P.compact_audio(conn, log=lambda _: None)["converted"] == 0  # nothing left to do


def test_a_worker_that_crashes_after_saving_keeps_its_result(lab):
    """2 Oct: onnxruntime's telemetry thread crashed `speakers` workers on exit, after the output
    was saved; the runner used to throw the result away and retry."""
    P.add_recording(lab["conn"], lab["wav"], "bench")
    conn = lab["conn"]

    def execute_then_crash(run_id):
        run = row(conn, "SELECT * FROM stage_runs WHERE id=?", run_id)
        out = lab["stages"][run["stage"]].fn(W.inputs_for(conn, run), json.loads(run["params"]))
        conn.execute("UPDATE stage_runs SET status='done', output=? WHERE id=?", (json.dumps(out), run_id))
        return -11, "Segmentation fault: 11"  # died during teardown

    assert P.run_pending(conn, log=lambda _: None, execute=execute_then_crash)["done"] == 3
    assert not rows(conn, "SELECT * FROM stage_runs WHERE status='failed'")


def test_onnxruntime_telemetry_is_off_in_every_octo_process():
    """It queues usage events and uploads them to Microsoft; nothing should leave the Mac."""
    import os

    import octo  # noqa: F401
    assert os.environ["ORT_DISABLE_TELEMETRY"] == "1"


def test_an_embedding_coreml_refuses_is_made_on_the_cpu(monkeypatch):
    """8-9 Oct: CoreML refused one speaker's length ("Error in dynamically resizing for sequence
    length") and that conversation's identify failed six times."""
    import onnxruntime as ort
    from octo import voice as V

    class Refuses:
        def run(self, *a):
            raise RuntimeError("[ONNXRuntimeError] : 1 : FAIL : CoreMLExecutionProvider_0 Error in dynamically resizing")

    class Cpu:
        def __init__(self, path, providers):
            assert providers == ["CPUExecutionProvider"]

        def run(self, *a):
            return [np.ones((1, 4), dtype=np.float32)]

    e = V.Embedder.__new__(V.Embedder)
    e._onnx, e._cpu, e._file, e._input = Refuses(), None, "model.onnx", "feats"
    monkeypatch.setattr(ort, "InferenceSession", Cpu)
    monkeypatch.setattr(V, "wespeaker_fbank", lambda s: s)
    assert e.embed(np.zeros(16000, dtype=np.float32)).tolist() == [1, 1, 1, 1]
    assert isinstance(e._cpu, Cpu)


def test_a_log_that_only_grows_is_cut_to_its_last_lines(tmp_path):  # audit 14 Oct
    from octo import files as FL
    log = tmp_path / "process.log"
    FL.trim_log(log, keep=100)  # no log yet: nothing to do
    log.write_text("".join(f"line {i}\n" for i in range(20)))  # 150 bytes: not yet twice `keep`
    FL.trim_log(log, keep=100)
    assert log.read_text().startswith("line 0\n")
    log.write_text("".join(f"line {i}\n" for i in range(100)))
    FL.trim_log(log, keep=100)
    text = log.read_text()
    assert text.endswith("line 99\n") and text.startswith("line ") and len(text) <= 100  # whole lines only


def test_a_login_items_log_is_cut_in_place_so_launchd_keeps_writing_to_it(tmp_path):  # architecture review 10, 28 Oct
    from octo import files as FL
    log = tmp_path / "lab.log"
    log.write_text("".join(f"line {i}\n" for i in range(100)))
    writer = open(log, "a")  # as launchd holds it: open for append the whole time
    inode = log.stat().st_ino
    FL.trim_log(log, keep=100, held=True)
    writer.write("after\n")
    writer.close()
    text = log.read_text()
    assert log.stat().st_ino == inode and text.startswith("line ") and text.endswith("line 99\nafter\n") and len(text) <= 106
