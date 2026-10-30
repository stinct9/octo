"""Superseded stage runs lose their output (architecture review 4, 28 Oct).

Every redo, re-transcription or model switch adds a stage run, and the one before stays: before this, the only
delete was a deleted conversation's. Transcripts are most of the database, and each daily backup copies them.

A run's output is cleared (its row kept: id, stage, params, upstream and times, for the ETA's fit and anything
that names it) only when all of these hold:
- it's a listening step or numbers (`STAGES`): reviews and commitments keep theirs, since a new run reuses what earlier
  ones worked out (`worker.py`'s memo) and Progress reads them;
- it's done, not current, finished more than GRACE_DAYS ago, and a newer current run of the same step has replaced it;
- nothing names it: not the current runs nor the queued, running or failed ones (nor what they were built on, all
  the way up), and not any other table (feedback, review_answers, ticktick_tasks, focus, commitments, coach notes,
  session_speakers' and corrections' diarize_run, sessions.redo_review, …: every text column is searched, so a
  new table that names a run is covered too), nor anything those name in turn.
Nothing re-runs: the planner reads only current runs' outputs. The model calls a cleared run made stay counted: the
day's count reads the last two days only, and a run is cleared after GRACE_DAYS.
"""

from __future__ import annotations

import json
import re
import time

from .db import load_json, rows

GRACE_DAYS = 14
STAGES = ("find_conversations", "transcribe", "speakers", "diarize", "identify", "numbers")
PRUNED = '{"pruned": true}'
MERGED = '{"pruned": true, "merged": true}'  # the ETA fits a Hindi merge apart (eta.rates)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9_]+", text))


def _named(conn) -> set[str]:
    """Every word in every text value in the database except stage runs', to look run ids up in."""
    parts = []
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        cols = [c["name"] for c in rows(conn, f"PRAGMA table_info({t})")
                if "CHAR" in (c["type"] or "").upper() or "TEXT" in (c["type"] or "").upper() or not c["type"]]
        if t == "stage_runs":
            continue  # its outputs are what's cleared; its upstream is followed below, from the runs kept
        for c in cols:
            parts += [v for (v,) in conn.execute(f'SELECT "{c}" FROM "{t}" WHERE typeof("{c}")=\'text\'')]
    return _tokens("\n".join(parts))


def candidates(conn, now: float | None = None) -> list[str]:
    """The runs whose output would be cleared now."""
    cutoff = (now if now is not None else time.time()) - GRACE_DAYS * 86400
    q = ",".join("?" * len(STAGES))
    old = rows(conn, f"SELECT id FROM stage_runs sr WHERE status='done' AND is_current=0 AND stage IN ({q}) "
                     f"AND finished_at < ? AND output IS NOT NULL AND output NOT LIKE ? AND EXISTS (SELECT 1 FROM stage_runs c "
                     f"WHERE c.target=sr.target AND c.stage=sr.stage AND c.is_current=1 AND c.queued_at > sr.queued_at)",
               *STAGES, cutoff, PRUNED[:-1] + "%")
    if not old:
        return []
    named = _named(conn)
    runs = {r["id"]: r for r in rows(conn, "SELECT id, status, is_current, upstream, params FROM stage_runs")}
    todo = [i for i, r in runs.items() if r["is_current"] or r["status"] != "done" or i in named]
    keep = set(todo)
    while todo:
        while todo:  # what the kept runs were built on, all the way up
            for u in json.loads(runs[todo.pop()]["upstream"] or "[]"):
                if u in runs and u not in keep:
                    keep.add(u)
                    todo.append(u)
        params = _tokens("\n".join(runs[i]["params"] for i in keep))  # a kept run's params can name another (a diarize run)
        todo = [r["id"] for r in old if r["id"] not in keep and r["id"] in params]
        keep.update(todo)
    return [r["id"] for r in old if r["id"] not in keep]


def prune(conn, now: float | None = None) -> int:
    """Clear the output of every candidate; returns how many."""
    ids = candidates(conn, now)
    for i in ids:
        out = load_json(rows(conn, "SELECT output FROM stage_runs WHERE id=?", i)[0]["output"])
        conn.execute("UPDATE stage_runs SET output=? WHERE id=? AND is_current=0",
                     (MERGED if isinstance(out, dict) and out.get("merged") else PRUNED, i))
    return len(ids)
