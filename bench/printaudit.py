"""Voiceprints that may not be their person's voice (people plan, 4.1).

    python -m bench.printaudit

A label on a speaker that was really two people (8 Oct: "Bram" was two colleagues pyannote heard as
one) stores a print of the blend, which then teaches the wrong voice. For each print: how close it is
to its own person's other prints, and to the nearest other person. Read-only; prints names and numbers,
never audio or words.
"""

from __future__ import annotations

import numpy as np

from octo import DB_PATH as DB
from octo.db import connect, rows
from octo.voice import cosine

FAR_FROM_OWN = 0.30   # below this to its own person's other prints: not their voice (9 Oct: short prints of one person ran 0.36-0.62; the mixed one 0.01)
CLOSER_MARGIN = 0.0   # nearer another person than its own by more than this


def audit(prints: list[dict]) -> list[dict]:
    """One row per print: `own` = mean cosine to its person's other prints (None if it's their only
    one), `other` = the best mean cosine to any other person's prints, and `flag`."""
    by: dict[str, list[dict]] = {}
    for p in prints:
        by.setdefault(p["person_id"], []).append(p)
    out = []
    for p in prints:
        mine = [q for q in by[p["person_id"]] if q["id"] != p["id"]]
        own = float(np.mean([cosine(p["embedding"], q["embedding"]) for q in mine])) if mine else None
        others = sorted(((float(np.mean([cosine(p["embedding"], q["embedding"]) for q in qs])), qs[0]["name"])
                         for pid, qs in by.items() if pid != p["person_id"]), reverse=True)
        other, nearest = others[0] if others else (None, None)
        flag = None
        if own is not None and own < FAR_FROM_OWN:
            flag = "far from own"
        if own is not None and other is not None and other > own + CLOSER_MARGIN:
            flag = f"closer to {nearest}"
        out.append({"id": p["id"], "name": p["name"], "source": p["source"], "seconds": p["seconds"],
                    "own": own, "other": other, "nearest": nearest, "flag": flag})
    return out


def load(conn) -> list[dict]:
    return [{**r, "embedding": np.frombuffer(r["embedding"], dtype=np.float32)} for r in rows(
        conn, "SELECT v.id, v.person_id, p.name, v.source, v.seconds, v.embedding, v.model FROM voiceprints v "
              "JOIN people p ON p.id = v.person_id ORDER BY p.name, v.created_at")]


def main() -> None:
    conn = connect(DB)
    prints = load(conn)
    models = {p["model"] for p in prints}
    for model in sorted(models, key=str):
        group = [p for p in prints if p["model"] == model]
        print(f"model {model}: {len(group)} prints, {len({p['person_id'] for p in group})} people\n")
        print(f"{'person':14} {'source':44} {'s':>4} {'own':>5} {'other':>5}  nearest")
        for r in audit(group):
            f = lambda x: f"{x:5.2f}" if x is not None else "    -"
            print(f"{r['name'][:14]:14} {r['source'][:44]:44} {r['seconds']:4.0f} {f(r['own'])} {f(r['other'])}  "
                  f"{r['nearest'] or ''}{'   <-- ' + r['flag'] if r['flag'] else ''}")
        print()


if __name__ == "__main__":
    main()
