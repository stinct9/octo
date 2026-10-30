"""SPIKE (B20, step 2b): what cuts the wrong cues? Each variant trained and scored the same way, over 3 folds of the
recorder conversations on the fillers' line (each held out once), with any extra conversations only ever in
training. Reported at the threshold where 80% of his "you know"s are caught, pooled over the folds.
B22 (21 Oct): each variant over SEEDS training seeds (same folds), and for one and two windows in a row: his share
caught at <= 3 wrong cues an hour, and wrong cues an hour at 75% of his caught. Results in experiments2.json.

    OCTO_HOME=... .venv/bin/python spikes/b20-filler-cue/experiment.py [VARIANT ...]
"""
import json
import os
import random
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

from clips import OUT, conversations, norm
from octo import HOME, db
from octo import audio as A
from octo import fillers
from octo import filler_loop
from train import MATCH_S, MERGE_S, SR, features

# B29: the filler trained for (OCTO_FILLER, set by octo/filler_loop.py), "you know" by default. The variable and field
# names below still say "you know" (yk): they mean the target. Lone words that sound like part of it are hard negatives.
TARGET = os.environ.get("OCTO_FILLER") or filler_loop.YOU_KNOW
ALONE = {filler_loop.YOU_KNOW: ("you", "know", "no", "yeah"), filler_loop.HESITATION: ("a", "oh", "i", "and")}
ALONE_WORDS = ALONE.get(TARGET) or tuple({w for p in filler_loop.phrases_of(TARGET) for w in p.split()})

HOP = SR // 4
FOLDS = 3
RECALL = 0.80
THRESHOLDS = sorted({round(float(x), 3) for x in np.concatenate([np.arange(0.02, 0.3, 0.02), np.arange(0.3, 0.9, 0.05),
                                                          np.arange(0.9, 0.999, 0.005),
                                                          # past int8 softmax's last step (1/256): needs the logit on the chip
                                                          [0.996, 0.997, 0.998, 0.999, 0.9995, 0.9998, 0.9999]])})

BASE = {"train_share": 1.0, "extra": False, "mine_rounds": 1, "others": "positive", "width": 64, "blocks": 4, "win_s": 1.0,
        "down": False}
VARIANTS = {
    "base": {},
    "round3": {"mine_rounds": 2},
    "more_data": {"extra": True},
    "his_only": {"others": "negative"},
    "bigger": {"width": 96, "blocks": 5},
    "win1.5": {"win_s": 1.5},
    "his_only_half": {"others": "negative", "train_share": 0.5},  # a learning curve: would more conversations help?
    # For the board (21 Oct): his_only took 105 ms a window there; the first block halves height and width, so the
    # other three do a quarter of the work.
    "fast": {"others": "negative", "down": True},  # far worse (learnings, 21 Oct)
    "w48": {"others": "negative", "width": 48},
    "w32": {"others": "negative", "width": 32},
    # B22 step 1 (21 Oct): cheap wins on the board's model (w32), each one change, scored over SEEDS.
    "w32_specaug": {"others": "negative", "width": 32, "specaug": True},
    "w32_mix": {"others": "negative", "width": 32, "mix": True},
    "w32_more_neg": {"others": "negative", "width": 32, "extra": "negatives"},
    "w32_libri": {"others": "negative", "width": 32, "extra": "libri"},  # read speech, as negatives only
    # Trained on every window, as ear.py's head is: separates its recipe from Whisper's ear.
    "w32_dense": {"others": "negative", "width": 32, "dense": True, "mine_rounds": 0, "epochs": 8},
}
LIBRI = Path.home() / "Library/Caches/octo-b22/ls/LibriSpeech/dev-clean"  # OpenSLR 12, CC BY 4.0; kept out of the lab
SEEDS = 3
DECODES = (1, 2)       # windows in a row over the threshold before a cue
WRONG_BUDGET = 3.0     # summary: his caught at no more than this many wrong cues an hour
RECALL_AT = 0.75       # summary: wrong cues an hour at this share of his caught


def ds_block(c: int, stride: int = 1) -> nn.Sequential:
    return nn.Sequential(nn.Conv2d(c, c, 3, stride=stride, padding=1, groups=c), nn.BatchNorm2d(c), nn.ReLU(),
                         nn.Conv2d(c, c, 1), nn.BatchNorm2d(c), nn.ReLU())


def load_all() -> list[dict]:
    """Every English conversation with lines and kept audio: (id, audio as float16, "you know" middles and whose,
    lone you/know/no/yeah, whether it's a recorder conversation on the fillers' line)."""
    conn = db.connect()
    on_line = {c["session_id"]: c["on_line"] for c in fillers.all_conversations(conn)}
    got = []
    for sess, ws in conversations(conn, on_line_only=False):
        _, _, _, tr = fillers.load(conn, sess["id"])
        if not tr or tr.get("language") != "en" or not ws:
            continue
        found = filler_loop.hits(ws, TARGET)
        yk = [((ws[i]["start"] + ws[i + k - 1]["end"]) / 2, ws[i]["me"]) for i, k in found]
        skip = {j for i, k in found for j in range(i, i + k)}
        alone = [(w["start"] + w["end"]) / 2 for i, w in enumerate(ws)
                 if i not in skip and norm(w["w"]) in ALONE_WORDS]
        x = A.read(HOME / sess["path"], sess["start_s"], sess["end_s"]).astype(np.float16)
        got.append({"id": sess["id"], "x": x, "yk": yk, "alone": alone, "started_at": sess["started_at"],
                    "test": sess["source"] == "device" and on_line.get(sess["id"], False)})
    return got


def load_libri() -> list[dict]:
    """One pseudo-conversation per LibriSpeech speaker, skipping any utterance with "you know" in it."""
    import soundfile as sf
    got = []
    for spk in sorted(LIBRI.iterdir()):
        xs = []
        for tr in sorted(spk.glob("*/*.trans.txt")):
            for line in tr.read_text().splitlines():
                utt, text = line.split(" ", 1)
                if "YOU KNOW" not in text:
                    xs.append(sf.read(str(tr.parent / f"{utt}.flac"), dtype="float32")[0])
        got.append({"id": f"libri-{spk.name}", "x": np.concatenate(xs).astype(np.float16), "yk": [], "alone": [],
                    "test": False, "libri": True})
    return got


def extra(c: dict, cfg: dict) -> bool:
    """Whether a conversation off the line is in this variant's training."""
    if c.get("libri"):
        return cfg["extra"] == "libri"
    return cfg["extra"] in (True, "negatives")


def crop(x: np.ndarray, centre: float, win: int) -> np.ndarray:
    a = int(round(centre * SR)) - win // 2
    w = x[max(0, a):a + win].astype(np.float32)
    return np.pad(w, (max(0, -a), win - len(w) - max(0, -a)))


def model(width: int, blocks: int, down: bool = False) -> nn.Module:
    return nn.Sequential(nn.Conv2d(1, width, (5, 3), stride=(2, 2), padding=(2, 1)), nn.BatchNorm2d(width), nn.ReLU(),
                         *[ds_block(width, 2 if down and i == 0 else 1) for i in range(blocks)],
                         nn.AdaptiveAvgPool2d(1), nn.Flatten(), nn.Linear(width, 2))


def mix(w: np.ndarray, convs: list[dict], rng: random.Random) -> np.ndarray:
    """w with a second of another conversation under it, 5-20 dB quieter: other talk in the room."""
    o = convs[rng.randrange(len(convs))]
    b = crop(o["x"], rng.uniform(0.5, len(o["x"]) / SR - 0.5), len(w))
    snr = rng.uniform(5, 20)
    return w + b * np.sqrt(((w ** 2).mean() + 1e-9) / (((b ** 2).mean() + 1e-9) * 10 ** (snr / 10)))


def examples(convs: list[dict], cfg: dict, rng: random.Random, win: int) -> tuple[list, list]:
    xs, ys = [], []
    for c in convs:
        if not c["test"] and cfg["extra"] in ("negatives", "libri"):  # only moments well away from any "you know"
            mids = [m for m, _ in c["yk"]]
            dur = len(c["x"]) / SR
            for _ in range(150 if c.get("libri") else 60):
                t = rng.uniform(0.5, dur - 0.5)
                if all(abs(t - m) > 1.5 for m in mids):
                    xs.append(crop(c["x"], t, win)); ys.append(0)
            continue
        dur = len(c["x"]) / SR
        mids = [m for m, _ in c["yk"]]
        far = lambda t: all(abs(t - m) > 0.7 for m in mids)
        for m, me in c["yk"]:
            if not me and cfg["others"] == "negative":
                xs += [crop(c["x"], m + rng.uniform(-0.15, 0.15), win) for _ in range(2)]; ys += [0, 0]
                continue
            xs += [crop(c["x"], m + rng.uniform(-0.15, 0.15), win) for _ in range(3)]; ys += [1, 1, 1]
        hard = [t for t in c["alone"] if far(t)]
        for t in rng.sample(hard, min(len(hard), len(mids) * 4)):
            xs.append(crop(c["x"], t + rng.uniform(-0.15, 0.15), win)); ys.append(0)
        n = 0
        while n < max(20, len(mids) * 8):
            t = rng.uniform(0.5, dur - 0.5)
            if far(t):
                xs.append(crop(c["x"], t, win)); ys.append(0); n += 1
    if cfg.get("mix"):  # a mixed copy of every positive and of as many negatives, so mixing alone tells nothing
        pos = [i for i, y in enumerate(ys) if y == 1]
        neg = rng.sample([i for i, y in enumerate(ys) if y == 0], len(pos))
        for i in pos + neg:
            xs.append(mix(xs[i], convs, rng)); ys.append(ys[i])
    return xs, ys


def dense(convs: list[dict], win: int) -> tuple[torch.Tensor, torch.Tensor]:
    """Every window the recorder would score (as `stream`): 1 within 0.15 s of his "you know", 0 well away from
    anyone's (others' within 0.15 s too: his_only), the rest left out."""
    F, Y = [], []
    for c in convs:
        starts = np.arange(0, max(1, len(c["x"]) - win + 1), HOP)
        t = (starts + win / 2) / SR
        y = np.zeros(len(t), dtype=np.int64)
        for m, _ in c["yk"]:
            y[np.abs(t - m) <= 0.7] = -1
        for m, me in sorted(c["yk"], key=lambda k: k[1]):
            y[np.abs(t - m) <= 0.15] = int(me)
        keep = starts[y >= 0]
        for i in range(0, len(keep), 2048):
            F.append(features(torch.tensor(np.stack([c["x"][s:s + win].astype(np.float32) for s in keep[i:i + 2048]]))))
        Y.append(torch.tensor(y[y >= 0]))
    return torch.cat(F), torch.cat(Y)


def specaug(f: torch.Tensor) -> torch.Tensor:
    """Each example with up to 6 mel bands and up to 8 frames set to its mean (0 after the features' mean)."""
    n, _, M, T = f.shape
    m0, mw = torch.randint(0, M, (n, 1)), torch.randint(0, 7, (n, 1))
    t0, tw = torch.randint(0, T, (n, 1)), torch.randint(0, 9, (n, 1))
    bands = (torch.arange(M) >= m0) & (torch.arange(M) < m0 + mw)
    frames = (torch.arange(T) >= t0) & (torch.arange(T) < t0 + tw)
    return f.masked_fill((bands[:, :, None] | frames[:, None, :])[:, None], 0.0)


def fit(F: torch.Tensor, y: torch.Tensor, cfg: dict, dev) -> nn.Module:
    torch.manual_seed(20 + cfg.get("seed", 0))
    net = model(cfg["width"], cfg["blocks"], cfg["down"]).to(dev)
    opt = torch.optim.Adam(net.parameters(), 1e-3)
    w = torch.tensor([1.0, float((y == 0).sum() / y.sum())]).to(dev)
    for _ in range(cfg.get("epochs", 25)):
        net.train()
        perm = torch.randperm(len(y))
        for i in range(0, len(y), 128):
            j = perm[i:i + 128]
            f = specaug(F[j]) if cfg.get("specaug") else F[j]
            loss = nn.functional.cross_entropy(net((f + 0.1 * torch.randn_like(f)).to(dev)), y[j].to(dev), weight=w)
            opt.zero_grad(); loss.backward(); opt.step()
    return net.eval()


def stream(net: nn.Module, x: np.ndarray, win: int, dev) -> tuple[np.ndarray, np.ndarray]:
    starts = np.arange(0, max(1, len(x) - win + 1), HOP)
    scores = []
    with torch.no_grad():
        for i in range(0, len(starts), 512):
            b = torch.tensor(np.stack([x[s:s + win].astype(np.float32) for s in starts[i:i + 512]]))
            scores.append(torch.softmax(net(features(b).to(dev)), 1)[:, 1].cpu().numpy())
    return (starts + win / 2) / SR, np.concatenate(scores)


def cues(t: np.ndarray, p: np.ndarray, thr: float, run: int = 1) -> list[float]:
    """Cue times: `run` windows in a row at or over thr; cues closer than MERGE_S are one."""
    out: list[float] = []
    k = 0
    for ti, pi in zip(t, p):
        k = k + 1 if pi >= thr else 0
        if k >= run and (not out or ti - out[-1] > MERGE_S):
            out.append(ti)
    return out


def summary(curve: list[dict], his_total: int, hours: float) -> dict:
    """His share caught at no more than WRONG_BUDGET wrong an hour; wrong an hour at RECALL_AT of his caught."""
    caught = max([r["his"] / his_total for r in curve if r["wrong"] / hours <= WRONG_BUDGET], default=0.0)
    wrong = min([r["wrong"] / hours for r in curve if r["his"] >= RECALL_AT * his_total], default=float("inf"))
    return {"caught_at_budget": round(caught, 3), "wrong_at_recall": round(wrong, 1)}


def run(convs: list[dict], name: str, dev, seed: int = 0, allowed: set[str] | None = None, tag: str | None = None) -> dict:
    """`allowed`: train only on these conversations (B22's loop: the data the installed model had); the held-out
    folds stay the same. `tag` names the saved scores (the variant's name by default)."""
    cfg = {**BASE, **VARIANTS[name], "seed": seed}
    win = int(cfg["win_s"] * SR)
    tests = [c for c in convs if c["test"]]
    rng = random.Random(20)
    rng.shuffle(tests)
    folds = [tests[i::FOLDS] for i in range(FOLDS)]  # the same split for every seed and variant
    rng = random.Random(20 + seed)
    # per decode and threshold, pooled over folds: his caught, his total, others' caught, others' total, wrong cues
    tots = {d: {t: [0, 0, 0, 0, 0] for t in THRESHOLDS} for d in DECODES}
    hours = 0.0
    scores = {}  # every held-out window's score, so another grid or decode costs no training
    for k, test in enumerate(folds):
        held = {c["id"] for c in test}
        train = [c for c in convs if c["id"] not in held and (c["test"] or extra(c, cfg))
                 and (allowed is None or c["id"] in allowed)]
        train = train[:max(1, round(len(train) * cfg["train_share"]))]
        if cfg.get("dense"):
            F, y = dense(train, win)
        else:
            xs, ys = examples(train, cfg, rng, win)
            F, y = features(torch.tensor(np.stack(xs))), torch.tensor(ys)
            del xs
        net = fit(F, y, cfg, dev)
        for _ in range(cfg["mine_rounds"]):
            hard = []
            for c in train:
                t, p = stream(net, c["x"], win, dev)
                hard += [crop(c["x"], q + rng.uniform(-0.1, 0.1), win) for q in cues(t, p, 0.5)
                         if all(abs(q - m) > 0.7 for m, _ in c["yk"]) for _ in range(2)]
            F = torch.cat([F, features(torch.tensor(np.stack(hard)))])
            y = torch.cat([y, torch.zeros(len(hard), dtype=y.dtype)])
            net = fit(F, y, cfg, dev)
        for c in test:
            hours += len(c["x"]) / SR / 3600
            t, p = stream(net, c["x"], win, dev)
            scores[f"{c['id']}_t"], scores[f"{c['id']}_p"] = t, p
            for d, tot in tots.items():
                for thr in THRESHOLDS:
                    cs, used = cues(t, p, thr, d), set()
                    for m, me in c["yk"]:
                        j = next((j for j, q in enumerate(cs) if j not in used and abs(q - m) <= MATCH_S), None)
                        tot[thr][1 if me else 3] += 1
                        if j is not None:
                            used.add(j); tot[thr][0 if me else 2] += 1
                    tot[thr][4] += len(cs) - len(used)
        print(f"  {name} fold {k + 1}/{FOLDS}: train {len(train)} conversations, {len(y)} examples", flush=True)
    (OUT / "b22-scores").mkdir(exist_ok=True)
    np.savez_compressed(OUT / "b22-scores" / f"{tag or name}-s{seed}.npz", **scores)
    tot = tots[1]
    ok = [t for t in THRESHOLDS if tot[t][0] >= RECALL * tot[t][1]]
    thr = max(ok) if ok else min(THRESHOLDS)
    h, nh, o, no, wrong = tot[thr]
    weights = sum(p.numel() for p in model(cfg["width"], cfg["blocks"], cfg["down"]).parameters())
    res = {"variant": name, "threshold": thr, "his": f"{h}/{nh}", "others": f"{o}/{no}",
           "wrong_per_hour": round(wrong / hours, 1), "others_per_hour": round(o / hours, 1),
           "his_per_hour": round(nh / hours, 1), "hours": round(hours, 2), "weights": weights,
           "seed": seed, "convs": len(tests)}
    for d, tt in tots.items():
        curve = [{"threshold": t, "his": tt[t][0], "others": tt[t][2], "wrong": tt[t][4]} for t in THRESHOLDS]
        res[f"run{d}"] = {**summary(curve, tt[THRESHOLDS[0]][1], hours), "curve": curve}
    print({k: v for k, v in res.items() if not k.startswith("run")},
          {d: {k: v for k, v in res[f"run{d}"].items() if k != "curve"} for d in DECODES}, flush=True)
    return res


def main() -> None:
    names = sys.argv[1:] or list(VARIANTS)
    dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    convs = load_all()
    if any(VARIANTS[n].get("extra") == "libri" for n in names):
        convs += load_libri()
    print(f"{len(convs)} English conversations, {sum(c['test'] for c in convs)} recorder ones on the line", flush=True)
    results = [run(convs, n, dev, s) for n in names for s in range(SEEDS)]
    path = OUT / "experiments2.json"  # B22's bench: seeds and decodes (experiments.json is B20's)
    old = json.loads(path.read_text()) if path.exists() else []
    path.write_text(json.dumps(old + results, indent=1))


if __name__ == "__main__":
    main()
