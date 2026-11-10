"""The lab's split rules (octo/split.py) replayed at every split already made, by the owner or the lab.

    python -m bench.splitreplay [DB]

Read-only (mode=ro). For each split whose two parts are both active: both parts' current diarization put back
together, people keyed by who they were named as (unnamed speakers stay apart per part), the owner left out. Prints
the pause, the others' silence, the shared talk at the cut, whether `suggest` offers it, whether it's `strong`, and
whether `auto` would cut. Ids and numbers only, never names or words. Pipeline review 11 Nov 2026.
"""

from __future__ import annotations

import json
import sqlite3
import sys

ME = "owner"


def _side(conn, sid: str, off: float):
    from octo import sessions as SV
    _, out, _ = SV.diarized(conn, sid)
    if not out:
        return None
    sp = {s["letter"]: s for s in SV.speakers(conn, sid)}

    def key(letter: str) -> str:
        s = sp.get(letter) or {}
        return ME if s.get("self") else (s.get("person_id") or f"{sid}:{letter}")
    return ([{"speaker": key(t["speaker"]), "start": t["start"] + off, "end": t["end"] + off} for t in out["turns"]],
            [{**ln, "start": ln["start"] + off} for ln in out["lines"]])


def _around(turns: list[dict], at: float) -> float:
    after = min((t["start"] for t in turns if t["start"] >= at), default=at)
    before = max((t["end"] for t in turns if t["end"] <= at), default=at)
    return after - before


def replay(conn) -> list[dict]:
    from octo import split as SP
    out = []
    for sp in conn.execute("SELECT * FROM splits ORDER BY created_at"):
        a = conn.execute("SELECT * FROM sessions WHERE recording_id=? AND abs(end_s-?)<0.01 AND status='active'",
                         (sp["recording_id"], sp["at_s"])).fetchone()
        b = conn.execute("SELECT * FROM sessions WHERE recording_id=? AND abs(start_s-?)<0.01 AND status='active'",
                         (sp["recording_id"], sp["at_s"])).fetchone()
        r = {"by": sp["by"], "undone": sp["undone_at"] is not None, "a": a and a["id"], "b": b and b["id"]}
        sides = a and b and (_side(conn, a["id"], 0.0), _side(conn, b["id"], b["start_s"] - a["start_s"]))
        if not sides or not all(sides):
            out.append(r)
            continue
        turns, lines = sides[0][0] + sides[1][0], sides[0][1] + sides[1][1]
        cut = sp["at_s"] - a["start_s"]
        ign = {ME}
        before, after = SP._talk(turns, cut - SP.WINDOW_S, cut, ign), SP._talk(turns, cut, cut + SP.WINDOW_S, ign)
        whole_b, whole_a = SP._talk(turns, 0, cut, ign), SP._talk(turns, cut, float("inf"), ign)
        shared = max(SP._carry(x, y) if y else 1.0 for x, y in ((before, after), (after, before), (whole_b, whole_a), (whole_a, whole_b)))
        near = [c for c in SP._candidates(turns, ign) if abs(c["at"] - cut) <= 30]
        c = near[0] if near else None
        r.update({"pause_s": round(_around(turns, cut), 1), "others_silent_s": round(_around([t for t in turns if t["speaker"] != ME], cut), 1),
                  "shared": round(shared, 2), "goodbye_hello": SP.ended(lines, cut),
                  "owner_share": [round(SP._own_share(turns, cut - SP.WINDOW_S, cut, ign), 2),
                                  round(SP._own_share(turns, cut, cut + SP.WINDOW_S, ign), 2)],
                  "suggested": bool(c), "strong": bool(c and c["strong"]),
                  "auto_would": bool(c and c["overlap"] <= SP.AUTO_MAX_OVERLAP
                                     and (c["gap_s"] >= SP.AUTO_MIN_GAP_S or SP.ended(lines, c["at"])))})
        out.append(r)
    return out


def main(argv: list[str]) -> None:
    from octo import DB_PATH
    conn = sqlite3.connect(f"file:{argv[0] if argv else DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    for r in replay(conn):
        print(json.dumps(r))


if __name__ == "__main__":
    main(sys.argv[1:])
