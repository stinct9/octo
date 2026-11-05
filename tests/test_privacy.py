"""Deleting parts, conversations and old audio (D5): gone, not hidden."""

import time

import numpy as np
import pytest

from octo import audio as A
from octo import pipeline as P
from octo import privacy as PV
from octo.db import connect, row, rows
from tests.calls import spend


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(PV, "HOME", tmp_path)
    monkeypatch.setattr(PV, "AUDIO", tmp_path / "audio")
    conn = connect(tmp_path / "p.db")
    rec = tmp_path / "rec.wav"
    A.write(rec, np.full(16000 * 20, 0.1, np.float32))  # 20 s of "sound"
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('r1', 'phone', 'rec.wav', 20, ?)",
                 (time.time(),))
    for sid, a, b in (("s1", 0, 10), ("s2", 10, 20)):
        conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES (?, 'r1', ?, ?, 5, 0)", (sid, a, b))
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                     "VALUES (?, 'insight', ?, 'h', 'done', 1, '{}', 0)", (f"in_{sid}", sid))
        conn.execute("INSERT INTO feedback (insight_run, item, session_id, verdict, updated_at) VALUES (?, 'p8', ?, 'right', 0)", (f"in_{sid}", sid))
    conn.execute("INSERT INTO people (id, name, created_at) VALUES ('g', 'Nisha', 0)")
    for sid in ("s1", "s2"):
        conn.execute("INSERT INTO voiceprints (id, person_id, embedding, seconds, source, created_at) VALUES (?, 'g', x'00', 5, ?, 0)",
                     (f"vp_{sid}", f"label:{sid}:A"))
    return conn, rec


def test_deleting_part_silences_only_that_part_and_drops_every_derived_version(lab):
    conn, rec = lab
    PV.delete_range(conn, "s2", 2.0, 4.0)  # 12..14 s into the recording
    x = A.read(rec)
    assert not np.any(x[12 * 16000:14 * 16000]) and np.all(x[:12 * 16000] > 0) and np.all(x[14 * 16000:] > 0)
    assert rows(conn, "SELECT id FROM stage_runs WHERE target='s2'") == []
    assert rows(conn, "SELECT * FROM feedback WHERE session_id='s2'") == []
    assert row(conn, "SELECT status FROM sessions WHERE id='s2'")["status"] == "active"  # redone from what's left
    assert row(conn, "SELECT id FROM stage_runs WHERE target='s1'")  # the other conversation is untouched
    with pytest.raises(ValueError):
        PV.delete_range(conn, "s2", 5.0, 5.0)


def test_two_deletes_in_one_recording_at_once_both_stay_silent(lab, monkeypatch):  # audit 14 Oct
    import threading
    conn, rec = lab
    monkeypatch.setattr(PV, "DATA", rec.parent)
    write = A.write
    monkeypatch.setattr(A, "write", lambda *a, **k: (time.sleep(0.3), write(*a, **k)))  # both read before either writes
    ts = [threading.Thread(target=PV._silence, args=(rec, a, b)) for a, b in ((2, 4), (12, 14))]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    x = A.read(rec)
    assert not np.any(x[2 * 16000:4 * 16000]) and not np.any(x[12 * 16000:14 * 16000])


def test_a_deleted_part_keeps_left_out_speakers_out_and_the_conversation_out_of_the_gate_count(lab):
    import json

    from octo import corrections as C
    conn, rec = lab
    turns = lambda *t: [{"speaker": k, "start": a, "end": b} for k, a, b in t]
    old = {"speakers": ["A", "B", "C"], "turns": turns(("A", 0, 4), ("B", 4, 7), ("C", 7, 10)), "lines": []}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('dz_old', 'diarize', 's2', 'h', 'done', 1, ?, 0)", (json.dumps(old),))
    C.add(conn, "s2", "dz_old", "merge", {"from": "B", "into": "C"})
    C.add(conn, "s2", "dz_old", "exclude", {"letter": "C"})  # B and C are one person, left out
    conn.execute("UPDATE sessions SET reviewed_at=1 WHERE id='s2'")
    PV.delete_range(conn, "s2", 0.5, 1.0)
    assert row(conn, "SELECT reviewed_at FROM sessions WHERE id='s2'")["reviewed_at"] is None  # its ratings are gone
    # Worked out again from the audio that's left: new letters.
    new = {"speakers": ["A", "B"], "turns": turns(("B", 0, 4), ("A", 4, 10)), "lines": []}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('dz_new', 'diarize', 's2', 'h2', 'done', 1, ?, 0)", (json.dumps(new),))
    assert C.adopt_left_out(conn, "s2", "dz_new") == 1
    assert [f["letter"] for f in C.for_run(conn, "s2", "dz_new") if f["kind"] == "exclude"] == ["A"]
    assert row(conn, "SELECT left_out_spans FROM sessions WHERE id='s2'")["left_out_spans"] is None


def test_deleting_a_conversation_takes_its_voiceprints_too(lab):
    conn, rec = lab
    PV.delete_session(conn, "s1", work=True)
    x = A.read(rec)
    assert not np.any(x[:10 * 16000]) and np.all(x[10 * 16000:] > 0)
    assert row(conn, "SELECT status, note FROM sessions WHERE id='s1'") == {"status": "deleted", "note": "work: purged"}
    assert [v["id"] for v in rows(conn, "SELECT id FROM voiceprints")] == ["vp_s2"]
    assert rows(conn, "SELECT * FROM feedback WHERE session_id='s1'") == []


def test_old_raw_audio_goes_after_the_retention_period_and_is_never_re_listened_to(lab):
    conn, rec = lab
    assert PV.RAW_AUDIO_DAYS == 60  # 1 Oct 2026 (was 14)
    assert PV.drop_old_audio(conn) == []  # added just now
    assert PV.drop_old_audio(conn, now=time.time() + 59 * 86400) == []
    assert PV.drop_old_audio(conn, now=time.time() + 61 * 86400) == ["r1"]
    assert not rec.exists()
    assert row(conn, "SELECT audio_deleted_at FROM recordings WHERE id='r1'")["audio_deleted_at"]
    assert P.plan(conn) == []  # nothing that needs the audio is queued for it


# ---- B29: a lab that keeps its recordings (23 Oct) --------------------------------------

def test_a_lab_that_keeps_recordings_keeps_them_past_60_days_and_still_sweeps_the_rest(lab, monkeypatch):
    from octo import labconfig as LC
    conn, rec = lab
    monkeypatch.setitem(LC.TEST_OVERRIDES, "keep_recordings", True)
    old = time.time() - 61 * 86400
    PV.BENCH.mkdir(parents=True)
    stale = PV.BENCH / "clip.wav"
    stale.write_bytes(b"x")
    import os
    os.utime(stale, (old, old))
    assert PV.drop_old_audio(conn, now=time.time() + 400 * 86400) == []
    assert rec.exists() and not row(conn, "SELECT audio_deleted_at FROM recordings WHERE id='r1'")["audio_deleted_at"]
    assert not stale.exists()  # bench output still goes at 60 days


def test_a_new_lab_keeps_d5s_60_days(lab, monkeypatch):
    from octo import labconfig as LC
    conn, _ = lab
    monkeypatch.delitem(LC.TEST_OVERRIDES, "keep_recordings")
    assert LC.flag(conn, "keep_recordings") is False  # no reviews before the product: a buyer's lab
    assert PV.drop_old_audio(conn, now=time.time() + 61 * 86400) == ["r1"]


def _diarized_with_nisha(conn):
    """s2 (10-20 s of the recording): the owner (A) at 1-3 s, Nisha (B) at 4-6 s and 7-8 s."""
    import json
    lines = [{"id": 0, "speaker": "A", "start": 1.0, "end": 3.0, "text": "hello"},
             {"id": 1, "speaker": "B", "start": 4.0, "end": 6.0, "text": "hi"},
             {"id": 2, "speaker": "B", "start": 7.0, "end": 8.0, "text": "bye"}]
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('dz_s2', 'diarize', 's2', 'h', 'done', 1, ?, 0)",
                 (json.dumps({"lines": lines, "speakers": ["A", "B"], "talk_s": {"A": 2.0, "B": 3.0}, "turns": []}),))
    conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES ('me', 'Sam', 1, 0)")
    conn.execute("INSERT INTO session_speakers (session_id, speaker, person_id, state, diarize_run) VALUES "
                 "('s2', 'A', 'me', 'confirmed', 'dz_s2'), ('s2', 'B', 'g', 'confirmed', 'dz_s2')")


def test_forgetting_someone_in_a_lab_that_keeps_recordings_silences_their_lines_only(lab, monkeypatch):
    from octo import labconfig as LC
    from octo.voice import forget
    conn, rec = lab
    monkeypatch.setitem(LC.TEST_OVERRIDES, "keep_recordings", True)
    _diarized_with_nisha(conn)
    assert forget(conn, "Nisha")
    x = A.read(rec)
    sec = lambda a, b: x[int(a * 16000):int(b * 16000)]
    assert not np.any(sec(14, 16)) and not np.any(sec(17, 18))  # her two lines
    assert np.all(sec(11, 13) > 0) and np.all(sec(0, 10) > 0)  # the owner's, and the other conversation
    assert not row(conn, "SELECT 1 FROM people WHERE id='g'")


def test_a_forget_whose_silencing_fails_leaves_them_findable_to_forget_again(lab, monkeypatch):  # audit 28 Oct
    from octo import labconfig as LC
    from octo.voice import forget
    conn, rec = lab
    monkeypatch.setitem(LC.TEST_OVERRIDES, "keep_recordings", True)
    _diarized_with_nisha(conn)
    real = PV.silence_spans
    monkeypatch.setattr(PV, "silence_spans", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        forget(conn, "Nisha")
    assert row(conn, "SELECT 1 FROM people WHERE id='g'") and row(conn, "SELECT 1 FROM voiceprints WHERE person_id='g'")
    assert row(conn, "SELECT 1 FROM session_speakers WHERE person_id='g'")
    monkeypatch.setattr(PV, "silence_spans", real)
    assert forget(conn, "Nisha")
    assert not np.any(A.read(rec)[14 * 16000:16 * 16000])


def test_forgetting_someone_where_audio_goes_at_60_days_leaves_the_audio(lab):
    from octo.voice import forget
    conn, rec = lab
    _diarized_with_nisha(conn)
    before = A.read(rec)
    assert forget(conn, "Nisha")
    assert np.array_equal(A.read(rec), before)


# ---- 4 Oct review, batch C ------------------------------------------------------------------

def test_after_a_boundary_moves_no_clip_keeps_a_deleted_conversations_audio(lab):  # M12
    conn, rec = lab
    s1 = row(conn, "SELECT s.*, r.path FROM sessions s JOIN recordings r ON r.id = s.recording_id WHERE s.id='s1'")
    wide = PV.clip({**s1, "end_s": 20.0})  # cut before a split moved s1's end from 20 s to 10 s
    assert len(A.read(wide)) == 20 * 16000
    narrow = PV.clip(s1)
    assert narrow != wide and len(A.read(narrow)) == 10 * 16000  # new bounds, new clip
    PV.delete_session(conn, "s2")  # the second half: no clip of the recording may still hold it
    assert list(PV.CLIPS.glob("*.wav")) == []


def test_unplayed_clips_go_after_a_week_and_played_ones_stay(lab):  # M13
    conn, _ = lab
    s1 = row(conn, "SELECT s.*, r.path FROM sessions s JOIN recordings r ON r.id = s.recording_id WHERE s.id='s1'")
    path = PV.clip(s1)
    assert PV.drop_idle_clips(now=time.time() + 6 * 86400) == 0 and path.exists()
    assert PV.drop_idle_clips(now=time.time() + 8 * 86400) == 1 and not path.exists()


def test_a_clip_is_cut_louder_to_listen_to_and_the_recording_stays_as_it_was(lab):  # 13 Oct
    conn, rec = lab
    t = np.arange(16000 * 20) / 16000
    A.write(rec, (0.003 * np.sin(2 * np.pi * 300 * t)).astype(np.float32))  # -50 dBFS, as the recorder hears a table
    kept = A.read(rec)
    s1 = row(conn, "SELECT s.*, r.path FROM sessions s JOIN recordings r ON r.id = s.recording_id WHERE s.id='s1'")
    clip = A.read(PV.clip(s1))
    assert len(clip) == 10 * 16000
    assert np.std(clip[16000:-16000]) > 20 * np.std(kept[16000:9 * 16000])
    assert np.array_equal(A.read(rec), kept)


def test_the_60_day_rule_also_takes_stray_recorder_chunks_and_bench_output(lab):  # M15
    conn, _ = lab
    old = time.time() - 61 * 86400
    chunk = PV.HOME / "audio" / "device" / "b00001" / "s00001.wav"
    chunk.parent.mkdir(parents=True)
    chunk.write_bytes(b"x")
    conn.execute("INSERT INTO device_chunks (boot, seq, seg, start_ms, samples, sha256, path, pulled_at) "
                 "VALUES (1, 1, 0, 0, 1, 'h', 'audio/device/b00001/s00001.wav', ?)", (old,))
    PV.BENCH.mkdir(parents=True)
    stale, fresh = PV.BENCH / "clip.wav", PV.BENCH / "new.wav"
    stale.write_bytes(b"x")
    fresh.write_bytes(b"x")
    import os
    os.utime(stale, (old, old))
    PV.drop_old_audio(conn)
    assert not chunk.exists() and row(conn, "SELECT recording_id FROM device_chunks")["recording_id"] == "expired"
    assert not stale.exists() and fresh.exists()


def test_a_deleted_conversations_review_still_counts_in_the_day(lab):  # audit 28 Oct
    conn, _ = lab
    spend(conn, 1)  # its review's call: a row of its own in model_calls, not read from the run
    today = P._midnight(time.time())
    assert P.claude_calls_since(conn, today) == 1
    PV.delete_session(conn, "s1")
    assert not row(conn, "SELECT 1 FROM stage_runs WHERE id='in_s1'")
    assert P.claude_calls_since(conn, today) == 1
