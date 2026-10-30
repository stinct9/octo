"""Compare speaker-separation models on the owner's own conversations.

    python -m bench.diarcompare run pyannote/speaker-diarization-community-1 [SESSION...]
    python -m bench.diarcompare report

`run` diarizes each conversation with the model and keeps only the turns (who spoke when) under
data/diarcompare/, never the audio or words. `report` scores every model run so far against what
the owner confirmed: the current diarize run's speakers, renamed to the people they confirmed and with their
merges applied. That reference has the current model's boundaries, so it favours the current model
on timing; what it does measure fairly is the thing that goes wrong: one person split into several
speakers, or two people merged into one.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from octo import audio as A
from octo.db import connect, row, rows
from octo import DATA, DB_PATH as DB, HOME, ROOT

OUT = DATA / "bench" / "diarcompare"  # swept at 60 days (privacy.BENCH)
MIN_MIN = 1.0  # conversations shorter than a minute say little


def slug(model: str) -> str:
    return model.replace("/", "__")


def sessions(conn) -> list[dict]:
    return rows(conn, "SELECT s.*, r.path FROM sessions s JOIN recordings r ON r.id=s.recording_id "
                      "WHERE s.status='active' AND s.end_s - s.start_s >= ? ORDER BY s.start_s", MIN_MIN * 60)


def diarize_pyannote(model: str, samples) -> list[dict]:
    import torch
    from pyannote.audio import Pipeline

    model, _, threshold = model.partition("@")  # "name@0.5": a different clustering threshold
    pipe = Pipeline.from_pretrained(model)
    if threshold:
        base = pipe.parameters(instantiated=True)
        pipe.instantiate({**base, "clustering": {**base["clustering"], "threshold": float(threshold)}})
    pipe.to(torch.device("mps" if torch.backends.mps.is_available() else "cpu"))
    out = pipe({"waveform": torch.from_numpy(samples).unsqueeze(0), "sample_rate": A.RATE})
    ann = getattr(out, "exclusive_speaker_diarization", None) or out.speaker_diarization
    return [{"speaker": spk, "start": round(seg.start, 2), "end": round(seg.end, 2)}
            for seg, _, spk in ann.itertracks(yield_label=True)]


def diarize_nemo(model: str, samples) -> list[dict]:
    """In .venv-nemo (see bench/nemo_diar.py), through a temporary WAV that is deleted after."""
    import os
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "in.wav"
        A.write(wav, samples)
        env = {**os.environ, "HF_HUB_OFFLINE": os.environ.get("HF_HUB_OFFLINE", "1")}
        proc = subprocess.run([str(ROOT / ".venv-nemo" / "bin" / "python"), str(ROOT / "bench" / "nemo_diar.py"),
                               str(wav), "--model", model], capture_output=True, text=True, env=env)
    if proc.returncode:
        raise RuntimeError(proc.stderr[-2000:])
    return json.loads(proc.stdout)["turns"]


def run(model: str, only: list[str]) -> None:
    conn = connect(DB)
    d = OUT / slug(model)
    d.mkdir(parents=True, exist_ok=True)
    fn = diarize_nemo if model.startswith("nvidia/") else diarize_pyannote
    for s in sessions(conn):
        if only and s["id"] not in only:
            continue
        samples = A.read(HOME / s["path"], s["start_s"], s["end_s"])
        t0 = time.time()
        turns = fn(model, samples)
        took = time.time() - t0
        (d / f"{s['id']}.json").write_text(json.dumps({"model": model, "session": s["id"], "seconds": round(took, 1),
                                                         "audio_s": round(len(samples) / A.RATE, 1), "turns": turns}))
        print(f"{s['id']}  {len(samples) / A.RATE / 60:5.1f} min  {took:6.1f} s  "
              f"{len({t['speaker'] for t in turns})} speakers", flush=True)


def reference(conn, sid: str) -> tuple[list[dict], dict[str, str]] | None:
    """Current diarize turns with the owner's merges applied, and letter -> person for confirmed letters."""
    from octo.corrections import apply, for_run

    dz = row(conn, "SELECT id, output FROM stage_runs WHERE target=? AND stage='diarize' AND is_current=1", sid)
    if not dz:
        return None
    out = json.loads(dz["output"])
    fixes = for_run(conn, sid, dz["id"])
    turns = apply(out, fixes)["turns"] if fixes else out["turns"]
    who = {r["speaker"]: r["name"] for r in rows(
        conn, "SELECT ss.speaker, p.name FROM session_speakers ss JOIN people p ON p.id=ss.person_id "
              "WHERE ss.session_id=? AND ss.state='confirmed'", sid)}
    return turns, who


def overlap(a: list[dict], b: list[dict]) -> dict[tuple[str, str], float]:
    shared: dict[tuple[str, str], float] = {}
    for x in a:
        for y in b:
            o = min(x["end"], y["end"]) - max(x["start"], y["start"])
            if o > 0:
                shared[(x["speaker"], y["speaker"])] = shared.get((x["speaker"], y["speaker"]), 0.0) + o
    return shared


def score(ref: list[dict], who: dict[str, str], hyp: list[dict]) -> dict:
    """Over the speech of people the owner confirmed, with each model speaker standing for at most one
    person (the best one-to-one pairing): the share of speech on its person's speaker; people
    merged into someone else's speaker ("lost"); and people spread over extra speakers ("split").
    A merge costs the owner most: a split is one Merge click, a merge means moving lines one by one."""
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    ref = [{**t, "speaker": who[t["speaker"]]} for t in ref if t["speaker"] in who]
    people = sorted({t["speaker"] for t in ref})
    speakers = sorted({t["speaker"] for t in hyp})
    shared = overlap(ref, hyp)
    m = np.array([[shared.get((p, h), 0.0) for h in speakers] for p in people]).reshape(len(people), len(speakers))
    talk = {p: sum(t["end"] - t["start"] for t in ref if t["speaker"] == p) for p in people}
    rows_, cols = linear_sum_assignment(-m) if m.size else ([], [])
    own = {people[i]: m[i, j] for i, j in zip(rows_, cols) if m[i, j] >= 0.5 * m[i].max() > 0}
    lost = [p for p in people if p not in own]
    split = sum(max(0, int((m[i] >= max(3.0, 0.05 * talk[p])).sum()) - 1) for i, p in enumerate(people))
    return {"right_s": round(float(sum(own.values())), 1), "talk_s": round(sum(talk.values()), 1),
            "speakers": len(speakers), "lost": lost, "split": split}


def report() -> None:
    conn = connect(DB)
    models = ["current"] + (sorted(p.name for p in OUT.iterdir() if p.is_dir()) if OUT.exists() else [])
    names = [m.split("__")[-1].replace("speaker-diarization-", "")[:22] for m in models]
    print(f"{'conversation':10} {'min':>5} {'people':>6}  " + "  ".join(f"{n:>30}" for n in names))
    totals = {m: [0.0, 0.0, 0.0, 0] for m in models}
    for s in sessions(conn):
        got = reference(conn, s["id"])
        if not got or not got[1]:
            continue
        ref, who = got
        cells = []
        for m in models:
            if m == "current":
                dz = row(conn, "SELECT output FROM stage_runs WHERE target=? AND stage='diarize' AND is_current=1", s["id"])
                hyp, secs = json.loads(dz["output"])["turns"], None
            else:
                f = OUT / m / f"{s['id']}.json"
                if not f.exists():
                    cells.append(f"{'-':>30}")
                    continue
                data = json.loads(f.read_text())
                hyp, secs = data["turns"], data["seconds"]
            sc = score(ref, who, hyp)
            cells.append(f"{sc['speakers']:>2}spk {100 * sc['right_s'] / sc['talk_s']:5.1f}% {len(sc['lost'])}lost {sc['split']}split"
                         .rjust(30))
            t = totals[m]
            t[0] += sc["right_s"]; t[1] += sc["talk_s"]; t[2] += secs or 0; t[3] += len(sc["lost"])
        print(f"{s['id'][-8:]:10} {(s['end_s'] - s['start_s']) / 60:5.1f} {len(set(who.values())):>6}  " + "  ".join(cells))
    print()
    for m, (r, t, secs, lost) in totals.items():
        print(f"{m:50} {100 * r / t:5.1f}% right, {lost} people lost" + (f", {secs / 60:.1f} min to run" if secs else ""))


if __name__ == "__main__":
    if sys.argv[1] == "run":
        run(sys.argv[2], sys.argv[3:])
    else:
        report()
