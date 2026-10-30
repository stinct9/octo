"""SPIKE (B20, step 3d): play clips of the owner's "you know"s through the Mac's speaker for the recorder to catch
(`board.py live` running in another terminal). Logs when each "you know" played; `board.py` logs the cues; `match`
lines them up. Prefers conversations the board's model didn't train on (phone memos, recorder ones off the line).
Nothing leaves the Mac: clips go to HOME/data/bench/b20-fillers/play/ and are played with afplay.

    OCTO_HOME=... .venv/bin/python spikes/b20-filler-cue/play.py play [N]
    .venv/bin/python spikes/b20-filler-cue/play.py match LIVE.json
"""
import json
import random
import subprocess
import sys
import time

import numpy as np

from clips import OUT

BEFORE, AFTER = 4.0, 2.0   # each clip: some talk before the phrase, a little after
GAP_S = 3.0
DIR = OUT / "play"


def play(n: int) -> None:
    from octo import audio as A
    from experiment import load_all, SR

    convs = load_all()
    rng = random.Random(21)
    held = [c for c in convs if not c["test"]]
    trained = [c for c in convs if c["test"]]
    picks = []
    for pool in (held, trained):  # one per conversation, held-out ones first
        rng.shuffle(pool)
        for c in pool:
            mine = [m for m, me in c["yk"] if me and BEFORE < m < len(c["x"]) / SR - AFTER]
            if mine and len(picks) < n:
                picks.append((c, rng.choice(mine), not c["test"]))
    DIR.mkdir(parents=True, exist_ok=True)
    log = []
    for i, (c, m, held_out) in enumerate(picks):
        a, b = int((m - BEFORE) * SR), int((m + AFTER) * SR)
        path = DIR / f"p{i:02d}.wav"
        A.write(path, A.for_listening(c["x"][a:b].astype(np.float32)))
        t0 = time.time()
        subprocess.run(["afplay", str(path)], check=True)
        log.append({"clip": path.name, "session_id": c["id"], "held_out": held_out, "played_at": t0,
                    "phrase_mid": t0 + BEFORE})
        print(f"{i + 1}/{len(picks)} {'held out' if held_out else 'trained on'}: \"you know\" at "
              f"{time.strftime('%H:%M:%S', time.localtime(t0 + BEFORE))}", flush=True)
        time.sleep(GAP_S)
    (DIR / "played.json").write_text(json.dumps(log, indent=1))


def match(live_path: str) -> None:
    rows = [r for r in json.loads(open(live_path).read()) if "score" in r]
    played = json.loads((DIR / "played.json").read_text())
    t = np.array([r["mac_t"] for r in rows])
    p = np.array([r["score"] for r in rows])
    for thr in (0.5, 0.8, 0.9, 0.95):
        hits, lat = 0, []
        for e in played:
            # the window's line reaches the Mac after the window ends; a cue within 1.5 s after the phrase's middle
            k = np.where((t >= e["phrase_mid"]) & (t <= e["phrase_mid"] + 1.5) & (p >= thr))[0]
            if len(k):
                hits += 1
                lat.append(t[k[0]] - e["phrase_mid"])
        span = [(e["played_at"], e["played_at"] + BEFORE + AFTER) for e in played]
        inside = np.array([any(a <= x <= b for a, b in span) for x in t])
        near = np.array([any(abs(x - e["phrase_mid"]) <= 1.5 for e in played) for x in t])
        other = int(((p >= thr) & inside & ~near).sum())
        print(f"threshold {thr}: caught {hits}/{len(played)}, median {np.median(lat) if lat else float('nan'):.2f} s "
              f"after the phrase's middle; windows over it elsewhere in the clips: {other}")
    for e in played:
        k = np.where((t >= e["phrase_mid"] - 1) & (t <= e["phrase_mid"] + 1.5))[0]
        print(f"{e['clip']} {'held out' if e['held_out'] else 'trained'}: best {p[k].max() if len(k) else float('nan'):.2f}")


if __name__ == "__main__":
    if sys.argv[1] == "play":
        play(int(sys.argv[2]) if len(sys.argv) > 2 else 8)
    else:
        match(sys.argv[2])
