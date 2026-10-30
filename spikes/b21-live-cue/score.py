"""B21 spike: from live.swift's output, each filler's count in the final text and how long after it was said it
first showed (volatile or final). Usage: python score.py RUN.jsonl [TRUTH.txt]"""
import json, re, sys

FILLERS = ["um", "uh", "ah", "er", "hmm", "you know", "kind of", "sort of", "like", "basically"]


def norm(w):
    return re.sub(r"[^a-z' ]", "", w.lower()).strip()


def hits(words):
    """(filler, audio start, audio end) for each filler in a list of timed words."""
    ws = [(norm(w["w"]), w["s"], w["e"]) for w in words]
    ws = [w for w in ws if w[0]]
    out = []
    for i, (w, s, e) in enumerate(ws):
        for f in FILLERS:
            parts = f.split()
            if [x[0] for x in ws[i:i + len(parts)]] == parts:
                out.append((f, s, ws[i + len(parts) - 1][2]))
    return out


def seen(r, f, s):
    """Whether result r shows filler f said at s: a timed word there, or (volatile results come as one run over
    the whole stretch) the filler inside a run that covers s."""
    for w in r["words"]:
        if len(norm(w["w"]).split()) > 1 and w["s"] - 0.1 <= s <= w["e"] + 0.1 and count(w["w"])[f]:
            return True
    return any(g == f and abs(gs - s) < 0.4 for g, gs, _ in hits(r["words"]))


def count(text):
    t = " " + " ".join(norm(w) for w in text.split()) + " "
    return {f: len(re.findall(rf"(?<= ){f}(?= )", t)) for f in FILLERS}


def main(path, truth=None):
    lines = [json.loads(l) for l in open(path) if l.startswith("{")]
    start = next(l["t"] for l in lines if l.get("event") == "start")
    res = [l for l in lines if "final" in l]
    finals = [l for l in res if l["final"]]
    text = " ".join(l["text"] for l in finals) or (res[-1]["text"] if res else "")
    got = count(text)
    want = count(open(truth).read()) if truth else {}
    lat = []
    for l in finals or res[-1:]:
        for f, s, e in hits(l["words"]):
            first = min((r["t"] for r in res if seen(r, f, s)), default=None)
            if first is not None:
                lat.append((f, round(first - start - e, 2), round(l["t"] - start - e, 2)))
    print({f: f"{got[f]}/{want.get(f, '?')}" for f in FILLERS if got[f] or want.get(f)})
    if lat:
        firsts = sorted(x[1] for x in lat)
        print("latency first seen (s): median", firsts[len(firsts) // 2], "max", firsts[-1], "| as final: median",
              sorted(x[2] for x in lat)[len(lat) // 2])
        print(lat)


main(*sys.argv[1:])
