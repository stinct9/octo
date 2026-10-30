"""Compare voiceprint models on the owner's own conversations, and score normalisation on each.

    python -m bench.voicecompare run [MODEL...]   # default: every model below
    python -m bench.voicecompare report

`run` embeds every person the owner has confirmed, in every conversation whose audio is still kept (plus
their enrolment take), with each model, and keeps only the embeddings under data/bench/voicecompare/,
never the audio. Those are voiceprints of people the owner has labelled, which D1 already allows on the
Mac. `report` scores them.

Each model is scored twice: raw cosine, and with adaptive score normalisation (AS-norm, "+norm"),
which rescales a score by how that voiceprint and that voice score against other people. The
"other people" (the cohort) are the prints of everyone else the owner has labelled -- in production that
is all it could use, and it shares the owner's mics and rooms, which is the mismatch it is meant to
correct. No public recordings are needed.

Raw cosine scales differ between models (0.6 means different things), so nothing here uses a fixed
bar. The report compares how well each model separates people:
- EER: where false accepts equal false rejects, over every pair of samples from different
  conversations. Lower is better.
- separation (d'): distance between same-person and different-person scores, in their spread.
- right top-1: the closest print is the right person, for voices whose person has a print from
  another conversation (leave-one-conversation-out, as if each conversation were new).
- named, no wrong names: at the lowest bar that names nobody wrongly -- no wrong top-1, and no
  voice of someone without a print -- how many are named right. The bar is picked on the same
  data, so it flatters every model equally; it's a comparison, not a setting.
Two conditions: a person's up-to-60 s of speech (what identify embeds) and 10 s slices of it (a
quiet person), each scored against the long prints.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from itertools import combinations
from pathlib import Path

import numpy as np

os.environ.setdefault("HF_HUB_OFFLINE", "1")

from octo import DATA, DB_PATH as DB, HOME, RATE, ROOT  # noqa: E402
from octo import audio as A  # noqa: E402
from octo.db import connect, rows  # noqa: E402
from octo.voice import EMBEDDING_MODEL, MATCH_MARGIN, MATCH_MIN, MAX_SPEECH_S, MIN_PRINT_S, MIN_TURN_S  # noqa: E402

OUT = DATA / "bench" / "voicecompare"  # swept at 60 days (privacy.BENCH)
SLICE_S = 10.0
MAX_SLICES = 6  # per person per conversation, spread across it
COHORT_K = 10  # AS-norm: the closest cohort scores used

# name -> (route, model). CAM++'s ONNX file lives in data/voicecompare/models/, from
# https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models, run with onnxruntime
# and 3D-Speaker's own features. Not through sherpa-onnx itself: on 2 Oct its features for WeSpeaker
# models (1.13.8) gave embeddings unrelated to pyannote's for the same ResNet34 (cosine ~0, 46% EER),
# while this route matched pyannote exactly (cosine 1.0000).
MODELS = {  # "octo": through octo.voice.Embedder, exactly as identify runs it
    "resnet34": ("octo", "pyannote/wespeaker-voxceleb-resnet34-LM"),  # identify until 2 Oct
    "resnet293": ("octo", "Wespeaker/wespeaker-voxceleb-resnet293-LM"),  # identify since 2 Oct
    "campplus": ("3dspeaker", "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"),
    "titanet": ("nemo", "nvidia/speakerverification_en_titanet_large"),  # in .venv-nemo
}
MIC = {"device": "recorder", "phone": "phone"}


# ---- samples: who, from which conversation and mic --------------------------------------------

def speech_pieces(samples: np.ndarray, turns: list[tuple[float, float]]) -> tuple[list[np.ndarray], float]:
    """The speech identify embeds (octo.voice.Embedder.embed_turns): longest turns first, each at
    least MIN_TURN_S, up to MAX_SPEECH_S."""
    pieces, total = [], 0.0
    for s, e in sorted(turns, key=lambda t: t[1] - t[0], reverse=True):
        if e - s < MIN_TURN_S or total >= MAX_SPEECH_S:
            continue
        pieces.append(samples[int(s * RATE):int(e * RATE)])
        total += e - s
    return pieces, round(total, 1)


def slices(speech: np.ndarray, size: float = SLICE_S, most: int = MAX_SLICES) -> list[np.ndarray]:
    """Up to `most` consecutive `size`-second stretches of speech, spread evenly across it."""
    n = int(size * RATE)
    have = len(speech) // n
    picks = sorted({int(round(i)) for i in np.linspace(0, have - 1, min(most, have))}) if have else []
    return [speech[i * n:(i + 1) * n] for i in picks]


def gather(conn) -> tuple[list[dict], dict[str, np.ndarray]]:
    """Every confirmed person in every conversation whose audio is kept (with the owner's merges and line
    fixes applied), plus enrolment takes. Returns sample descriptions and their audio by key."""
    from bench.diarcompare import reference
    from octo.voice import speech_only

    items: list[dict] = []
    waves: dict[str, np.ndarray] = {}

    def add(person: str, session: str, mic: str, full: np.ndarray, seconds: float, in_time_order: np.ndarray) -> None:
        key = f"f{len(items)}"
        items.append({"key": key, "kind": "long", "person": person, "session": session, "mic": mic, "seconds": seconds})
        waves[key] = full
        for sl in slices(in_time_order):
            key = f"s{len(items)}"
            items.append({"key": key, "kind": "slice", "person": person, "session": session, "mic": mic, "seconds": SLICE_S})
            waves[key] = sl

    for s in rows(conn, "SELECT s.*, r.path, r.source FROM sessions s JOIN recordings r ON r.id=s.recording_id "
                        "WHERE s.status='active' AND r.audio_deleted_at IS NULL ORDER BY s.created_at"):
        got = reference(conn, s["id"])
        if not got or not got[1] or not (HOME / s["path"]).exists():
            continue
        turns, who = got
        samples = A.read(HOME / s["path"], s["start_s"], s["end_s"])
        for person in sorted(set(who.values())):
            mine = [(t["start"], t["end"]) for t in turns if who.get(t["speaker"]) == person]
            pieces, total = speech_pieces(samples, mine)
            if total < 1.0:  # identify needs a second of speech
                continue
            ordered = [samples[int(a * RATE):int(b * RATE)] for a, b in sorted(mine) if b - a >= MIN_TURN_S]
            add(person, s["id"], MIC.get(s["source"], s["source"]), np.concatenate(pieces), total, np.concatenate(ordered))

    for vp in rows(conn, "SELECT DISTINCT v.source, p.name FROM voiceprints v JOIN people p ON p.id=v.person_id "
                         "WHERE v.source LIKE 'enrol:%'"):
        name = vp["source"].split(":", 1)[1]
        path = next((p for p in (HOME / "audio").rglob(name) if p.is_file()), None)
        if not path:
            continue
        speech = speech_only(A.read(path))
        if len(speech) >= RATE:
            full = speech[:int(MAX_SPEECH_S * RATE)]
            add(vp["name"], vp["source"], "board at desk", full, round(len(full) / RATE, 1), speech)
    return items, waves


def items_hash(items: list[dict]) -> str:
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()[:12]


# ---- embedding ---------------------------------------------------------------------------------

def embed_octo(model: str, waves: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    from octo.voice import Embedder  # high-passes, as identify does

    emb = Embedder(model)
    return {k: emb.embed(w) for k, w in waves.items()}


def fbank_3dspeaker(samples: np.ndarray) -> np.ndarray:
    """3D-Speaker's input (speakerlab FBank): 80 log-mel bins over [-1, 1] samples with Kaldi's
    default window, centred over the clip. (WeSpeaker's is octo.voice.wespeaker_fbank.)"""
    import torch
    from torchaudio.compliance import kaldi

    x = torch.from_numpy(np.ascontiguousarray(samples, dtype=np.float32)).unsqueeze(0)
    f = kaldi.fbank(x, num_mel_bins=80, sample_frequency=RATE, dither=0.0)
    return (f - f.mean(0, keepdim=True)).numpy()[None]


def embed_onnx(model_file: str, waves: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    import onnxruntime as ort

    model = OUT / "models" / model_file
    if not model.exists():
        raise SystemExit(f"missing {model}: download it from the sherpa-onnx speaker-recongition-models release")
    sess = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name
    # same 80 Hz high-pass as identify
    return {k: sess.run(None, {name: fbank_3dspeaker(A.highpass(w))})[0].reshape(-1) for k, w in waves.items()}


def embed_nemo(model: str, waves: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """In .venv-nemo (see bench/nemo_embed.py), through a temporary file deleted after."""
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.npz", Path(tmp) / "out.npz"
        np.savez(src, **{k: A.highpass(w) for k, w in waves.items()})
        env = {**os.environ, "HF_HUB_OFFLINE": os.environ.get("HF_HUB_OFFLINE", "1")}
        proc = subprocess.run([str(ROOT / ".venv-nemo" / "bin" / "python"), str(ROOT / "bench" / "nemo_embed.py"),
                               str(src), str(dst), "--model", model], capture_output=True, text=True, env=env)
        if proc.returncode:
            raise RuntimeError(proc.stderr[-2000:])
        with np.load(dst) as out:
            return {k: out[k] for k in out.files}


def run(names: list[str]) -> None:
    conn = connect(DB)
    items, waves = gather(conn)
    OUT.mkdir(parents=True, exist_ok=True)
    h = items_hash(items)
    (OUT / "samples.json").write_text(json.dumps({"hash": h, "items": items}, indent=1))
    longs = [i for i in items if i["kind"] == "long"]
    print(f"{len(longs)} long samples, {len(items) - len(longs)} slices, {len({i['person'] for i in items})} people")
    for name in names or list(MODELS):
        route, model = MODELS[name]
        t0 = time.time()
        embs = (embed_octo(model, waves) if route == "octo" else embed_nemo(model, waves) if route == "nemo"
                else embed_onnx(model, waves))
        took = time.time() - t0
        np.savez(OUT / f"{name}.npz", **embs)
        (OUT / f"{name}.json").write_text(json.dumps({"model": model, "hash": h, "seconds": round(took, 1),
                                                      "audio_s": round(sum(len(w) for w in waves.values()) / RATE, 1)}))
        print(f"{name:14} {len(embs)} embeddings, dim {len(next(iter(embs.values())))}, {took:.1f} s", flush=True)


# ---- scoring (pure) ----------------------------------------------------------------------------

def unit(e: np.ndarray) -> np.ndarray:
    return e / (np.linalg.norm(e, axis=-1, keepdims=True) + 1e-12)


def eer(target: list[float], nontarget: list[float]) -> float:
    """Equal error rate: the threshold where missed same-person pairs equal accepted different ones."""
    t, n = np.asarray(target), np.asarray(nontarget)
    best = (2.0, 1.0)
    for th in np.unique(np.concatenate([t, n])):
        frr, far = float((t < th).mean()), float((n >= th).mean())
        best = min(best, (abs(frr - far), (frr + far) / 2))
    return best[1]


def dprime(target: list[float], nontarget: list[float]) -> float:
    t, n = np.asarray(target), np.asarray(nontarget)
    return float((t.mean() - n.mean()) / np.sqrt((t.var() + n.var()) / 2 + 1e-12))


def asnorm(score: float, a_cohort: np.ndarray, b_cohort: np.ndarray, k: int = COHORT_K) -> float:
    """Adaptive S-norm: the score in standard deviations above each side's k closest cohort scores, averaged."""
    def z(c: np.ndarray) -> float:
        top = np.sort(c)[-k:]
        return (score - top.mean()) / max(float(top.std()), 1e-3)
    return 0.5 * (z(a_cohort) + z(b_cohort))


def trials(items: list[dict], E: dict[str, np.ndarray], probe_kind: str, norm: bool) -> list[dict]:
    """Every probe (long sample or slice) against every long print from a different conversation.
    Cohort for AS-norm: prints of everyone else, from other conversations than either side's."""
    prints = [i for i in items if i["kind"] == "long" and i["seconds"] >= MIN_PRINT_S]
    if probe_kind == "long":  # print against print: each pair once
        pairs = list(combinations(prints, 2))
    else:
        pairs = [(p, g) for p in items if p["kind"] == probe_kind for g in prints]
    out = []
    for p, g in pairs:
        if g["session"] != p["session"]:
            s = float(unit(E[p["key"]]) @ unit(E[g["key"]]))
            if norm:
                cohort = [c for c in prints if c["person"] not in (p["person"], g["person"])
                          and c["session"] not in (p["session"], g["session"])]
                if len(cohort) < 3:
                    continue
                C = unit(np.stack([E[c["key"]] for c in cohort]))
                s = asnorm(s, C @ unit(E[p["key"]]), C @ unit(E[g["key"]]))
            out.append({"score": s, "same": p["person"] == g["person"], "mics": tuple(sorted((p["mic"], g["mic"])))})
    return out


def identify_all(items: list[dict], E: dict[str, np.ndarray], probe_kind: str, norm: bool) -> list[dict]:
    """Each probe as a new conversation: the best-scoring person among prints from other
    conversations (each person's best print, as octo.voice.match), raw or AS-normed. With AS-norm,
    scoring against person P uses everyone else's prints as the cohort (production can't know who
    the probe is, only who P is)."""
    prints = [i for i in items if i["kind"] == "long" and i["seconds"] >= MIN_PRINT_S]
    out = []
    for p in [i for i in items if i["kind"] == probe_kind]:
        gallery = [g for g in prints if g["session"] != p["session"]]
        if not gallery:
            continue
        G = unit(np.stack([E[g["key"]] for g in gallery]))
        x = unit(E[p["key"]])
        raw = G @ x
        best: dict[str, float] = {}
        for idx, g in enumerate(gallery):
            s = float(raw[idx])
            if norm:
                others = [j for j, c in enumerate(gallery) if c["person"] != g["person"]]
                if len(others) < 3:
                    continue
                s = asnorm(s, raw[others], G[others] @ G[idx])
            best[g["person"]] = max(best.get(g["person"], -np.inf), s)
        if not best:
            continue
        ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
        out.append({"person": p["person"], "known": p["person"] in best, "top": ranked[0][0], "score": ranked[0][1],
                    "runner_up": ranked[1][1] if len(ranked) > 1 else None, "mic": p["mic"]})
    return out


def naming(results: list[dict]) -> dict:
    """Right top-1 among voices with a print elsewhere; and at the lowest bar that names nobody
    wrongly (no wrong top-1, no voice without a print), how many are named right."""
    known = [r for r in results if r["known"]]
    right = [r for r in known if r["top"] == r["person"]]
    wrong = [r["score"] for r in results if r["top"] != r["person"]]
    bar = max(wrong) if wrong else -np.inf
    return {"known": len(known), "unknown": len(results) - len(known), "right": len(right),
            "named": sum(r["score"] > bar for r in right)}


def todays_rule(results: list[dict]) -> dict:
    """octo.voice.match on raw cosine: >= MATCH_MIN and MATCH_MARGIN ahead of the runner-up."""
    named = [r for r in results if r["score"] >= MATCH_MIN and (r["runner_up"] is None or r["score"] - r["runner_up"] >= MATCH_MARGIN)]
    return {"right": sum(r["top"] == r["person"] for r in named), "wrong": sum(r["top"] != r["person"] for r in named),
            "unnamed": len(results) - len(named)}


# ---- report ------------------------------------------------------------------------------------

def load() -> tuple[list[dict], dict[str, dict]]:
    meta = json.loads((OUT / "samples.json").read_text())
    models = {}
    for name in MODELS:
        f, j = OUT / f"{name}.npz", OUT / f"{name}.json"
        if not f.exists():
            continue
        info = json.loads(j.read_text())
        if info["hash"] != meta["hash"]:
            print(f"({name}: embedded from an older set of samples -- run it again)")
            continue
        with np.load(f) as z:
            models[name] = {"E": {k: z[k] for k in z.files}, **info}
    return meta["items"], models


def report() -> None:
    items, models = load()
    longs = [i for i in items if i["kind"] == "long"]
    multi = {p for p in {i["person"] for i in longs} if len({i["session"] for i in longs if i["person"] == p}) > 1}
    print(f"{len(longs)} voices ({len(items) - len(longs)} 10 s slices) from {len({i['session'] for i in longs})} "
          f"conversations/takes; {len({i['person'] for i in longs})} people, {len(multi)} heard in more than one: "
          f"{', '.join(sorted(multi))}\n")

    for kind, title in (("long", "Up to 60 s of a person (what identify embeds)"), ("slice", "10 s of a person, against long prints")):
        tr0 = trials(items, next(iter(models.values()))["E"], kind, False) if models else []
        print(f"{title}: {sum(t['same'] for t in tr0)} same-person pairs, {sum(not t['same'] for t in tr0)} different-person")
        print(f"  {'model':16} {'EER':>6} {'separation':>10} {'right top-1':>12} {'named, none wrong':>18} {'time':>7}")
        for name, m in models.items():
            for norm in (False, True):
                tr = trials(items, m["E"], kind, norm)
                ids = identify_all(items, m["E"], kind, norm)
                nm = naming(ids)
                tgt = [t["score"] for t in tr if t["same"]]
                non = [t["score"] for t in tr if not t["same"]]
                label = name + ("+norm" if norm else "")
                took = f"{m['seconds']:.0f} s" if not norm else ""
                print(f"  {label:16} {100 * eer(tgt, non):5.1f}% {dprime(tgt, non):10.2f} "
                      f"{nm['right']:>5}/{nm['known']:<6} {nm['named']:>11}/{nm['known']:<6} {took:>7}")
        live = next((n for n, (_, model) in MODELS.items() if model == EMBEDDING_MODEL and n in models), None)
        if live:
            r = todays_rule(identify_all(items, models[live]["E"], kind, False))
            print(f"  today's rule ({live}, >= {MATCH_MIN} and {MATCH_MARGIN} ahead): {r['right']} right, "
                  f"{r['wrong']} wrong, {r['unnamed']} unnamed")
        ids0 = identify_all(items, next(iter(models.values()))["E"], kind, False) if models else []
        print(f"  ({sum(not r['known'] for r in ids0)} voices of people with no print elsewhere: naming any of them is wrong)\n")

    print("Same person, different conversations, by mic: median score in standard deviations above")
    print("different-person scores (raw / +norm; higher is safer), long samples")
    pairs = sorted({t["mics"] for t in trials(items, next(iter(models.values()))["E"], "long", False) if t["same"]}) if models else []
    print(f"  {'mics':28} {'n':>3} " + " ".join(f"{n:>14}" for n in models))
    for pair in pairs:
        cells, n = [], 0
        for m in models.values():
            zs = []
            for norm in (False, True):
                tr = trials(items, m["E"], "long", norm)
                non = np.array([t["score"] for t in tr if not t["same"]])
                same = [t["score"] for t in tr if t["same"] and t["mics"] == pair]
                n = len(same)
                zs.append(float(np.median((np.array(same) - non.mean()) / non.std())) if same else float("nan"))
            cells.append(f"{zs[0]:5.1f} / {zs[1]:4.1f}".rjust(14))
        print(f"  {' + '.join(pair):28} {n:>3} " + " ".join(cells))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        bad = [m for m in sys.argv[2:] if m not in MODELS]
        if bad:
            raise SystemExit(f"unknown model(s) {bad}; choose from {list(MODELS)}")
        run(sys.argv[2:])
    else:
        report()
