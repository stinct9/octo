"""SPIKE (B22, step 2c): Whisper (30 s heard, ear.py) as the teacher, w32 (1 s, the chip's) as the student: does the
student learn better from the teacher's scores than from the transcript's points?

Kept honest, nested: for each held-out fold, the student's two training folds are scored by a teacher trained on the
other of the two, so no teacher ever saw the held-out conversations. The student trains on every window with the
teacher's score as its target. Scored as experiment.py scores (one and two windows in a row).

    OCTO_HOME=... .venv/bin/python spikes/b20-filler-cue/distill.py
"""
import json

import numpy as np
import torch
import torch.nn as nn

from clips import OUT
from ear import folds_of, prepare, result, score, tally, teacher
from experiment import DECODES, HOP, THRESHOLDS, model, stream
from train import SR, features

EPOCHS = 8


def student_set(convs: list[dict], soft: dict) -> tuple[torch.Tensor, torch.Tensor]:
    """Every window of the student's training conversations (as `stream` cuts them) and the teacher's score."""
    F, Q = [], []
    for c in convs:
        starts = np.arange(0, max(1, len(c["x"]) - SR + 1), HOP)
        q = soft[c["id"]]
        assert len(q) == len(starts), (c["id"], len(q), len(starts))
        for i in range(0, len(starts), 2048):
            F.append(features(torch.tensor(np.stack([c["x"][s:s + SR].astype(np.float32) for s in starts[i:i + 2048]]))))
        Q.append(torch.tensor(q, dtype=torch.float32))
    return torch.cat(F), torch.cat(Q)


def fit_soft(F: torch.Tensor, q: torch.Tensor, seed: int, dev) -> nn.Module:
    torch.manual_seed(20 + seed)
    net = model(32, 4).to(dev)
    opt = torch.optim.Adam(net.parameters(), 1e-3)
    ratio = float((q < 0.5).sum() / max(1, (q >= 0.5).sum()))  # the positives' weight, as fit's class weight
    for _ in range(EPOCHS):
        net.train()
        perm = torch.randperm(len(q))
        for i in range(0, len(q), 128):
            j = perm[i:i + 128]
            f = F[j]
            lp = torch.log_softmax(net((f + 0.1 * torch.randn_like(f)).to(dev)), 1)
            qj = q[j].to(dev)
            loss = -(ratio * qj * lp[:, 1] + (1 - qj) * lp[:, 0]).mean()
            opt.zero_grad(); loss.backward(); opt.step()
    return net.eval()


def main() -> None:
    dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    from experiment import load_all
    convs = [c for c in load_all() if c["test"]]
    prepare(convs, short=False, keep_audio=True)
    folds = folds_of(convs)
    results = []
    for seed in range(3):
        tots = {d: {t: [0, 0, 0] for t in THRESHOLDS} for d in DECODES}
        hours = 0.0
        for k, test in enumerate(folds):
            a, b = [f for i, f in enumerate(folds) if i != k]
            soft = {}
            for lab, sc in ((b, a), (a, b)):
                net = teacher(lab, seed, dev)
                soft.update({c["id"]: score(net, c, dev) for c in sc})
            F, q = student_set(a + b, soft)
            stu = fit_soft(F, q, seed, dev)
            del F
            for c in test:
                hours += c["dur"] / 3600
                t, p = stream(stu, c["x"], SR, dev)
                tally(tots, c, t, p)
            print(f"  seed {seed} fold {k + 1}/3: {len(q)} windows, {(q >= 0.5).sum().item()} over 0.5", flush=True)
        results.append(result("w32_distilled", seed, tots, hours, len(convs)))
    (OUT / "distill.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
