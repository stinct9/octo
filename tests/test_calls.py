"""B19: calls on the Mac, two channels (the mic is the owner, the call app the others). Synthetic audio only."""

import json

import numpy as np
import pytest

from octo import audio as A
from octo import calls as C
from octo import pipeline as P
from octo.db import connect, rows
from octo.stages import STAGES, diarize, identify

RATE = 16000


def two_channels(path, mic, app):
    A.write_pcm(path, np.stack([mic, app], 1).astype(np.int16))


def tone(seconds, hz, level=8000):
    t = np.arange(int(seconds * RATE)) / RATE
    return (level * np.sin(2 * np.pi * hz * t)).astype(np.int16)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "HOME", tmp_path)
    monkeypatch.setattr(P, "AUDIO", tmp_path / "audio")
    return connect(tmp_path / "c.db")


def test_a_call_keeps_both_channels_and_reads_as_one_mix_unless_a_channel_is_asked_for(lab, tmp_path):
    src = tmp_path / "call.wav"
    mic, app = tone(2, 300), tone(2, 700, 4000)
    two_channels(src, mic, app)
    rec = P.add_recording(lab, src, "call", original="call.wav (on headphones)")
    path = tmp_path / rows(lab, "SELECT path FROM recordings WHERE id=?", rec)[0]["path"]
    assert path.suffix == ".flac" and A.channels(path) == 2
    assert np.array_equal(A.read_pcm(path, channel=C.MIC), mic) and np.array_equal(A.read_pcm(path, channel=C.APP), app)
    assert A.read_pcm(path).ndim == 1 and abs(int(A.read_pcm(path)[100]) - round((int(mic[100]) + int(app[100])) / 2)) <= 1
    assert A.read_pcm(path, 0.5, 1.0, "all").shape == (8000, 2) and A.duration(path) == 2.0


def test_a_call_with_one_channel_is_refused_and_other_recordings_stay_mono(lab, tmp_path):
    mono = tmp_path / "memo.wav"
    A.write_pcm(mono, tone(1, 300))
    with pytest.raises(ValueError, match="two channels"):
        P.add_recording(lab, mono, "call")
    rec = P.add_recording(lab, mono, "phone")
    path = tmp_path / rows(lab, "SELECT path FROM recordings WHERE id=?", rec)[0]["path"]
    assert A.channels(path) == 1 and np.array_equal(A.read_pcm(path), tone(1, 300))  # bit for bit, as before
    assert A.read_pcm(path, channel=1).ndim == 1  # a channel asked of a mono file is the file


def test_a_stereo_memo_is_still_mixed_down_to_mono(lab, tmp_path):
    src = tmp_path / "stereo.wav"
    two_channels(src, tone(1, 300), tone(1, 300))
    rec = P.add_recording(lab, src, "phone")
    assert A.channels(tmp_path / rows(lab, "SELECT path FROM recordings WHERE id=?", rec)[0]["path"]) == 1


def test_deleting_part_of_a_call_silences_both_channels(lab, tmp_path):
    from octo import privacy as PV
    src = tmp_path / "call.wav"
    two_channels(src, tone(2, 300), tone(2, 700))
    path = tmp_path / rows(lab, "SELECT path FROM recordings WHERE id=?", P.add_recording(lab, src, "call"))[0]["path"]
    PV._silence(path, 0.5, 1.0)
    x = A.read_pcm(path, channel="all")
    assert x.shape == (32000, 2) and not x[8000:16000].any() and x[:8000].any() and x[16000:].any()


def test_the_echo_canceller_takes_the_others_out_of_the_mic_and_leaves_the_owner(lab, tmp_path):
    """Speakers: the others reach the mic 60 ms late at -14 dB (the spike). Made up: the far end is noise for 20 s,
    then the owner alone (another noise) for 6 s."""
    rng = np.random.default_rng(7)
    far = np.concatenate([rng.normal(0, 3000, 20 * RATE), np.zeros(6 * RATE)])
    owner = np.concatenate([np.zeros(20 * RATE), rng.normal(0, 3000, 6 * RATE)])
    echo = np.concatenate([np.zeros(960), far[:-960]]) * 10 ** (-14 / 20)
    src = tmp_path / "speakers.wav"
    two_channels(src, np.clip(owner + echo, -32768, 32767), np.clip(far, -32768, 32767))
    rec = P.add_recording(lab, src, "call", speakers=True)
    out = A.read_pcm(tmp_path / rows(lab, "SELECT path FROM recordings WHERE id=?", rec)[0]["path"], channel="all")
    db = lambda x: 10 * np.log10(np.mean(x.astype(np.float64) ** 2) + 1e-9)
    before = np.clip(owner + echo, -32768, 32767)
    assert db(out[10 * RATE:19 * RATE, C.MIC]) - db(before[10 * RATE:19 * RATE]) < -10  # the others, once it has converged
    assert abs(db(out[21 * RATE:26 * RATE, C.MIC]) - db(before[21 * RATE:26 * RATE])) < 1.5  # the owner, untouched
    assert np.array_equal(out[:, C.APP], np.clip(far, -32768, 32767).astype(np.int16))  # the app channel as it was


def word(w, a, b, ch=None):
    return {"w": w, "start": a, "end": b, "p": 0.9, **({"ch": ch} if ch is not None else {})}


def test_a_call_is_diarized_with_the_mic_as_the_owner_and_pyannote_only_on_the_others(tmp_path):
    wav = tmp_path / "c.flac"
    two_channels(wav, np.zeros(10 * RATE), np.zeros(10 * RATE))
    tr = {"segments": [
        {"ch": 0, "words": [word("Hi", 0.5, 0.8, 0), word("Nisha.", 0.9, 1.3, 0), word("sure", 2.57, 2.9, 0),
                            word("Okay.", 7.0, 7.4, 0)]},
        {"ch": 1, "words": [word("Hello!", 1.6, 2.0, 1), word("Sure", 2.5, 2.8, 1), word("thing.", 2.9, 3.3, 1)]}],
        "rejected_as_silence": [{"ch": 0, "start": 8.0, "end": 8.5, "words": [word("you", 8.0, 8.5, 0)]}]}
    sp = {"model": "pyannote/x", "clustering_threshold": 0.705, "owner": "A", "speakers": ["A", "B"],
          "turns": [{"speaker": "B", "start": 1.5, "end": 5.0}],
          "overlapping_turns": [{"speaker": "B", "start": 1.5, "end": 5.0}]}
    out = diarize({"path": wav, "start_s": 0.0, "end_s": 10.0, "upstream": [tr, sp]}, STAGES["diarize"].params)
    assert [(ln["speaker"], ln["text"]) for ln in out["lines"]] == [
        ("A", "Hi Nisha."), ("B", "Hello! Sure thing."), ("A", "Okay.")]  # "sure" was B's, heard through the speakers
    assert out["echo_dropped"] == 1 and out["owner"] == "A"
    assert {"speaker": "A", "start": 0.5, "end": 1.3} in out["turns"] and out["talk_s"]["A"] == 1.2
    assert C.channel(out, "A") == C.MIC and C.channel(out, "B") == C.APP and C.channel({"speakers": ["A"]}, "A") is None


def test_words_from_the_mix_go_to_the_louder_channel(tmp_path):  # a Hindi or mixed call is transcribed from the mix
    wav = tmp_path / "c.flac"
    two_channels(wav, np.concatenate([tone(2, 300), np.zeros(2 * RATE)]), np.concatenate([np.zeros(2 * RATE), tone(2, 500)]))
    tr = {"segments": [{"words": [word("namaste", 0.5, 1.0), word("ji", 2.5, 3.0)]}]}
    sp = {"model": "m", "clustering_threshold": 0.7, "owner": "A", "speakers": ["A", "B"],
          "turns": [{"speaker": "B", "start": 2.0, "end": 4.0}], "overlapping_turns": []}
    out = diarize({"path": wav, "start_s": 0.0, "end_s": 4.0, "upstream": [tr, sp]}, STAGES["diarize"].params)
    assert [(ln["speaker"], ln["text"]) for ln in out["lines"]] == [("A", "namaste"), ("B", "ji")]


def test_identify_marks_the_mic_as_the_owner_and_finds_them_present(tmp_path):
    dz = {"owner": "A", "speakers": ["A", "B"], "turns": [{"speaker": "A", "start": 0, "end": 4},
                                                          {"speaker": "B", "start": 5, "end": 9}]}
    out = identify({"path": tmp_path / "x.flac", "start_s": 0, "end_s": 9, "upstream": [dz], "upstream_ids": ["dz"],
                    "voiceprints": []}, {})
    assert out["speakers"][0]["owner_channel"] and "owner_channel" not in out["speakers"][1]
    assert out["jay_present"] is True


def _seed(conn, match, self_person=True):
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('r', 'call', 'x', 9, 0)")
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('s', 'r', 0, 9, 5, 0)")
    if self_person:
        conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES ('me', 'Sam', 1, 0)")
    conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES ('g', 'Nisha', 0, 0)")
    dz = {"owner": "A", "speakers": ["A", "B"], "turns": [], "lines": []}
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES ('dz', 'diarize', 's', 'h', 'done', 1, ?, 0)", (json.dumps(dz),))
    out = {"diarize_run": "dz", "jay_present": True, "speakers": [{"speaker": "A", "match": match, "owner_channel": True},
                                                                  {"speaker": "B", "match": None}]}
    P._apply_identify(conn, {"target": "s", "output": json.dumps(out)})
    return {r["speaker"]: (r["person_id"], r["state"]) for r in rows(conn, "SELECT * FROM session_speakers")}


def m(pid, sim, margin=0.5):
    return {"person_id": pid, "name": pid, "similarity": sim, "margin": margin, "ambiguous": False}


def test_the_mic_is_seeded_as_the_owner_without_asking(tmp_path):
    assert _seed(connect(tmp_path / "1.db"), None) == {"A": ("me", "auto"), "B": (None, "unknown")}  # voice unknown
    assert _seed(connect(tmp_path / "2.db"), m("me", 0.4))["A"] == ("me", "auto")  # a weak match is still the mic
    assert _seed(connect(tmp_path / "3.db"), m("g", 0.55))["A"] == ("me", "auto")  # not surely someone else
    # someone else on the owner's Mac, surely: asked about as usual
    assert _seed(connect(tmp_path / "4.db"), m("g", 0.85))["A"] == ("g", "suggested")
    # a new lab whose owner isn't named yet: nothing to seed
    assert _seed(connect(tmp_path / "5.db"), None, self_person=False)["A"] == (None, "unknown")


def test_drop_echo_needs_the_same_word_at_the_same_moment():
    others = [word("budget", 1.0, 1.4)]
    kept, dropped = C.drop_echo([word("budget", 1.06, 1.46), word("budget", 3.0, 3.4), word("yes", 1.0, 1.2)], others)
    assert [w["start"] for w in dropped] == [1.06] and len(kept) == 2


def test_the_transcript_of_a_call_is_each_channel_on_its_own_merged_by_time(tmp_path, monkeypatch):
    import mlx.core as mx
    import mlx_whisper.audio as WA
    from mlx_whisper.transcribe import ModelHolder

    from octo import stages as S

    wav = tmp_path / "c.flac"
    two_channels(wav, tone(10, 300), tone(10, 600))

    class Model:
        dims = type("D", (), {"n_mels": 128})()

        def detect_language(self, mel):
            return None, {"en": 0.9}

    monkeypatch.setattr(ModelHolder, "get_model", lambda *a, **k: Model())
    monkeypatch.setattr(WA, "log_mel_spectrogram", lambda *a, **k: mx.zeros((3000, 128)))
    monkeypatch.setattr(WA, "pad_or_trim", lambda x, *a, **k: x)
    heard = []

    def qwen(samples, spans, lang, p):  # each channel alone: which one, by its tone
        hz = np.argmax(np.abs(np.fft.rfft(samples[:RATE])))
        heard.append(hz)
        at = 1.0 if hz == 300 else 2.0
        return [{"id": 0, "start": at, "end": at + 0.5, "text": "x", "words": [word("x", at, at + 0.5)]}], []

    monkeypatch.setattr(S, "qwen_words", qwen)
    inputs = {"path": wav, "start_s": 0.0, "end_s": 10.0, "upstream": [{"conversations": [{"start": 0.0, "speech": [[0.5, 9.5]]}]}]}
    out = S.transcribe(inputs, {**S.STAGES["transcribe"].params})
    assert heard == [300, 600]
    assert [(s["ch"], s["id"], s["words"][0]["ch"]) for s in out["segments"]] == [(0, 0, 0), (1, 1, 1)]


def test_pyannote_hears_only_the_others_channel_and_names_them_after_the_owner(tmp_path, monkeypatch):
    import pyannote.audio as PA
    from pyannote.core import Annotation, Segment

    from octo.stages import speakers

    wav = tmp_path / "c.flac"
    two_channels(wav, tone(5, 300), tone(5, 600))
    heard = {}

    class Pipe:
        def to(self, device):
            pass

        def parameters(self, instantiated=True):
            return {"clustering": {"threshold": 0.7}}

        def instantiate(self, p):
            pass

        def __call__(self, x):
            heard["hz"] = int(np.argmax(np.abs(np.fft.rfft(x["waveform"][0].numpy()[:RATE]))))
            ann = Annotation()
            ann[Segment(1, 2)], ann[Segment(3, 4)] = "S0", "S1"
            return type("Out", (), {"speaker_diarization": ann, "exclusive_speaker_diarization": ann})()

    monkeypatch.setattr(PA.Pipeline, "from_pretrained", lambda *a, **k: Pipe())
    out = speakers({"path": wav, "start_s": 0.0, "end_s": 5.0}, STAGES["speakers"].params)
    assert heard["hz"] == 600 and out["owner"] == "A" and out["speakers"] == ["A", "B", "C"]
    assert [t["speaker"] for t in out["turns"]] == ["B", "C"]
