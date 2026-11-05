"""When will it be ready? (B3, 13 Oct, docs/backlog.md)

13 Oct: *"for the stuff that's queued - lets put an ETA on when it'll be ready"*. Read time only:
nothing here changes what runs or in what order.

How long a step takes is fitted from this lab's own finished runs, as fixed seconds plus seconds per
minute of audio, per stage and model (a buyer's Mac isn't the first lab's). On the first lab, 3–13 Oct: transcribe
20 s + 8.1 s/min, speakers 20 s + 4.9 s/min, identify 20 s + 0.4 s/min, diarize 4 s + 0.8 s/min, the
review 26 s + 2.3 s/min, a Hindi merge 56 s/min (learnings, 13 Oct). Until a stage has MIN_RUNS of its
own, DEFAULT is used.

Then the queue is walked as the runner would (`pipeline.next_run`'s order, one step at a time), with the
steps that aren't queued yet: the planner queues a step only once the one before it is done, so a
conversation being transcribed still has speakers, diarize, identify, numbers and maybe its review to
come. A conversation's time is when its last step that doesn't need the owner is done: through the review when
its speakers are settled, else through numbers ("ready for you" to confirm). Claude steps wait for the
usage limit's reset, as the runner does.
"""

from __future__ import annotations

import time

from . import frame as FR
from .db import load_json, row, rows

MIN_RUNS = 5
RECENT_RUNS = 40  # per stage: the newest, so a faster model or Mac shows within a few days
# (fixed s, s per minute of audio): the first lab, 3–13 Oct (above), for a lab with too few runs of its own.
DEFAULT: dict[str, tuple[float, float]] = {
    "find_conversations": (20.0, 0.5), "transcribe": (20.0, 8.1), "transcribe+merge": (0.0, 56.0),
    "speakers": (20.0, 4.9), "diarize": (4.0, 0.8), "identify": (20.0, 0.4), "numbers": (0.5, 0.0),
    "insight": (26.0, 2.3), "commitments": (20.0, 1.0),  # commitments (B14): a guess, until the lab has its own runs
}
LATE_FACTOR, LATE_SLACK_S = 1.5, 60.0  # a running step past this is "taking longer than usual"
_TTL_S = 5.0
_cache: tuple[float, dict] | None = None


def _fit(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    """Least squares seconds = a + b * minutes; None when there's too little to go on. A negative
    intercept with a rising slope is fitted through zero (a Hindi merge: Claude's time is all per
    minute); a falling slope (noise on a stage that doesn't scale) falls back to the mean, flat."""
    if len(points) < MIN_RUNS:
        return None
    n = len(points)
    mx = sum(m for m, _ in points) / n
    my = sum(s for _, s in points) / n
    sxx = sum((m - mx) ** 2 for m, _ in points)
    if sxx <= 1e-9:
        return (my, 0.0)
    b = sum((m - mx) * (s - my) for m, s in points) / sxx
    a = my - b * mx
    if b < 0:
        return (my, 0.0)
    if a < 0:
        return (0.0, sum(m * s for m, s in points) / max(sum(m * m for m, _ in points), 1e-9))
    return (a, b)


def _model(params: str | dict | None) -> str:
    p = load_json(params) if isinstance(params, str) else (params or {})
    return str((p or {}).get("model") or "")


def rates(conn) -> dict[str, tuple[float, float]]:
    """Per stage (and per model where it has one: "insight:claude-opus-5-5"), (fixed s, s per minute)."""
    out = dict(DEFAULT)
    found = rows(conn, "SELECT sr.id, sr.stage, sr.params, sr.finished_at - sr.started_at AS took, "
                       "coalesce((s.end_s - s.start_s), r.duration_s) / 60.0 AS minutes FROM stage_runs sr "
                       "LEFT JOIN sessions s ON s.id = sr.target LEFT JOIN recordings r ON r.id = sr.target "
                       "WHERE sr.status='done' AND sr.started_at > 0 AND sr.finished_at >= sr.started_at "
                       "ORDER BY sr.finished_at DESC")
    by: dict[str, list[tuple[float, float]]] = {}
    for f in found:
        if not f["minutes"] or f["minutes"] <= 0:
            continue
        key = f["stage"]
        if key == "transcribe" and _merged(conn, f["id"]):
            key = "transcribe+merge"
        model = _model(f["params"])
        for k in [key, f"{key}:{model}"] if model else [key]:
            pts = by.setdefault(k, [])
            if len(pts) < RECENT_RUNS:
                pts.append((f["minutes"], f["took"]))
    lab, ids = FR.lab(conn), {f["id"] for f in found}
    for k in [k for k in _MERGED if k[0] == lab and k[1] not in ids]:  # deleted, or pruned: not kept for ever (review 5 Nov)
        del _MERGED[k]
    for k, pts in by.items():
        fit = _fit(pts)
        if fit:
            out[k] = fit
    return out


def _merged(conn, run_id: str) -> bool:
    """A transcript merged by Claude (octo/hindi.py). Asked of a done run, whose output is written once: sorting every
    run with its output spilled ~20 MB of transcripts to a temporary file on each forecast (4 Nov)."""
    key = (FR.lab(conn), run_id)
    if key not in _MERGED:
        m = row(conn, "SELECT json_extract(output, '$.merged') m FROM stage_runs WHERE id=?", run_id)["m"]
        _MERGED[key] = bool(load_json(m) if isinstance(m, str) and m[:1] in "{[" else m)  # an object comes back as its text
    return _MERGED[key]


_MERGED: dict[tuple[str, str], bool] = {}


def seconds(rate: dict[str, tuple[float, float]], stage: str, minutes: float, model: str = "", merge: bool = False) -> float:
    key = "transcribe+merge" if merge and stage == "transcribe" else stage
    a, b = rate.get(f"{key}:{model}") or rate.get(key) or (30.0, 1.0)
    return a + b * max(minutes, 0.0)


def forecast(conn, now: float | None = None) -> dict:
    """{"sessions": {id: {"at", "late", "then"}}, "queue_empty_at", "steps"}. "then": "review" when the
    review is among its steps, "you" when it stops for the owner (speakers to confirm). Empty when nothing waits."""
    from . import runs as RN
    from . import planning as PL
    from .stages import STAGES, downstream

    now = time.time() if now is None else now
    order = {name: i for i, name in enumerate(STAGES)}
    rate = rates(conn)
    pending = rows(conn, "SELECT * FROM stage_runs WHERE status IN ('queued', 'running')")
    if not pending:
        return {"sessions": {}, "queue_empty_at": None, "steps": 0}
    info: dict[str, dict] = {}  # target → minutes, when it was recorded, is it a session, merge?

    def about(target: str) -> dict:
        if target not in info:
            s = row(conn, "SELECT s.start_s, s.end_s, r.started_at, r.created_at FROM sessions s JOIN recordings r "
                          "ON r.id = s.recording_id WHERE s.id=?", target)
            if s:
                tr = RN.current(conn, target, "transcribe")
                lang = (load_json(tr["output"]) or {}).get("language") if tr and tr["output"] else None
                info[target] = {"minutes": (s["end_s"] - s["start_s"]) / 60, "session": True,
                                "when": (s["started_at"] or s["created_at"]) + s["start_s"], "merge": lang not in (None, "en")}
            else:
                r = row(conn, "SELECT duration_s, started_at, created_at FROM recordings WHERE id=?", target)
                info[target] = {"minutes": ((r or {}).get("duration_s") or 0) / 60, "session": False,
                                "when": (r or {}).get("started_at") or (r or {}).get("created_at") or 0, "merge": False}
        return info[target]

    tasks: dict[tuple[str, str], dict] = {}
    for run in pending:
        model = _model(run["params"])
        t = about(run["target"])
        est = seconds(rate, run["stage"], t["minutes"], model, t["merge"])
        task = {"stage": run["stage"], "target": run["target"], "claude": RN.uses_claude(run), "queued_at": run["queued_at"] or 0}
        if run["status"] == "running":
            took = now - (run["started_at"] or now)
            task.update(running=True, left=max(est - took, 0.0), late=took > est * LATE_FACTOR + LATE_SLACK_S)
        else:
            task.update(running=False, left=est, late=False)
        tasks[(run["target"], run["stage"])] = task
    # The steps that will be queued after these, for the same conversation.
    for (target, stage) in list(tasks):
        if STAGES[stage].level != "session":
            continue
        t = about(target)
        # The review builds on diarize but waits on identify in practice (it needs speakers settled, which
        # identify decides), so a conversation with anything left before it has its review to come too.
        for later in [*downstream(stage), "insight"]:
            if (target, later) in tasks:
                continue
            if later == "insight" and not PL.insight_ready(conn, target):
                continue  # waits for the owner to confirm who's who: the time stops before it
            if later == "commitments" and not PL.commitments_ready(conn, target):
                continue  # B14: after the review, and only in its history
            st = STAGES[later]
            model = str(st.params.get("model") or "")
            tasks[(target, later)] = {"stage": later, "target": target, "running": False, "late": False,
                                      "claude": later == "insight" and model.startswith("claude"),
                                      "queued_at": float("inf"), "left": seconds(rate, later, t["minutes"], model)}

    def deps(key: tuple[str, str]) -> list[tuple[str, str]]:
        ups = [*STAGES[key[1]].upstreams, *(("identify", "numbers") if key[1] == "insight" else ())]
        return [(key[0], u) for u in ups if (key[0], u) in tasks]

    def rank(key: tuple[str, str]) -> tuple:
        t = tasks[key]
        first = 0 if t["stage"] == "find_conversations" else 1 if (t["stage"] == "insight" and t["claude"]) else 2
        return (first, order[t["stage"]], -about(key[0])["when"], t["queued_at"])

    blocked = RN.claude_blocked_until(conn, now) or 0.0
    clock, finished, waits = now, {}, {}
    for key in [k for k, t in tasks.items() if t["running"]]:  # one runner: the running step ends first
        clock += tasks[key]["left"]
        finished[key] = clock
    left = {k for k in tasks if k not in finished}
    while left:
        ready = [k for k in left if all(d in finished for d in deps(k))]
        if not ready:
            break  # a cycle can't happen; but never loop forever on surprises
        can = [k for k in ready if not (tasks[k]["claude"] and clock < blocked)]
        if not can:
            for k in ready:
                waits[k[0]] = blocked
            clock = blocked
            continue
        key = min(can, key=rank)
        clock += tasks[key]["left"]
        finished[key] = clock
        left.discard(key)

    out: dict[str, dict] = {}
    for (target, stage), at in finished.items():
        if not about(target)["session"] or stage == "commitments":  # B14: not what the owner waits for
            continue
        o = out.setdefault(target, {"at": at, "late": False, "then": "you", "claude_wait": None})
        o["at"] = max(o["at"], at)
        o["late"] = o["late"] or tasks[(target, stage)]["late"]
        if stage == "insight":
            o["then"] = "review"
        if target in waits:
            o["claude_wait"] = waits[target]
    return {"sessions": out, "queue_empty_at": max(finished.values()) if finished else None, "steps": len(finished)}


def cached(conn) -> dict:
    """The forecast, worked out at most every few seconds: the list asks for it once per row."""
    global _cache
    if _cache and time.time() - _cache[0] < _TTL_S:
        return _cache[1]
    _cache = (time.time(), forecast(conn))
    return _cache[1]


def expected(conn, run: dict) -> float | None:
    """What a run was expected to take, for the log line beside what it took (B3: how good the ETA is)."""
    try:
        t = row(conn, "SELECT end_s - start_s AS s FROM sessions WHERE id=?", run["target"]) or \
            row(conn, "SELECT duration_s AS s FROM recordings WHERE id=?", run["target"])
        merge = run["stage"] == "transcribe" and bool((load_json(run.get("output")) or {}).get("merged"))
        return seconds(rates(conn), run["stage"], ((t or {}).get("s") or 0) / 60, _model(run["params"]), merge)
    except Exception:  # a log line is never worth a crash
        return None
