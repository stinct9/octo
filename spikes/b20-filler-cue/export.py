"""SPIKE (B20, step 3a): the candidate model (his voice only, one round of hard negatives) trained on all 21 recorder
conversations on the line, and what the board needs to check it: calibration features for int8, check windows with
the PyTorch scores, and the mel filterbank. Then `convert.py` (TensorFlow, a scratch venv) makes the int8 model.

    OCTO_HOME=... .venv/bin/python spikes/b20-filler-cue/export.py [VARIANT [OUT_DIR THRESHOLD]]   # his_only (default)

B22's loop (`octo filler train`) passes w32, its own folder and the threshold its bench chose.
"""
import json
import random
import sys
from pathlib import Path

import numpy as np
import torch

from experiment import BASE, VARIANTS, crop, cues, examples, fit, load_all, stream
from train import OUT, SR, features, mel

# Per variant, from the folds (learnings, 21 Oct): his_only 0.8 catches ~62% of his at ~2.4 wrong cues an hour;
# w48 0.95 ~71% at 2.4; w32 0.95 ~66% at 3.3.
THRESHOLDS = {"his_only": 0.8, "w48": 0.95, "w32": 0.95}


def main(variant: str, out: str | None = None, threshold: float | None = None) -> None:
    dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    cfg = {**BASE, **VARIANTS[variant]}
    win = SR
    rng = random.Random(20)
    train = [c for c in load_all() if c["test"]]
    xs, ys = examples(train, cfg, rng, win)
    F, y = features(torch.tensor(np.stack(xs))), torch.tensor(ys)
    net = fit(F, y, cfg, dev)
    hard = []
    for c in train:
        t, p = stream(net, c["x"], win, dev)
        hard += [crop(c["x"], q + rng.uniform(-0.1, 0.1), win) for q in cues(t, p, 0.5)
                 if all(abs(q - m) > 0.7 for m, _ in c["yk"]) for _ in range(2)]
    xs += hard
    F = torch.cat([F, features(torch.tensor(np.stack(hard)))])
    y = torch.cat([y, torch.zeros(len(hard), dtype=y.dtype)])
    net = fit(F, y, cfg, dev).cpu()
    print(f"trained on {len(train)} conversations, {len(y)} examples")

    THRESHOLD = threshold if threshold is not None else THRESHOLDS[variant]
    d = Path(out) if out else OUT / ("board" if variant == "his_only" else f"board-{variant}")
    d.mkdir(parents=True, exist_ok=True)
    torch.save(net.state_dict(), d / "final.pt")
    np.savez(d / "final.npz", **{k: v.numpy() for k, v in net.state_dict().items()})
    (d / "meta.json").write_text(json.dumps({"variant": variant, "down": cfg["down"], "threshold": THRESHOLD,
                                             "conversations": sorted(c["id"] for c in train)}))
    idx = rng.sample(range(len(y)), 1000)
    np.save(d / "calib.npy", F[idx].numpy())
    # Check windows: half "you know" (his), half not, as the samples the board will get (int16).
    pos = [i for i in range(len(y)) if y[i] == 1]
    neg = [i for i in range(len(y)) if y[i] == 0]
    pick = rng.sample(pos, 100) + rng.sample(neg, 100)
    xw = np.stack([np.clip(np.round(xs[i] * 32768), -32768, 32767).astype(np.int16) for i in pick])
    with torch.no_grad():
        p = torch.softmax(net(features(torch.tensor(xw.astype(np.float32) / 32768))), 1)[:, 1].numpy()
    np.save(d / "check_x.npy", xw)
    np.save(d / "check_p.npy", p)
    np.save(d / "fb.npy", mel.mel_scale.fb.numpy())  # (257, 40)
    print(f"check windows: {len(pick)}, positives over {THRESHOLD}: {(p[:100] >= THRESHOLD).mean():.0%}, "
          f"negatives over: {(p[100:] >= THRESHOLD).mean():.0%}")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0] if a else "his_only", a[1] if len(a) > 1 else None, float(a[2]) if len(a) > 2 else None)
