"""Runs one stage_run and exits:  python -m octo.worker <run_id>

Everything a model allocated goes back to the OS when this process ends.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback

from . import HOME
from .db import calling, connect, row, rows
from .stages import STAGES


def inputs_for(conn, run: dict) -> dict:
    stage = STAGES[run["stage"]]
    if stage.level == "recording":
        rec = row(conn, "SELECT * FROM recordings WHERE id=?", run["target"])
        return {"path": HOME / rec["path"], "duration_s": rec["duration_s"]}
    sess = row(conn, "SELECT * FROM sessions WHERE id=?", run["target"])
    rec = row(conn, "SELECT * FROM recordings WHERE id=?", sess["recording_id"])
    upstream_ids = json.loads(run["upstream"])
    inputs = {"path": HOME / rec["path"], "start_s": sess["start_s"], "end_s": sess["end_s"],
              "upstream_ids": upstream_ids,
              "upstream": [json.loads(r["output"]) for u in upstream_ids
                           if (r := row(conn, "SELECT output FROM stage_runs WHERE id=?", u))]}
    parts = json.loads(run["params"] or "{}").get("parts") if run["stage"] == "insight" else None
    if run["stage"] in ("numbers", "insight", "commitments") and upstream_ids:
        from .corrections import apply, for_run, left_out, without
        # B35: one review of a discussion in parts reads each part as its own page does, then all of them as one.
        for i, (sid, dz) in enumerate(zip([p["id"] for p in parts] if parts else [run["target"]], upstream_ids)):
            fixes = for_run(conn, sid, dz)
            raw = inputs["upstream"][i]
            inputs["upstream"][i] = apply(raw, fixes)
            if run["stage"] in ("insight", "commitments"):  # speakers the owner left out of insights: neither their words
                # nor their numbers (the review's talk-share table, with their name) reach the model
                inputs["upstream"][i] = without(inputs["upstream"][i], left_out(raw, fixes))
        if parts:
            from .parts import stitch
            inputs["upstream"] = [stitch(inputs["upstream"], parts)]
    if run["stage"] in ("insight", "commitments") and rec["started_at"]:  # so the review can date its next steps; not an input
        inputs["started_at"] = rec["started_at"] + sess["start_s"]  # that re-runs anything (the owner's ratings stay put)
    if run["stage"] == "insight":  # what earlier runs of this conversation worked out (octo/insight.py, memo)
        from .owner import load
        # Who a review through Claude Code is about (octo/review_any.py). Like started_at, not an input: the
        # first lab's reviews don't re-run when its profile is edited (one on the owner's own connection carries
        # the profile in its params, and does), nor did they when it moved off claude_review.py (19 Oct).
        inputs["owner"] = load(conn)
        inputs["memo"] = {}
        for r in rows(conn, "SELECT output FROM stage_runs WHERE target=? AND stage='insight' AND status='done' "
                            "ORDER BY finished_at", run["target"]):
            inputs["memo"].update((json.loads(r["output"]) or {}).get("memo") or {})
    if run["stage"] == "commitments":  # B14: earlier answers for the same conversation, reused while nothing changed
        inputs["memo"] = {}
        for r in rows(conn, "SELECT output FROM stage_runs WHERE target=? AND stage='commitments' AND status='done' "
                            "ORDER BY finished_at", run["target"]):
            inputs["memo"].update((json.loads(r["output"]) or {}).get("memo") or {})
    if run["stage"] == "identify":
        from .voice import all_voiceprints
        inputs["voiceprints"] = all_voiceprints(conn)
    return inputs


def main(run_id: str) -> int:
    conn = connect()
    run = row(conn, "SELECT * FROM stage_runs WHERE id=?", run_id)
    if not run:
        print(f"no such run {run_id}", file=sys.stderr)
        return 2
    conn.execute("UPDATE stage_runs SET status='running', started_at=? WHERE id=?", (time.time(), run_id))
    try:
        if run["stage"] == "transcribe":  # MLX capped in this process (review 5 Nov, risk 6)
            from .asr import cap_mlx
            cap_mlx()
        inputs = inputs_for(conn, run)
        inputs["progress"] = lambda msg: conn.execute("UPDATE stage_runs SET progress=? WHERE id=?", (msg, run_id))
        with calling(conn, run["stage"], run_id):  # a model call it makes is counted in model_calls
            out = STAGES[run["stage"]].fn(inputs, json.loads(run["params"]))
    except Exception as e:
        traceback.print_exc()
        conn.execute("UPDATE stage_runs SET status='failed', error=?, finished_at=? WHERE id=?",
                     (f"{type(e).__name__}: {e}", time.time(), run_id))
        return 1
    conn.execute("UPDATE stage_runs SET status='done', output=?, finished_at=? WHERE id=?",
                 (json.dumps(out), time.time(), run_id))
    return 0


if __name__ == "__main__":
    code = main(sys.argv[1])
    # The result is committed (autocommit). Skip the interpreter's teardown: native libraries'
    # exit-time destructors (onnxruntime's telemetry thread, torch's dispatcher) crashed two
    # workers after their work was saved (2 Oct), and the runner then threw the result away.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
