"""Which reader writes the review the owner finds most useful? Blind, on their own conversations.

    python -m bench.reviewcompare pick                  # conversations to use: reviewed, English, varied lengths
    python -m bench.reviewcompare run SESSION [...]     # every reader on each, written as A/B/C in random order
    python -m bench.reviewcompare rank SESSION B A C    # The owner's order, best first, before the reveal
    python -m bench.reviewcompare reveal                # who each letter was, their ranks, time and tokens

The review code is the lab's own (octo/review_any.py through Claude Code: model and effort are its inputs),
fed exactly what the review step feeds it (the worker's corrections and left-out speakers, the names they
confirmed, their principles). Only the reader differs. A reader whose review of this conversation is
already stored with the same inputs is reused, not asked again.

The blind pages go to notes/ (local, never committed: they hold transcript quotes); the key, ranks and
usage to data/bench/reviewcompare/ (swept at 60 days). Calls go to Claude through Claude Code on the owner's
plan, like the reviews, and aren't counted in the lab's 60 a day.
"""

from __future__ import annotations

import json
import secrets
import sys
import time

from octo import DATA, NOTES
from octo.db import connect, load_json, row, rows

OUT = DATA / "bench" / "reviewcompare"
READERS = {  # 13 Oct: today's, a lower effort, and Sonnet at high
    "opus-high": {"model": "claude-opus-5-5", "effort": "high"},
    "opus-medium": {"model": "claude-opus-5-5", "effort": "medium"},
    "sonnet-high": {"model": "claude-sonnet-5-5", "effort": "high"},
}


def pick(conn) -> None:
    got = rows(conn, "SELECT s.id, s.start_s, s.end_s, s.reviewed_at, r.started_at FROM sessions s "
                     "JOIN recordings r ON r.id = s.recording_id WHERE s.status='active' AND s.reviewed_at IS NOT NULL "
                     "ORDER BY s.end_s - s.start_s")
    print("reviewed conversations with a Claude review in English, shortest first:")
    for s in got:
        ins = row(conn, "SELECT output FROM stage_runs WHERE target=? AND stage='insight' AND is_current=1 AND status='done'", s["id"])
        tr = row(conn, "SELECT output FROM stage_runs WHERE target=? AND stage='transcribe' AND is_current=1", s["id"])
        out, lang = load_json(ins["output"]) if ins else {}, (load_json(tr["output"]) or {}).get("language") if tr else None
        if out.get("kind") != "review" or out.get("skipped") or lang != "en":
            continue
        when = time.strftime("%d %b %H:%M", time.localtime((s["started_at"] or 0) + s["start_s"]))
        print(f"  {s['id']}  {when}  {(s['end_s'] - s['start_s']) / 60:5.1f} min")


def inputs(conn, sid: str) -> tuple[dict, dict, set, dict]:
    """What the review step would give the reader now (octo/worker.py, stages.insight)."""
    from octo.corrections import apply, for_run, left_out, without
    from octo.owner import load
    from octo.planning import settled_speakers

    speakers = settled_speakers(conn, sid)
    if speakers is None:
        raise SystemExit(f"{sid}: speakers aren't settled")
    dz = row(conn, "SELECT id, output FROM stage_runs WHERE target=? AND stage='diarize' AND is_current=1", sid)
    raw, fixes = load_json(dz["output"]), for_run(conn, sid, dz["id"])
    diarize = without(apply(raw, fixes), left_out(raw, fixes))
    sess = row(conn, "SELECT s.start_s, r.started_at FROM sessions s JOIN recordings r ON r.id = s.recording_id WHERE s.id=?", sid)
    ins = row(conn, "SELECT params FROM stage_runs WHERE target=? AND stage='insight' AND is_current=1", sid)
    params = {k: v for k, v in load_json(ins["params"]).items() if k in ("min_jay_speech_s", "max_ctx")}
    params["started_at"] = sess["started_at"] + sess["start_s"] if sess["started_at"] else None
    params["owner"] = load(conn)  # as the worker gives the review
    names = {s["speaker"]: s["name"] for s in speakers}
    return diarize, names, {s["speaker"] for s in speakers if s["self"]}, params


def run(conn, sessions: list[str]) -> None:
    from octo import insight as I
    from octo.cli import render_insights
    from octo.rubric import text as rubric_text

    OUT.mkdir(parents=True, exist_ok=True)
    NOTES.mkdir(exist_ok=True)
    for sid in sessions:
        diarize, names, me, params = inputs(conn, sid)
        memo: dict = {}
        for r in rows(conn, "SELECT output FROM stage_runs WHERE target=? AND stage='insight' AND status='done'", sid):
            memo.update((load_json(r["output"]) or {}).get("memo") or {})
        order = list(READERS)
        for i in range(len(order) - 1, 0, -1):  # a fair shuffle from the OS's randomness
            j = secrets.randbelow(i + 1)
            order[i], order[j] = order[j], order[i]
        key, doc = {}, [f"# Which review is most useful? {sid}", "",
                        "Read all three. Rank them best first, then: "
                        f"`python -m bench.reviewcompare rank {sid} X Y Z` (or tell Claude).", ""]
        for letter, reader in zip("ABC", order):
            print(f"{sid} {letter}…", flush=True)
            out = I.run(diarize, names, me, {**params, **READERS[reader]}, rubric_text=rubric_text(), memo=memo)
            key[letter] = {"reader": reader, "seconds": out["seconds"], "reused": out["reused"], "usage": out.get("usage")}
            doc += ["", "---", "", f"## Review {letter}", ""] + render_insights(out)
        (NOTES / f"reviewcompare_{sid}.md").write_text("\n".join(doc) + "\n")
        (OUT / f"{sid}.json").write_text(json.dumps({"session": sid, "key": key, "at": time.time(), "rank": None}))
        print(f"wrote notes/reviewcompare_{sid}.md")


def rank(sid: str, order: list[str]) -> None:
    f = OUT / f"{sid}.json"
    data = json.loads(f.read_text())
    if sorted(order) != sorted(data["key"]):
        raise SystemExit(f"give all of {', '.join(sorted(data['key']))}, best first")
    data["rank"] = order
    f.write_text(json.dumps(data))
    print(f"{sid}: ranked {' > '.join(order)}")


def reveal() -> None:
    files = sorted(OUT.glob("*.json"))
    places: dict[str, list[int]] = {r: [] for r in READERS}
    for f in files:
        d = json.loads(f.read_text())
        line = []
        for letter, k in sorted(d["key"].items()):
            u = k.get("usage") or {}
            tokens = (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
            line.append(f"{letter}={k['reader']} ({k['seconds']:.0f} s{', reused' if k['reused'] else ''}"
                        f"{f', {tokens:,} tokens' if tokens else ''})")
            if d["rank"]:
                places[k["reader"]].append(d["rank"].index(letter) + 1)
        print(f"{d['session']}: {'  '.join(line)}  ranked: {' > '.join(d['rank']) if d['rank'] else 'not yet'}")
    print()
    for reader, ps in places.items():
        if ps:
            print(f"{reader:12} mean place {sum(ps) / len(ps):.2f} over {len(ps)}  (firsts: {ps.count(1)})")


if __name__ == "__main__":
    cmd, conn = (sys.argv[1] if len(sys.argv) > 1 else "pick"), connect()
    if cmd == "pick":
        pick(conn)
    elif cmd == "run":
        run(conn, sys.argv[2:])
    elif cmd == "rank":
        rank(sys.argv[2], sys.argv[3:])
    elif cmd == "reveal":
        reveal()
