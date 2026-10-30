"""The list and what reads across conversations: the list and its token, search, others' questions, set-aside ones,
uploads and what's still coming in, the processor's status, home's open steps, Progress."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .. import aside as AS
from .. import audio as A
from .. import review as R
from .. import parts as PT
from .. import sessions as SV
from .. import settings as ST
from ..db import row, rows
from ..pipeline import DuplicateRecording, add_recording, current
from ..api import code_changed, _receive
from .common import AUDIO_TYPES, Lab, SOURCES, same_origin, upload_origin


class QuestionCheck(BaseModel):
    session_id: str
    start: float
    verdict: Literal["answered", "unanswered", "not_question"] | None = None


def add(router: APIRouter, lab: Lab) -> None:
    db, processor, watchdog = lab.db, lab.processor, lab.watchdog

    @router.get("/sessions")
    def sessions(response: Response, days: int | None = None) -> list[dict]:
        """Newest first. With `days`, only that many days back (by when it was recorded, or added);
        the X-Older header says how many more there are (review 4 Oct, M35: each row works out its
        state, which grows with every recording)."""
        conn = db()
        later = {sid for sid, head in PT.heads(conn).items() if sid != head}  # B35: one discussion in parts, once
        found = [s for s in rows(conn, "SELECT s.*, r.source, r.original, r.started_at, r.created_at AS added FROM sessions s "
                                       "JOIN recordings r ON r.id = s.recording_id WHERE s.status='active'") if s["id"] not in later]
        when = lambda s: (s["started_at"] + s["start_s"]) if s["started_at"] else s["added"]
        found.sort(key=when, reverse=True)
        if days is not None:
            cut = time.time() - days * 86400
            older = [s for s in found if when(s) < cut]
            found = [s for s in found if when(s) >= cut]
            response.headers["X-Older"] = str(len(older))
        response.headers["X-Aside"] = str(AS.count(conn))  # set aside as not conversations (B5): folded at the end
        return [SV.summary(conn, s) for s in found]

    @router.get("/sessions/changed")
    def sessions_changed() -> dict:
        """What the list polls while busy (review 28 Oct, item 3): moves when a step is queued, starts, ends or fails, or a
        recording or conversation comes or goes, from indexes only; the list itself is fetched only when it moves."""
        conn = db()
        runs = rows(conn, "SELECT status, count(*) n FROM stage_runs GROUP BY status")
        ends = [row(conn, f"SELECT max(rowid) m FROM {t}")["m"] for t in ("stage_runs", "recordings", "sessions")]
        live = rows(conn, "SELECT status, count(*) n FROM sessions GROUP BY status")
        going = rows(conn, "SELECT id, progress FROM stage_runs WHERE status='running' ORDER BY id")  # a row's "principle 5 of 14"
        return {"token": repr([(r["status"], r["n"]) for r in runs + live] + ends + [(r["id"], r["progress"]) for r in going])}

    @router.get("/search")
    def search(q: str = Query(max_length=200), person: str | None = None,
               who: Literal["you", "them"] | None = None) -> dict:
        """Lines and review text containing q, newest conversation first (B12): a scan at read time, no index."""
        from .. import search as SR
        return SR.search(db(), q, person, who)

    @router.get("/questions")
    def questions() -> dict:
        """Others' questions and who spoke next, pooled by 1:1 and group (B4, octo/questions.py)."""
        from .. import questions as QS
        return QS.summary(db())

    @router.get("/questions/check")
    def question_check() -> dict:
        """The 1:1 questions to hear, "nobody spoke next" first, and what the owner has heard so far (B4)."""
        from .. import questions as QS
        return QS.to_check(db())

    @router.post("/questions/check", dependencies=[Depends(same_origin)])
    def save_question_check(body: QuestionCheck) -> dict:
        from .. import questions as QS
        try:
            return QS.save_check(db(), body.session_id, body.start, body.verdict)
        except KeyError:
            raise HTTPException(404, "no such question") from None

    @router.get("/aside")
    def aside_list() -> list[dict]:
        """The recordings the owner set aside as not conversations, newest first (B5)."""
        return [{"id": s["id"], "aside": s["aside"], "duration_s": round(s["end_s"] - s["start_s"], 1), "source": s["source"],
                 "started_at": s["started_at"] + s["start_s"] if s["started_at"] else None} for s in AS.listed(db())]

    @router.get("/actions")
    def actions() -> list[dict]:
        """Every open "What to do" step from current reviews (home's "From your reviews")."""
        return SV.open_tasks(db())

    @router.get("/actions/ticktick")
    def actions_ticktick() -> dict:
        """Which of those steps are completed in TickTick, keyed "session_id/item". Asked once per
        home visit (one read per sent task), so the list itself never waits on TickTick."""
        from .. import ticktick as TT
        sent = {}
        conn = db()
        for t in SV.open_tasks(conn):
            if t["ticktick"]:
                sent[f"{t['session_id']}/{t['item']}"] = TT.sent(conn, t["run"])[t["item"]]
        return {"done": TT.done(sent)}

    @router.get("/progress")
    def progress() -> dict:
        from ..insight import principles
        from ..rubric import text as rubric_text
        conn = db()
        out = R.progress(conn, principles(rubric_text()))
        # Where every conversation is, so what's waiting on the owner is visible (review 4 Oct, M03).
        group = {"reviewed": "reviewed", "review": "to_review", "confirm": "waiting_on_you", "failed": "failed",
                 "processing": "processing", "short": "cant_count", "not_you": "cant_count", "paused": "cant_count"}
        states = {k: 0 for k in ("reviewed", "to_review", "waiting_on_you", "failed", "processing", "cant_count", "not_counted")}
        later = {sid for sid, head in PT.heads(conn).items() if sid != head}  # B35: a discussion in parts is one
        for s in rows(conn, "SELECT id, in_gate FROM sessions WHERE status='active'"):
            if s["id"] in later:
                continue
            states["not_counted" if not s["in_gate"] else group.get(SV.todo(conn, s["id"])["kind"], "processing")] += 1
        out["states"] = states
        from ..drills import progress as drill_line
        out["drills"] = drill_line(conn)  # B18: its own line, never in the counts above
        # A buyer's lab shows the beta's card; the first lab is the owner's own, not a tester's.
        from .. import labconfig
        out["first_lab"] = labconfig.origin(conn) == labconfig.JAY
        return out

    @router.post("/recordings", dependencies=[Depends(upload_origin)])
    async def upload(request: Request, name: str, source: str = "phone", modified: float | None = None,
                     output: str | None = None) -> dict:
        ext = Path(name).suffix.lower()
        if ext not in AUDIO_TYPES or source not in SOURCES:
            raise HTTPException(415, f"not an audio file this can read ({', '.join(sorted(AUDIO_TYPES))})")
        if output not in (None, "speakers", "headphones"):  # where a call was heard (B19): speakers get the canceller
            raise HTTPException(422, "output is speakers or headphones")
        tmp = await _receive(request, ext)
        try:
            # When it was recorded: the memo's own header, else the file date the browser saw.
            started = A.recorded_at(tmp) or (modified / 1000 if modified else None) or time.time()
            try:  # converting takes a while: off the event loop, with a connection made on that thread
                rec_id = await run_in_threadpool(lambda: add_recording(
                    db(), tmp, source, started, Path(name).name + (f" (on {output})" if output else ""), output == "speakers"))
            except DuplicateRecording:
                raise HTTPException(409, "already added") from None
            except ValueError as e:
                raise HTTPException(415, str(e)) from None
        finally:
            tmp.unlink(missing_ok=True)  # the lab keeps its own 16 kHz copy
        processor.kick()
        return {"id": rec_id, "started_at": started}

    @router.get("/incoming")
    def incoming() -> list[dict]:
        """Recordings from the last 3 days that haven't produced a conversation (yet)."""
        conn = db()
        out = []
        for rec in rows(conn, "SELECT * FROM recordings WHERE created_at > ? ORDER BY created_at DESC", time.time() - 3 * 86400):
            active = row(conn, "SELECT count(*) n FROM sessions WHERE recording_id=? AND status='active'", rec["id"])["n"]
            dropped = rows(conn, "SELECT id, start_s, end_s, note FROM sessions WHERE recording_id=? AND status='discarded' "
                                 "AND note LIKE 'Jay''s voice not heard%'", rec["id"])
            fc = current(conn, rec["id"], "find_conversations")
            pending = row(conn, "SELECT status FROM stage_runs WHERE target=? AND status IN ('queued','running')", rec["id"])
            if pending or not fc:
                state = "processing"
            elif fc["status"] == "failed":
                state = "failed"
            elif active or row(conn, "SELECT id FROM sessions WHERE recording_id=? AND status='deleted'", rec["id"]):
                continue  # it has conversations, or the owner deleted the ones it had
            elif dropped:
                state = "dropped"
            else:
                state = "nothing"
            out.append({"id": rec["id"], "name": Path(rec["original"] or rec["id"]).name, "source": rec["source"],
                        "started_at": rec["started_at"],
                        "duration_s": rec["duration_s"], "created_at": rec["created_at"], "state": state,
                        "error": fc["error"] if fc and fc["status"] == "failed" else None,
                        "dropped": [{"id": d["id"], "duration_s": round(d["end_s"] - d["start_s"], 1)} for d in dropped]})
        return out

    @router.get("/status")
    def status() -> dict:
        if watchdog.problem:  # answered from memory: the database may be what can't be reached (13 Oct)
            return {"processing": False, "current": None, "queued": None, "code_changed": code_changed(),
                    "free_gb": None, "cant_save": watchdog.problem, "queue_empty_at": None, "queue_steps": 0}
        conn = db()
        running = row(conn, "SELECT stage, target, progress, started_at FROM stage_runs WHERE status='running' LIMIT 1")
        queued = row(conn, "SELECT count(*) n FROM stage_runs WHERE status='queued'")["n"]
        busy = getattr(processor, "running", False)
        from ..privacy import free_gb
        from .. import eta as E
        return {"processing": bool(busy or running or queued), "current": running, "queued": queued,
                "code_changed": code_changed(), "free_gb": free_gb(), "cant_save": None,
                "queue_empty_at": E.cached(conn)["queue_empty_at"] if (running or queued) else None,
                "queue_steps": E.cached(conn)["steps"] if (running or queued) else 0,  # with the ones still to be queued
                "privacy": ST.privacy(conn)}  # what the privacy line on Conversations names (22 Oct)
