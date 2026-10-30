"""The beta's results file (product plan 4.8): what a tester sends back, counts only.

What we'd like to hear (docs/beta.md; no pass mark since 19 Oct): did they install it alone, did their reviews
have something new to them, and are they still using it in week 4. The file answers the second and third from
the lab itself; the first is a question in docs/beta.md. Nothing in it names anyone or
quotes anything: numbers, dates (days only), and which kind of model the reviews ran on. The tester sees
it before sending it (`octo beta results`, or Progress → For the beta).
"""

from __future__ import annotations

import subprocess
import time
from datetime import date

from . import ROOT
from . import review as R
from .db import row, rows

VERSION = 1


def _commit() -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def results(conn, now: float | None = None) -> dict:
    now = now or time.time()
    c = R.checks(conn)
    times = [r["at"] for r in rows(conn, "SELECT r.started_at + s.start_s AS at FROM sessions s JOIN recordings r "
                                         "ON r.id = s.recording_id WHERE s.status='active' AND r.started_at IS NOT NULL")]
    first = min(times, default=None)
    weeks = sorted({date.fromtimestamp(t).isocalendar()[:2] for t in times})
    since_first = (now - first) / 86400 if first else 0
    from .settings import BY_STAGE, selected
    choice = selected(conn, BY_STAGE["insight"])
    reader = (choice.params.get("reader") or {}) if choice else {}
    coach = row(conn, "SELECT count(*) n FROM coach_notes WHERE status='done'")["n"]
    marks = {v: row(conn, "SELECT count(*) n FROM coach_feedback WHERE verdict=?", v)["n"] for v in ("right", "partly", "wrong")}
    sources = {r["source"]: r["n"] for r in rows(conn, "SELECT source, count(*) n FROM recordings GROUP BY source")}
    reviewed = c["checks"]["reviewed"]["value"]
    with_new = c["checks"]["new_share"]["value"]
    return {
        "octo_beta_results": VERSION,
        "made_on": date.fromtimestamp(now).isoformat(),
        "version": _commit(),
        "days_since_first_conversation": round(since_first),
        "weeks_with_a_conversation": len(weeks),
        "in_week_4_or_later": since_first >= 21 and bool(times) and (now - max(times)) / 86400 <= 7,
        "conversations": len(times),
        "reviewed": reviewed,
        "reviewed_with_something_new": with_new,
        "share_new": round(with_new / reviewed, 2) if reviewed else None,
        "nothing_new_answers": c["novelty"].get("nothing_new", 0),
        "steps_acted_on": c["checks"]["acted"]["value"],
        "items_marked_not_right": c["wrong"]["value"],
        "items_shown": c["wrong"]["of"],
        "coach_notes": coach,
        "coach_marks": marks,
        "recordings_by_source": {k: v for k, v in sources.items() if k in ("phone", "device", "file", "bench", "call")},
        "review_model": {"kind": reader.get("kind") or ("claude-code" if choice and choice.claude else "local" if choice else None),
                         "model": choice.params.get("model") if choice else None},
    }
