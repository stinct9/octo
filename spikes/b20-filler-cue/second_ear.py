"""SPIKE (20 Oct): does the transcript miss the owner's fillers? A second transcriber (Whisper large-v3, local)
over the owner's own stretches of the English conversations; every WORD it hears there that Qwen's transcript has
no copy of within 1 s is a candidate miss, cut for a check by ear (`clips.py serve`, /<word>/).

    OCTO_HOME=... .venv/bin/python spikes/b20-filler-cue/second_ear.py essentially
"""
import json
import sys

import numpy as np

from clips import OUT, conversations, norm, write_set
from octo import HOME, db
from octo import audio as A

MODEL = "mlx-community/whisper-large-v3-mlx"
GAP_S = 1.0     # his lines closer than this are one stretch
NEAR_S = 1.0    # Qwen's copy of the word within this counts as the same one


def stretches(ws: list[dict]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for w in ws:
        if not w["me"]:
            continue
        if out and w["start"] - out[-1][1] < GAP_S:
            out[-1][1] = w["end"]
        else:
            out.append([w["start"], w["end"]])
    return [(a, b) for a, b in out]


def main(word: str) -> None:
    import mlx_whisper
    from octo import fillers

    conn = db.connect()
    misses, both, qwen_only, seconds = [], 0, 0, 0.0
    for sess, ws in conversations(conn, on_line_only=False):
        _, _, _, tr = fillers.load(conn, sess["id"])
        if not tr or tr.get("language") != "en":
            continue
        length = sess["end_s"] - sess["start_s"]
        qwen = [w["start"] for w in ws if w["me"] and norm(w["w"]) == word]
        heard = []
        for a, b in stretches(ws):
            lo, hi = max(0.0, a - 0.3), min(length, b + 0.3)
            seconds += hi - lo
            x = A.read(HOME / sess["path"], sess["start_s"] + lo, sess["start_s"] + hi).astype(np.float32)
            r = mlx_whisper.transcribe(x, path_or_hf_repo=MODEL, language="en", word_timestamps=True,
                                       condition_on_previous_text=False)
            heard += [lo + w["start"] for s in r["segments"] for w in s.get("words", []) if norm(w["word"]) == word]
        for t in heard:
            if any(abs(t - q) <= NEAR_S for q in qwen):
                both += 1
            else:
                misses.append({"session_id": sess["id"], "path": sess["path"], "base": sess["start_s"],
                               "length": length, "start": t - 0.2, "end": t + 0.6, "app_says": "cue"})
        qwen_only += sum(1 for q in qwen if not any(abs(t - q) <= NEAR_S for t in heard))
        print(f"{sess['id'][:8]}: qwen {len(qwen)}, whisper {len(heard)}", flush=True)
    print(f"his speech {seconds / 3600:.1f} h; \"{word}\": both {both}, whisper only {len(misses)}, qwen only {qwen_only}")
    folder = OUT / word
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "summary.json").write_text(json.dumps({"hours": seconds / 3600, "both": both,
                                                     "whisper_only": len(misses), "qwen_only": qwen_only}))
    if misses:
        write_set(folder, misses, word)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "essentially")
