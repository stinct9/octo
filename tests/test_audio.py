"""Audio analysis on synthetic recordings shaped like a real cued take."""

import array
import math
import random
import wave

from bench import audio
from bench.steps.mic import evaluate_recording, evaluate_selftest

RATE = 16000


def make_wav(path, segments):
    """segments: list of (seconds, amplitude, kind) with kind 'noise' or 'tone'."""
    rng = random.Random(1)
    a = array.array("h")
    for seconds, amp, kind in segments:
        for i in range(int(seconds * RATE)):
            v = amp * math.sin(2 * math.pi * 440 * i / RATE) if kind == "tone" else rng.gauss(0, amp)
            a.append(max(-32768, min(32767, int(v))))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(a.tobytes())
    return path


def cued_take(tmp_path, cue=True, speech_amp=600, clip=False, speech_s=6.2):
    segs = [(3.05, 100, "noise")]  # fan-only
    if cue:
        segs.append((0.3, 3000, "tone"))  # Glass cue from the Mac speaker
    segs.append((0.45 if cue else 0.75, 100, "noise"))
    segs.append((speech_s, 40000 if clip else speech_amp, "noise"))  # "speech"
    return make_wav(tmp_path / "take.wav", segs)


def test_cued_take_analyses_cleanly(tmp_path):
    a = audio.analyse(cued_take(tmp_path))
    assert abs(a["seconds"] - 10.0) < 0.01
    assert a["clipped"] == 0
    assert a["cue_at"] is not None and 2.9 <= a["cue_at"] <= 3.2
    assert a["snr_db"] > 10
    assert evaluate_recording(a, 9989).status == "pass"


def test_enrolment_take_is_judged_on_its_own_length(tmp_path):
    a = audio.analyse(cued_take(tmp_path, speech_s=31.2))  # bench record: 35 s
    assert abs(a["seconds"] - 35.0) < 0.01
    assert evaluate_recording(a, 35004, seconds=35).status == "pass"
    # a 10 s take where 35 s was asked for is a failure, not a pass
    short = audio.analyse(cued_take(tmp_path))
    assert evaluate_recording(short, 10000, seconds=35).status == "fail"
    # the board's own timing is checked against 35 s, not 10 s
    timing = lambda ms: next(m for m in evaluate_recording(a, ms, seconds=35).metrics if m.key == "record_ms")
    assert timing(35004).verdict == "pass" and timing(10000).verdict == "warn"


def test_capture_rejects_lengths_the_firmware_cant_record():
    import pytest
    with pytest.raises(ValueError):
        audio.capture(None, None, lambda *a: None, seconds=20)


def test_missing_cue_warns(tmp_path):
    a = audio.analyse(cued_take(tmp_path, cue=False))
    assert a["cue_at"] is None
    o = evaluate_recording(a, 10000)
    assert o.status == "warn" and "muted" in o.hint


def test_low_snr_warns(tmp_path):
    a = audio.analyse(cued_take(tmp_path, speech_amp=150))
    assert a["snr_db"] < 10
    assert evaluate_recording(a, 10000).status == "warn"


def test_clipping_warns(tmp_path):
    a = audio.analyse(cued_take(tmp_path, clip=True))
    assert a["clipped"] > 0
    assert evaluate_recording(a, 10000).status == "warn"


def test_dropout_fails(tmp_path):
    path = make_wav(tmp_path / "d.wav", [(4, 500, "noise"), (0.1, 0, "noise"), (5.9, 500, "noise")])
    a = audio.analyse(path)
    assert a["longest_flat_ms"] >= 99
    assert evaluate_recording(a, 10000).status == "fail"


def test_empty_wav_does_not_crash(tmp_path):
    a = audio.analyse(make_wav(tmp_path / "e.wav", []))
    assert a["empty"] and evaluate_recording(a, None).status == "fail"


def test_selftest_verdicts():
    good = {"samples": 32000, "empty_blocks": 0, "dbfs_min": -75, "dbfs_max": -58, "longest_run": 8}
    assert evaluate_selftest(good) is None
    assert evaluate_selftest(dict(good, samples=0)).status == "fail"
    assert evaluate_selftest(dict(good, longest_run=1600)).status == "fail"
    assert evaluate_selftest({"error": "i2s_begin_failed"}).status == "fail"


def test_board_wavs_bypass_pyav(tmp_path):
    """faster-whisper 1.2.1 + PyAV 19 can't decode files; our 16 kHz mono WAVs must go in as samples."""
    import numpy as np
    x = audio._whisper_input(make_wav(tmp_path / "w.wav", [(1, 1000, "noise")]))
    assert isinstance(x, np.ndarray) and x.dtype == np.float32 and len(x) == RATE
    assert np.abs(x).max() <= 1.0
    other = tmp_path / "memo.m4a"
    other.write_bytes(b"not a wav")
    assert audio._whisper_input(other) == str(other)  # other formats still go via PyAV


def test_recorded_at_reads_the_mp4_header_not_the_file_date(tmp_path):
    import struct

    from octo.audio import recorded_at

    when = 1601038888  # 2020-09-25, the kind of date a voice memo carries
    mvhd = b"mvhd" + bytes([0, 0, 0, 0]) + struct.pack(">I", when + 2082844800) + b"\0" * 16
    f = tmp_path / "memo.m4a"
    f.write_bytes(b"\0\0\0\x20ftypM4A " + b"\0" * 20 + mvhd)
    assert recorded_at(f) == when
    (tmp_path / "zero.m4a").write_bytes(b"mvhd" + b"\0" * 20)  # 1904: not a real date
    assert recorded_at(tmp_path / "zero.m4a") is None
    assert recorded_at(tmp_path / "missing.m4a") is None
