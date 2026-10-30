"""Back-to-back conversations that may be one discussion (B35), and how long each step takes by length.

    python -m bench.joinpairs [DB]

Read-only (the database opened with mode=ro). Prints ids, gaps, lengths and how many people the two share, and the
lab's rule for one discussion in parts (octo/parts.py: `measure`, the guesses counted, and whether `one` groups them);
never names or words. The list is for the owner's verdicts: one or two?
"""

from __future__ import annotations

import json
import sqlite3
import sys

MAX_GAP_S = 20 * 60


def pairs(conn) -> list[dict]:
    from octo import parts as PT
    from octo import sessions as SV
    from octo.planning import pinned_review

    sess = PT._active(conn)
    out = []
    for a, b in zip(sess, sess[1:]):
        gap = b["at"] - a["end_at"]
        if gap > MAX_GAP_S:
            continue
        why = PT.kind(conn, a, b)
        cut = why or ("split" if a["recording_id"] == b["recording_id"] and gap < MAX_GAP_S else "far")
        people = [{sp["person_id"] for sp in SV.speakers(conn, s["id"]) if sp["person_id"] and not sp["self"]} for s in (a, b)]
        m = PT.measure(conn, a, b)
        out.append({"a": a["id"], "b": b["id"], "cut": cut, "gap_s": round(gap),
                    "a_min": round((a["end_s"] - a["start_s"]) / 60, 1), "b_min": round((b["end_s"] - b["start_s"]) / 60, 1),
                    "people": [len(p) for p in people], "shared": len(people[0] & people[1]),
                    "carry": m and [m["ab"], m["ba"]], "whole": m and m["whole"],
                    "enough_talk": m and m["talk"], "grouped": bool(why and m and PT.one(m, why)),
                    "answered": bool(a["reviewed_at"] or b["reviewed_at"] or pinned_review(conn, a["id"])
                                     or pinned_review(conn, b["id"]))})
    return out


def step_times(conn) -> list[dict]:
    """Per active conversation: its length and each listening step's and the review's run time (the current run)."""
    out = []
    for s in conn.execute("SELECT id, end_s - start_s AS len FROM sessions WHERE status='active' ORDER BY len DESC"):
        t = {r[0]: r[1] for r in conn.execute(
            "SELECT stage, finished_at - started_at FROM stage_runs WHERE target=? AND is_current=1 AND status='done' "
            "AND started_at IS NOT NULL", (s[0],))}
        ins = conn.execute("SELECT length(output) FROM stage_runs WHERE target=? AND stage='insight' AND is_current=1",
                           (s[0],)).fetchone()
        dz = conn.execute("SELECT output FROM stage_runs WHERE target=? AND stage='diarize' AND is_current=1", (s[0],)).fetchone()
        lines = len(json.loads(dz[0])["lines"]) if dz and dz[0] else None
        out.append({"id": s[0], "min": round(s[1] / 60, 1), "lines": lines,
                    **{k: round(t[k]) for k in ("transcribe", "speakers", "diarize", "identify", "insight") if t.get(k)},
                    "review_chars": ins[0] if ins else None})
    return out


def main(argv: list[str]) -> None:
    from octo import DB_PATH
    path = argv[0] if argv else str(DB_PATH)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    for p in pairs(conn):
        print(json.dumps(p))
    print("--- longest conversations: minutes, lines, seconds per step")
    for t in step_times(conn)[:12]:
        print(json.dumps(t))


if __name__ == "__main__":
    main(sys.argv[1:])
