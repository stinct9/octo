"""B21 spike on the owner's own voice: Apple's live fillers (live.swift output) against the lab's transcript of the
same recording, counted with the lab's own matcher (octo.fillers.find) and the owner's list. Prints counts only,
never text. Run with the live lab's venv and OCTO_HOME; opens the database read-only.
Usage: python compare.py SESSION_ID RUN.jsonl"""
import json
import sqlite3
import sys
from collections import Counter

import octo
from octo import fillers, sessions

EXTRA = ["like", "basically"]  # not on his list; counted to see what Apple keeps


def main(sid, run):
    conn = sqlite3.connect(f"file:{octo.HOME}/data/octo.db?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    phr = [r[0] for r in conn.execute("SELECT phrase FROM filler_words WHERE removed_at IS NULL")] + EXTRA
    pat = fillers.matcher(phr)
    _, out, _ = sessions.diarized(conn, sid)
    lines = out["lines"]
    lab = [(l["start"], l["end"], k) for l in lines for _, _, k in fillers.find(l["text"], pat, phr)]

    res = [json.loads(x) for x in open(run) if x.startswith("{")]
    start = next(x["t"] for x in res if x.get("event") == "start")
    finals = [x for x in res if x.get("final")]
    vol = [x for x in res if "final" in x]
    apple = []  # (audio time, phrase, latency first seen, latency final)
    for f in finals:
        text = "".join(w["w"] for w in f["words"])
        # each run's characters mapped back to its time, so a match gets the word's time
        spans, pos = [], 0
        for w in f["words"]:
            spans.append((pos, pos + len(w["w"]), w["s"], w["e"]))
            pos += len(w["w"])
        for a, b, k in fillers.find(text, pat, phr):
            s, e = next(((s, e) for p, q, s, e in spans if p <= a < q), (None, None))
            if s is None:
                continue
            first = min((v["t"] for v in vol if any(w["s"] - 0.1 <= s <= w["e"] + 0.1 and
                        any(kk == k for _, _, kk in fillers.find(w["w"], pat, phr)) for w in v["words"])), default=f["t"])
            apple.append((s, k, first - start - e, f["t"] - start - e))

    # matched: an Apple filler of the same phrase inside a lab line's time (±1 s), each used once
    used, matched = set(), Counter()
    for s, e, k in lab:
        for i, (t, kk, *_) in enumerate(apple):
            if i not in used and kk == k and s - 1 <= t <= e + 1:
                used.add(i)
                matched[k] += 1
                break
    L, A = Counter(k for *_, k in lab), Counter(k for _, k, *_ in apple)
    print(f"{sid}: {len(lines)} lines, Apple ran {res[-1].get('t', 0) - start:.0f} s")
    print(f"{'phrase':12} {'lab':>5} {'apple':>6} {'both':>5}")
    for k in sorted(set(L) | set(A), key=lambda k: -L[k]):
        print(f"{k:12} {L[k]:5} {A[k]:6} {matched[k]:5}")
    lat = sorted(x[2] for x in apple)
    fin = sorted(x[3] for x in apple)
    if lat:
        q = lambda xs, p: round(xs[min(len(xs) - 1, int(p * len(xs)))], 2)
        print("first seen after said (s): median", q(lat, .5), "p75", q(lat, .75), "p90", q(lat, .9), "| final: median",
              q(fin, .5), "p90", q(fin, .9), f"| within 1.5 s: {sum(x <= 1.5 for x in lat)} of {len(lat)}",
              "| you know within 1.5 s:", sum(x[2] <= 1.5 for x in apple if x[1] == "you know"), "of",
              sum(x[1] == "you know" for x in apple))


main(*sys.argv[1:])
