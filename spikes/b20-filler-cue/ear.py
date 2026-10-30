"""SPIKE (B22, step 2): is the small model's ear the limit? Whisper large-v3-turbo's encoder (already on this Mac, run
locally on the GPU) over the same conversations and folds as experiment.py, a small head trained on its output, and
the same scoring (one and two windows in a row).

An upper bound, not a candidate: the encoder hears 30 s at once, past and future, and the head is trained on every
window (no sampling). If it isn't clearly better than w32, more model won't help; data and labels will.

With --short (21 Oct, after the first run): each 1 s window encoded alone, as the chip hears it; the encoder's
first 50 positions only (Whisper always trained on 30 s, so this is off its usual input). Separates "a better ear"
from "30 s of context".

    OCTO_HOME=... .venv/bin/python spikes/b20-filler-cue/ear.py [--short]
"""
import json
import random
import sys

import mlx.core as mx
import numpy as np
import torch
import torch.nn as nn
from mlx_whisper import audio as WA
from mlx_whisper.load_models import load_model

from clips import OUT
from experiment import DECODES, FOLDS, HOP, THRESHOLDS, crop, cues, load_all, summary
from train import MATCH_S, SR

POOL = 5                # Whisper's 50 frames a second → 10
WIN_F = 10              # the head sees 1 s, as the recorder's model does
CACHE = OUT / "ear"


def encode(model, x: np.ndarray) -> np.ndarray:
    """(frames at 10 a second, 1280) for the whole conversation, 30 s at a time."""
    out = []
    for a in range(0, len(x), WA.N_SAMPLES):
        chunk = x[a:a + WA.N_SAMPLES].astype(np.float32)
        n = int(np.ceil(len(chunk) / SR * 50))
        mel = WA.log_mel_spectrogram(np.pad(chunk, (0, WA.N_SAMPLES - len(chunk))), n_mels=128)[:WA.N_FRAMES]
        h = np.array(model.encoder(mel[None].astype(mx.float16)))[0, :n]
        out.append(h[:len(h) // POOL * POOL].reshape(-1, POOL, h.shape[1]).mean(1))
    return np.concatenate(out).astype(np.float16)


def encode_short(model, x: np.ndarray, t: np.ndarray) -> np.ndarray:
    """(windows, 5, 1280): each 1 s window round t alone through the encoder, pooled to 5 frames."""
    enc, out = model.encoder, []
    for i in range(0, len(t), 128):
        mel = mx.stack([WA.log_mel_spectrogram(crop(x, c, SR), n_mels=128)[:100] for c in t[i:i + 128]])
        h = nn_gelu(enc.conv2(nn_gelu(enc.conv1(mel.astype(mx.float16)))))
        h = h + enc._positional_embedding[:h.shape[1]]
        for block in enc.blocks:
            h, _, _ = block(h)
        h = np.array(enc.ln_post(h))
        out.append(h.reshape(len(h), 5, -1, h.shape[-1]).mean(2))
    return np.concatenate(out).astype(np.float16)


def nn_gelu(x):
    import mlx.nn
    return mlx.nn.gelu(x)


def windows(h: np.ndarray, dur: float) -> tuple[np.ndarray, np.ndarray]:
    """Window centres every 0.25 s (as experiment.stream) and the frames each covers."""
    t = np.arange(SR // 2, int(dur * SR) - SR // 2 + 1, HOP) / SR
    idx = np.clip(np.round(t * 10).astype(int)[:, None] + np.arange(-WIN_F // 2, WIN_F // 2), 0, len(h) - 1)
    return t, idx


def head() -> nn.Module:
    return nn.Sequential(nn.Conv1d(1280, 64, 3, padding=1), nn.ReLU(), nn.Conv1d(64, 64, 3, padding=1), nn.ReLU(),
                         nn.AdaptiveMaxPool1d(1), nn.Flatten(), nn.Linear(64, 2))


def labels(c: dict, t: np.ndarray) -> np.ndarray:
    """1 near his "you know", 0 well away from anyone's, -1 (unused) in between; others' count as 0 (his_only)."""
    y = np.zeros(len(t), dtype=np.int64)
    for m, _ in c["yk"]:
        y[np.abs(t - m) <= 0.7] = -1
    for m, me in sorted(c["yk"], key=lambda k: k[1]):  # his last, so his win where two are close
        y[np.abs(t - m) <= 0.15] = int(me)
    return y


def prepare(convs: list[dict], short: bool, keep_audio: bool = False) -> None:
    """Each conversation's window times (c["t"]) and its encodings, made once and cached; win(c) gives them."""
    cache = OUT / "ear1s" if short else CACHE
    cache.mkdir(exist_ok=True)
    model = None
    for c in convs:
        f = cache / f"{c['id']}.npy"
        c["dur"] = len(c["x"]) / SR
        c["t"] = np.arange(SR // 2, int(c["dur"] * SR) - SR // 2 + 1, HOP) / SR
        if not f.exists():
            model = model or load_model("mlx-community/whisper-large-v3-turbo", dtype=mx.float16)
            np.save(f, encode_short(model, c["x"], c["t"]) if short else encode(model, c["x"]))
        if short:
            c["w"] = np.load(f, mmap_mode="r")
        else:
            c["h"] = np.load(f)
            c["t"], c["idx"] = windows(c["h"], c["dur"])
        if not keep_audio:
            del c["x"]


def win(c: dict) -> np.ndarray:
    return c["w"] if "w" in c else c["h"][c["idx"]]


def folds_of(convs: list[dict]) -> list[list[dict]]:
    """The same folds as experiment.py: it shuffles the same list of test conversations."""
    convs = list(convs)
    random.Random(20).shuffle(convs)
    return [convs[i::FOLDS] for i in range(FOLDS)]


def teacher(train: list[dict], seed: int, dev) -> nn.Module:
    X = np.concatenate([win(c) for c in train])
    Y = np.concatenate([labels(c, c["t"]) for c in train])
    X, Y = torch.tensor(X[Y >= 0]), torch.tensor(Y[Y >= 0])
    torch.manual_seed(20 + seed)
    net = head().to(dev)
    opt = torch.optim.Adam(net.parameters(), 1e-3)
    w = torch.tensor([1.0, float((Y == 0).sum() / Y.sum())]).to(dev)
    for _ in range(8):
        perm = torch.randperm(len(Y))
        for i in range(0, len(Y), 256):
            j = perm[i:i + 256]
            xb = X[j].float().transpose(1, 2).to(dev)
            loss = nn.functional.cross_entropy(net(xb), Y[j].to(dev), weight=w)
            opt.zero_grad(); loss.backward(); opt.step()
    return net.eval()


def score(net: nn.Module, c: dict, dev) -> np.ndarray:
    with torch.no_grad():
        xb = torch.tensor(win(c)).float().transpose(1, 2).to(dev)
        return torch.cat([torch.softmax(net(xb[i:i + 1024]), 1)[:, 1] for i in range(0, len(xb), 1024)]).cpu().numpy()


def tally(tots: dict, c: dict, t: np.ndarray, p: np.ndarray) -> None:
    """Adds one held-out conversation to tots[decode][threshold] = [his caught, his total, wrong]."""
    for d, tot in tots.items():
        for thr in THRESHOLDS:
            cs, used = cues(t, p, thr, d), set()
            for m, me in c["yk"]:
                j = next((j for j, q in enumerate(cs) if j not in used and abs(q - m) <= MATCH_S), None)
                if j is not None:
                    used.add(j)
                if me:
                    tot[thr][1] += 1
                    tot[thr][0] += j is not None
            tot[thr][2] += len(cs) - len(used)


def result(name: str, seed: int, tots: dict, hours: float, n: int) -> dict:
    res = {"variant": name, "seed": seed, "hours": round(hours, 2), "convs": n}
    for d, tt in tots.items():
        curve = [{"threshold": t, "his": tt[t][0], "wrong": tt[t][2]} for t in THRESHOLDS]
        res[f"run{d}"] = {**summary(curve, tt[THRESHOLDS[0]][1], hours), "curve": curve}
    print(name, seed, {d: {k: v for k, v in res[f"run{d}"].items() if k != "curve"} for d in DECODES}, flush=True)
    return res


def main() -> None:
    dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    short = "--short" in sys.argv
    convs = [c for c in load_all() if c["test"]]
    prepare(convs, short)
    print(f"{len(convs)} conversations encoded", flush=True)
    results = []
    for seed in range(3):
        tots = {d: {t: [0, 0, 0] for t in THRESHOLDS} for d in DECODES}
        hours = 0.0
        for k, test in enumerate(folds_of(convs)):
            net = teacher([c for c in convs if c not in test], seed, dev)
            for c in test:
                hours += c["dur"] / 3600
                tally(tots, c, c["t"], score(net, c, dev))
            print(f"  seed {seed} fold {k + 1}/{FOLDS}", flush=True)
        results.append(result("whisper_ear_1s" if short else "whisper_ear", seed, tots, hours, len(convs)))
    (OUT / ("ear1s.json" if short else "ear.json")).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
