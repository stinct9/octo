"""Does Silero (find conversations) miss speech that pyannote hears? On the owner's own recordings.

    python -m bench.vadcompare run       # both detectors over every recording, timings only
    python -m bench.vadcompare report
    python -m bench.vadcompare clips     # pyannote-only stretches as WAVs, to listen to

`run` keeps only speech spans (seconds) under data/vadcompare/, never audio or words. Silero runs
exactly as find_conversations does (same params, 10-minute blocks). pyannote is segmentation-3.0's
voice-activity pipeline with no smoothing (min on/off 0), on the raw audio (what diarize gets) and
on the 80 Hz high-passed audio, since the board mic's < 5 Hz drift is known to upset detectors.
Neither is ground truth: a stretch only one of them hears could be speech the other missed, or
noise it mistook for speech. `clips` is how to tell which.
"""

from __future__ import annotations

import json
import os
import sys
import wave
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from octo import DATA, DB_PATH as DB  # noqa: E402
from octo import audio as A  # noqa: E402
from octo.db import connect, rows  # noqa: E402
from octo.stages import STAGES, detect_speech, group_speech, params_for  # noqa: E402

OUT = DATA / "bench" / "vadcompare"  # swept at 60 days (privacy.BENCH)
BLOCK = 600.0
MIN_STRETCH = 1.0  # one-sided stretches shorter than this are boundary disagreement, not misses
DETECTORS = ("silero", "pyannote_raw", "pyannote_hp")


def recordings(conn) -> list[dict]:
    return rows(conn, "SELECT * FROM recordings ORDER BY created_at")


def merge(spans: list, gap: float = 0.0) -> list[list[float]]:
    out: list[list[float]] = []
    for s, e in sorted(spans):
        if out and s - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def total(spans) -> float:
    return sum(e - s for s, e in spans)


def subtract(a: list, b: list) -> list[list[float]]:
    """Parts of spans a not covered by spans b (both merged)."""
    out = []
    for s, e in a:
        cur = s
        for bs, be in b:
            if be <= cur or bs >= e:
                continue
            if bs > cur:
                out.append([cur, bs])
            cur = max(cur, be)
        if cur < e:
            out.append([cur, e])
    return out


def intersect(a: list, b: list) -> float:
    return sum(max(0.0, min(e, be) - max(s, bs)) for s, e in a for bs, be in b)


def run() -> None:
    import torch
    from pyannote.audio import Model
    from pyannote.audio.pipelines import VoiceActivityDetection
    from silero_vad import load_silero_vad

    OUT.mkdir(parents=True, exist_ok=True)
    silero, params = load_silero_vad(), params_for(STAGES["find_conversations"])
    vad = VoiceActivityDetection(segmentation=Model.from_pretrained("pyannote/segmentation-3.0"))
    vad.instantiate({"min_duration_on": 0.0, "min_duration_off": 0.0})
    vad.to(torch.device("mps" if torch.backends.mps.is_available() else "cpu"))

    def pyannote(samples) -> list:
        ann = vad({"waveform": torch.from_numpy(samples).unsqueeze(0), "sample_rate": A.RATE})
        return [(seg.start, seg.end) for seg in ann.get_timeline().support()]

    with connect(DB) as conn:
        recs = recordings(conn)
    for r in recs:
        spans: dict[str, list] = {d: [] for d in DETECTORS}
        t = 0.0
        while t < r["duration_s"]:
            raw = A.read(Path(r["path"]), t, min(r["duration_s"], t + BLOCK))
            spans["silero"] += [(t + a, t + b) for a, b in detect_speech(raw, params, silero)]
            spans["pyannote_raw"] += [(t + a, t + b) for a, b in pyannote(raw)]
            spans["pyannote_hp"] += [(t + a, t + b) for a, b in pyannote(A.highpass(raw, params["highpass_hz"]))]
            t += BLOCK
        out = {"recording": r["id"], "source": r["source"], "duration_s": r["duration_s"],
               **{d: [[round(s, 2), round(e, 2)] for s, e in merge(v)] for d, v in spans.items()}}
        (OUT / f"{r['id']}.json").write_text(json.dumps(out))
        print(f"{r['id']}  {r['duration_s']:7.0f} s  " +
              "  ".join(f"{d} {total(out[d]):6.0f}" for d in DETECTORS), flush=True)


def load() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(OUT.glob("rec_*.json"))]


def one_sided(a: list, b: list) -> list[list[float]]:
    """Stretches a hears that b doesn't, ignoring slivers at the edges of shared speech."""
    return [s for s in merge(subtract(a, b), gap=0.3) if s[1] - s[0] >= MIN_STRETCH]


def report() -> None:
    with connect(DB) as conn:
        sessions = rows(conn, "SELECT recording_id, start_s, end_s FROM sessions WHERE status='active'")
    data = load()
    params = params_for(STAGES["find_conversations"])
    print(f"{len(data)} recordings, {sum(d['duration_s'] for d in data) / 60:.0f} min\n")
    print(f"{'recording':24} {'src':6} {'min':>5} | {'silero':>7} {'pyan':>7} {'pyan-hp':>7} | "
          f"{'both':>6} {'S only':>6} {'P only':>6} | P-only stretches ≥{MIN_STRETCH:.0f}s (in/out of convs)")
    agg = {"s": 0.0, "p": 0.0, "both": 0.0, "s_only": 0.0, "p_only": 0.0}
    missed_convs = []
    for d in data:
        s, p, raw = d["silero"], d["pyannote_hp"], d["pyannote_raw"]
        convs = [[x["start_s"], x["end_s"]] for x in sessions if x["recording_id"] == d["recording"]]
        both = intersect(s, p)
        s_only, p_only = total(s) - both, total(p) - both
        stretches = one_sided(p, s)
        inside = [x for x in stretches if intersect([x], convs) > 0]
        print(f"{d['recording']:24} {d['source']:6} {d['duration_s'] / 60:5.1f} | {total(s):7.0f} {total(raw):7.0f} "
              f"{total(p):7.0f} | {both:6.0f} {s_only:6.0f} {p_only:6.0f} | {len(stretches):3d} "
              f"({len(inside)} in, {len(stretches) - len(inside)} out; {total(stretches):.0f} s)")
        for k, v in zip(agg, (total(s), total(p), both, s_only, p_only)):
            agg[k] += v
        # Would pyannote's speech have made a conversation where Silero's made none?
        for c in group_speech([tuple(x) for x in p], params["gap_s"], params["min_speech_s"]):
            if not intersect([[c["start"], c["end"]]], convs):
                missed_convs.append((d["recording"], c))
    print(f"\nall: silero {agg['s']:.0f} s, pyannote (high-passed) {agg['p']:.0f} s, both {agg['both']:.0f} s, "
          f"silero only {agg['s_only']:.0f} s, pyannote only {agg['p_only']:.0f} s")
    print("\nconversations pyannote's speech would make that the pipeline doesn't have:")
    for rec, c in missed_convs:
        print(f"  {rec}  {c['start']:7.1f}–{c['end']:7.1f} s  speech {c['speech_s']} s")
    if not missed_convs:
        print("  none")


def clips() -> None:
    """Every pyannote-only stretch (≥ MIN_STRETCH) as a WAV under data/vadcompare/clips/ (gitignored)."""
    with connect(DB) as conn:
        paths = {r["id"]: Path(r["path"]) for r in recordings(conn)}
    dest = OUT / "clips"
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for d in load():
        for which, a, b in (("pyannote-only", d["pyannote_hp"], d["silero"]),
                            ("silero-only", d["silero"], d["pyannote_hp"])):
            for s, e in one_sided(a, b):
                pcm = (A.read(paths[d["recording"]], max(0, s - 0.5), e + 0.5) * 32767).astype("<i2").tobytes()
                with wave.open(str(dest / f"{which}_{d['recording']}_{s:07.1f}.wav"), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(A.RATE)
                    w.writeframes(pcm)
                n += 1
    print(f"{n} clips in {dest}")


if __name__ == "__main__":
    {"run": run, "report": report, "clips": clips}[sys.argv[1] if len(sys.argv) > 1 else "report"]()
