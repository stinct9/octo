"""SPIKE (B20, step 3c): the kws sketch on the board, from the Mac.

    .venv/bin/python spikes/b20-filler-cue/board.py check DIR    # the check windows: board vs Mac scores, timing
    .venv/bin/python spikes/b20-filler-cue/board.py live SECONDS [OUT.json] [THRESHOLD]  # the board's mic, as it goes

DIR is data/bench/b20-fillers/board under the lab's HOME (convert.py's output).
"""
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bench.device import open_port  # noqa: E402

THRESHOLD = 0.95  # w32's (export.THRESHOLDS); `live SECONDS OUT THRESHOLD` to try another


def result(s, timeout: float = 5.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        line = s.readline().decode(errors="replace").strip()
        if line.startswith("@@ "):
            return json.loads(line[3:])
    raise TimeoutError("no @@ line")


def check(d: Path) -> None:
    xw = np.load(d / "check_x.npy")
    p_mac = np.load(d / "check_p_int8.npy")
    p_torch = np.load(d / "check_p.npy")
    thr = json.loads((d / "meta.json").read_text())["threshold"]
    with open_port() as s:
        s.write(b"i")
        print(result(s))
        got, feat, inf = [], [], []
        for x in xw:
            s.write(b"w" + x.astype("<i2").tobytes())
            r = result(s)
            got.append(r["score"]); feat.append(r["feat_us"]); inf.append(r["inf_us"])
    got = np.array(got)
    print(f"{len(got)} windows; board vs Mac int8: max |diff| {np.abs(got - p_mac).max():.4f}; "
          f"vs PyTorch {np.abs(got - p_torch).max():.4f}; same side of {thr} as PyTorch {((got >= thr) == (p_torch >= thr)).mean():.0%}")
    print(f"features {statistics.median(feat) / 1000:.1f} ms (max {max(feat) / 1000:.1f}), "
          f"model {statistics.median(inf) / 1000:.1f} ms (max {max(inf) / 1000:.1f}), per 250 ms step")


def live(seconds: float) -> None:
    with open_port() as s:
        s.write(b"l")
        end, rows = time.time() + seconds, []
        while time.time() < end:
            try:
                r = result(s, 2.0)
            except TimeoutError:
                continue
            r["mac_t"] = time.time()
            rows.append(r)
            if "score" not in r:
                continue
            # One line per window: its level and score as bars, so a quiet mic and a low score look different.
            lvl = "#" * max(0, min(30, int((r["db"] + 70) / 2)))
            sc = "=" * int(r["score"] * 20)
            cue = "  >>> YOU KNOW <<<" if r["score"] >= THRESHOLD else ""
            print(f"{time.strftime('%H:%M:%S')} {r['db']:6.1f} dB {lvl:<30} score {r['score']:.2f} {sc:<20}{cue}", flush=True)
        s.write(b"x")
    busy = [(r["feat_us"] + r["inf_us"]) / 1000 for r in rows if "feat_us" in r]
    print(f"{len(rows)} windows; work per window {statistics.median(busy):.1f} ms median, {max(busy):.1f} max "
          f"(of 240 ms); cues: {sum(r['score'] >= THRESHOLD for r in rows if 'score' in r)}")
    Path(sys.argv[3] if len(sys.argv) > 3 else "live.json").write_text(json.dumps(rows))


if __name__ == "__main__":
    if sys.argv[1] == "check":
        check(Path(sys.argv[2]).expanduser())
    else:
        THRESHOLD = float(sys.argv[4]) if len(sys.argv) > 4 else THRESHOLD
        live(float(sys.argv[2]))
