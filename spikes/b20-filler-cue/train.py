"""SPIKE (B20, step 2): can a model small enough for the recorder spot "you know" in the lab's own audio?

Trains a small keyword model on the Mac from the recorder's conversations on the fillers' line (labels from the
transcript), holds out whole conversations, then slides it over those as the recorder would (1 s window every
0.25 s) and counts cues against the transcript's "you know"s. Nothing leaves the Mac; writes only the model and
numbers under HOME/data/bench/b20-fillers/.

    OCTO_HOME=... .venv/bin/python spikes/b20-filler-cue/train.py
"""
import json
import random

import numpy as np
import torch
import torch.nn as nn
import torchaudio

from clips import OUT, conversations, norm
from octo import HOME, db
from octo import audio as A

SR = A.RATE                 # 16 kHz, as the recorder records
WIN, HOP = SR, SR // 4      # the recorder's window and step
NEG_PER_POS = 8
HELD_OUT = 0.3              # of conversations, by count of "you know"
MATCH_S = 0.6               # a cue this close to a transcript "you know"'s middle is a hit
MERGE_S = 0.75              # cues closer than this are one

mel = torchaudio.transforms.MelSpectrogram(SR, n_fft=512, win_length=480, hop_length=320, f_min=60, n_mels=40)


def features(x: torch.Tensor) -> torch.Tensor:
    """(n, WIN) samples → (n, 1, 40, 51) log-mel, minus each window's mean: the same at any gain."""
    f = torch.log(mel(x) + 1e-6)
    return (f - f.mean(dim=(1, 2), keepdim=True)).unsqueeze(1)


def ds_block(c: int) -> nn.Sequential:
    return nn.Sequential(nn.Conv2d(c, c, 3, padding=1, groups=c), nn.BatchNorm2d(c), nn.ReLU(),
                         nn.Conv2d(c, c, 1), nn.BatchNorm2d(c), nn.ReLU())


def model() -> nn.Module:
    """A DS-CNN of the size keyword spotters run on microcontrollers (~25k weights, ~25 KB at int8)."""
    c = 64
    return nn.Sequential(nn.Conv2d(1, c, (5, 3), stride=(2, 2), padding=(2, 1)), nn.BatchNorm2d(c), nn.ReLU(),
                         *[ds_block(c) for _ in range(4)], nn.AdaptiveAvgPool2d(1), nn.Flatten(), nn.Linear(c, 2))


def crop(x: np.ndarray, centre: float) -> np.ndarray:
    a = int(round(centre * SR)) - WIN // 2
    w = x[max(0, a):a + WIN]
    return np.pad(w, (max(0, -a), WIN - len(w) - max(0, -a)))


def load() -> list[dict]:
    """Each recorder conversation: its audio, the "you know" middles (whose), and hard negatives ("you"/"know" alone)."""
    conn = db.connect()
    got = []
    for sess, ws in conversations(conn):
        if sess["source"] != "device":
            continue
        yk, alone = [], []
        skip = set()
        for i, (a, b) in enumerate(zip(ws, ws[1:])):
            if norm(a["w"]) == "you" and norm(b["w"]) == "know" and a["spk"] and a["spk"] == b["spk"]:
                yk.append(((a["start"] + b["end"]) / 2, a["me"]))
                skip |= {i, i + 1}
        for i, w in enumerate(ws):
            if i not in skip and norm(w["w"]) in ("you", "know", "no", "yeah"):
                alone.append((w["start"] + w["end"]) / 2)
        x = A.read(HOME / sess["path"], sess["start_s"], sess["end_s"])
        got.append({"id": sess["id"], "x": x, "yk": yk, "alone": alone})
    return got


def examples(convs: list[dict], rng: random.Random) -> tuple[torch.Tensor, torch.Tensor]:
    xs, ys = [], []
    for c in convs:
        dur = len(c["x"]) / SR
        mids = [m for m, _ in c["yk"]]
        far = lambda t: all(abs(t - m) > 0.7 for m in mids)
        for m in mids:
            for _ in range(3):  # jittered, as the window lands anywhere in a 0.25 s step
                xs.append(crop(c["x"], m + rng.uniform(-0.15, 0.15))); ys.append(1)
        hard = [t for t in c["alone"] if far(t)]
        for t in rng.sample(hard, min(len(hard), len(mids) * NEG_PER_POS // 2)):
            xs.append(crop(c["x"], t + rng.uniform(-0.15, 0.15))); ys.append(0)
        n = 0
        while n < max(20, len(mids) * NEG_PER_POS):
            t = rng.uniform(0.5, dur - 0.5)
            if far(t):
                xs.append(crop(c["x"], t)); ys.append(0); n += 1
    return torch.tensor(np.stack(xs)), torch.tensor(ys)


def stream(net: nn.Module, x: np.ndarray, dev) -> tuple[np.ndarray, np.ndarray]:
    """The recorder's view: P("you know") for a 1 s window every 0.25 s; (centre times, scores)."""
    starts = np.arange(0, max(1, len(x) - WIN + 1), HOP)
    scores = []
    with torch.no_grad():
        for i in range(0, len(starts), 512):
            b = torch.tensor(np.stack([x[s:s + WIN] for s in starts[i:i + 512]]))
            scores.append(torch.softmax(net(features(b).to(dev)), 1)[:, 1].cpu().numpy())
    return (starts + WIN / 2) / SR, np.concatenate(scores)


def cues(t: np.ndarray, p: np.ndarray, thr: float, run: int = 1) -> list[float]:
    """Cue times: `run` windows in a row at or over the threshold, cues closer than MERGE_S as one."""
    out: list[float] = []
    for i, ti in enumerate(t):
        if i + 1 >= run and all(p[i - k] >= thr for k in range(run)) and (not out or ti - out[-1] > MERGE_S):
            out.append(ti)
    return out


def main() -> None:
    torch.manual_seed(20)
    rng = random.Random(20)
    dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    convs = load()
    rng.shuffle(convs)
    total = sum(len(c["yk"]) for c in convs)
    test, n = [], 0
    for c in convs:  # whole conversations held out, about HELD_OUT of the "you know"s
        if n < total * HELD_OUT:
            test.append(c); n += len(c["yk"])
    held = {c["id"] for c in test}
    train = [c for c in convs if c["id"] not in held]
    X, y = examples(train, rng)
    F = features(X)
    del X
    print(f"{len(convs)} recorder conversations; train {len(train)} ({int(y.sum())} positives incl. jitter, "
          f"{int((y == 0).sum())} negatives), test {len(test)} ({n} \"you know\")")

    net = fit(F, y, dev)
    print(f"{sum(p.numel() for p in net.parameters())} weights")
    hours = sum(len(c["x"]) for c in test) / SR / 3600
    print(f"test audio {hours * 60:.0f} min")
    print("round 1")
    rows, wrong_at = evaluate(net, test, dev, hours)
    (OUT / "step2.json").write_text(json.dumps({"hours": hours, "rows": rows, "wrong": wrong_at}, indent=1))

    # Round 2 (20 Oct, after Jay heard 37 of 40 unexplained cues as no "you know"): the transcript's negatives are
    # right, so the model's own false cues on the training conversations become hard negatives.
    hard = []
    for c in train:
        t, p = stream(net, c["x"], dev)
        for q in cues(t, p, 0.5):
            if all(abs(q - m) > 0.7 for m, _ in c["yk"]):
                hard += [crop(c["x"], q + rng.uniform(-0.1, 0.1)) for _ in range(2)]
    Fh = features(torch.tensor(np.stack(hard)))
    print(f"round 2: {len(hard)} hard negatives added")
    net = fit(torch.cat([F, Fh]), torch.cat([y, torch.zeros(len(hard), dtype=y.dtype)]), dev)
    rows2, _ = evaluate(net, test, dev, hours)
    print("round 2, two windows in a row")
    rows3, _ = evaluate(net, test, dev, hours, run=2)
    torch.save(net.state_dict(), OUT / "model.pt")
    (OUT / "step3.json").write_text(json.dumps({"hours": hours, "round2": rows2, "round2_two": rows3}, indent=1))


def fit(F: torch.Tensor, y: torch.Tensor, dev) -> nn.Module:
    torch.manual_seed(20)
    net = model().to(dev)
    opt = torch.optim.Adam(net.parameters(), 1e-3)
    w = torch.tensor([1.0, float((y == 0).sum() / y.sum())]).to(dev)
    for epoch in range(25):
        net.train()
        perm = torch.randperm(len(y))
        for i in range(0, len(y), 128):
            j = perm[i:i + 128]
            fb = F[j]
            fb = fb + 0.1 * torch.randn_like(fb)  # a little noise against the room
            loss = nn.functional.cross_entropy(net(fb.to(dev)), y[j].to(dev), weight=w)
            opt.zero_grad(); loss.backward(); opt.step()
    return net.eval()


def evaluate(net: nn.Module, test: list[dict], dev, hours: float, run: int = 1) -> tuple[list[dict], list]:
    scored = [(c, *stream(net, c["x"], dev)) for c in test]
    rows, wrong_at = [], []
    for thr in (0.5, 0.7, 0.8, 0.9, 0.95, 0.98):
        hit_me = hit_other = n_me = n_other = wrong = 0
        for c, t, p in scored:
            cs = cues(t, p, thr, run)
            used = set()
            for m, me in c["yk"]:
                k = next((k for k, q in enumerate(cs) if k not in used and abs(q - m) <= MATCH_S), None)
                n_me += me; n_other += not me
                if k is not None:
                    used.add(k); hit_me += me; hit_other += not me
            wrong += len(cs) - len(used)
            if thr == 0.9:
                wrong_at += [(c["id"], q) for k, q in enumerate(cs) if k not in used]
        rows.append({"threshold": thr, "his_caught": f"{hit_me}/{n_me}", "others_caught": f"{hit_other}/{n_other}",
                     "other_cues_per_hour": round(wrong / hours, 1)})
        print(rows[-1])
    return rows, wrong_at


if __name__ == "__main__":
    main()
