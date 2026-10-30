"""B22 step 3: the loop's bench. Is a model retrained on every recorder conversation to date better than the installed
one? Both recipes (w32, his voice only) on the same 3 folds x 3 seeds of today's conversations (experiment.py): "before"
trains only on the conversations the installed model had, "now" on all of them. Better only beyond the seed spread:
now's worst seed above before's best, at the chip's operating point (<= WRONG_BUDGET wrong cues an hour, at a threshold
int8 can tell apart). Run by `octo filler train` (octo/filler_loop.py) in the lab's venv; writes OUT.json.

    OCTO_HOME=... .venv/bin/python spikes/b20-filler-cue/loop.py OUT.json BEFORE.json
BEFORE.json: {"ids": [...]} (the installed model's conversations) or {"until": EPOCH} (recorded before then).
"""
import json
import statistics
import sys
from pathlib import Path

import torch

from experiment import FOLDS, SEEDS, WRONG_BUDGET, load_all, run

CHIP_MAX = 0.996  # int8 softmax's last step on the chip (1/256 below 1)
FLOOR = 1 / 3     # B29: a first model for a filler (nothing installed to beat) is offered only if it catches this much


def operating_point(curve: list[dict], his_total: int, hours: float) -> tuple[float, float | None]:
    """(his share caught, threshold) at the highest catch with <= WRONG_BUDGET wrong an hour the chip can use."""
    ok = [(r["his"] / his_total, r["threshold"]) for r in curve
          if r["threshold"] <= CHIP_MAX and r["wrong"] / hours <= WRONG_BUDGET]
    return max(ok, default=(0.0, None))


def verdict(before: list[float], now: list[float]) -> bool:
    """Better beyond the seed spread: now's worst seed above before's best."""
    return min(now) > max(before)


def main(out: Path, before_spec: dict) -> None:
    dev = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    convs = load_all()
    line = [c for c in convs if c["test"]]
    before = ({c["id"] for c in line if c["id"] in set(before_spec["ids"])} if "ids" in before_spec
              else {c["id"] for c in line if (c["started_at"] or 0) < before_spec["until"]})
    got: dict[str, list[dict]] = {"before": [], "now": []}
    runs = (2 if before else 1) * SEEDS * FOLDS
    print(json.dumps({"plan": {"trainings": runs, "conversations": len(line),
                               "examples": sum(1 for c in line for _, me in c["yk"] if me)}}), flush=True)
    for which, allowed in (("before", before), ("now", None)) if before else (("now", None),):  # no model yet: any
        for seed in range(SEEDS):
            r = run(convs, "w32", dev, seed, allowed, tag=f"loop_{which}")
            caught, thr = operating_point(r["run1"]["curve"], int(r["his"].split("/")[1]), r["hours"])
            got[which].append({"seed": seed, "caught": round(caught, 3), "threshold": thr,
                               "wrong_at_recall": r["run1"]["wrong_at_recall"]})
    b, n = [g["caught"] for g in got["before"]], [g["caught"] for g in got["now"]]
    thr = [g["threshold"] for g in got["now"] if g["threshold"] is not None]
    res = {"conversations": len(line), "before_conversations": len(before), "hours": r["hours"],
           "his": int(r["his"].split("/")[1]), "before": got["before"], "now": got["now"],
           "before_mean": round(statistics.mean(b), 3) if b else 0.0, "now_mean": round(statistics.mean(n), 3),
           "better": (verdict(b, n) and len(before) < len(line)) if b else statistics.mean(n) >= FLOOR,
           "threshold": round(statistics.median(thr), 3) if thr else None}
    out.write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k not in ("before", "now")}), flush=True)


if __name__ == "__main__":
    main(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_text()))
