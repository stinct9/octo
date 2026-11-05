"""One conversation's page: it and its audio, split and join back, one discussion in parts or not, delete a stretch or all of it, the review's answers
and marks, its tasks into TickTick, the focus check, the reflection, set aside or kept, retried, redone."""

from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import aside as AS
from .. import parts as PT
from .. import review as R
from .. import sessions as SV
from .. import split as S
from ..privacy import RAW_AUDIO_DAYS
from ..db import load_json, row, tx
from ..pipeline import current, retry, review_outdated
from .common import DeleteSession, Lab, NO_STORE, OwnerWords, SESSION_ID, _TICKTICK, same_origin, session_or_404


class DeleteRange(BaseModel):
    start: float
    end: float


class Split(BaseModel):
    at: float | list[float]  # seconds into the conversation: one cut, or several at once


class Whole(BaseModel):
    at: float  # seconds into the conversation: the suggestion answered


class Group(BaseModel):
    side: Literal["before", "after"]  # the conversation right before or after it, in any recording


class Ungroup(BaseModel):
    first: str   # two neighbouring parts (as the page has them in `parts.list`)
    second: str


class Aside(BaseModel):
    kind: Literal["tv", "music", "other"] | None = None
    undo: bool = False  # take back the last answer, leaving none ("Sort them")


class ToTickTick(BaseModel):
    run: str
    item: str


class FocusCheck(BaseModel):
    focus_id: str
    answer: str | None = None


class Provisional(BaseModel):
    clear: bool  # The owner listened and the transcript holds up: the review counts in full


class Feedback(BaseModel):
    run: str
    item: str
    verdict: str | None = None  # "" clears it ("Not right?" is optional since 6 Oct)
    novelty: str | None = None
    useful: str | None = None
    acted: bool | None = None
    note: str | None = None
    let_go: bool | None = None  # a "What to do" step the owner won't do: off home's list, not acted on


class Answer(BaseModel):
    """The one question, "What here was new to you?": the items picked, or nothing new."""
    run: str
    new: list[str] = []
    nothing_new: bool = False


def add(router: APIRouter, lab: Lab) -> None:
    db, processor = lab.db, lab.processor

    @router.get("/sessions/{session_id}")
    def session(session_id: str) -> dict:
        conn = db()
        sess = session_or_404(conn, session_id, aside_ok=True)
        page = SV.detail(conn, sess)
        page["review_outdated"] = bool(page["insight_run"] and review_outdated(conn, session_id))  # the planner's to say
        return page

    @router.post("/sessions/{session_id}/join-back", dependencies=[Depends(same_origin)])
    def join_back(session_id: str) -> dict:
        """Undo a split (the owner's or the lab's): the two parts are one conversation again."""
        conn = db()
        session_or_404(conn, session_id)
        try:
            with tx(conn):
                joined = S.join_back(conn, session_id)
        except KeyError:
            raise HTTPException(404, "no split to join back") from None
        processor.kick()
        return {"session": joined}

    @router.post("/sessions/{session_id}/split", dependencies=[Depends(same_origin)])
    def split(session_id: str, body: Split) -> dict:
        """Several discussions in one: the first part stays this conversation, each later part becomes a new one."""
        conn = db()
        sess = session_or_404(conn, session_id)
        cuts = sorted(body.at if isinstance(body.at, list) else [body.at])
        edges = [0.0, *cuts, sess["end_s"] - sess["start_s"]]
        if not cuts or any(b - a < S.MIN_PART_S / 6 for a, b in zip(edges, edges[1:])):
            raise HTTPException(422, "split at least 30 s from either end and from each other")
        with tx(conn):
            new = S.split_at(conn, sess, [sess["start_s"] + at for at in cuts])
        processor.kick()
        return {"split_at": [round(at, 1) for at in cuts] if isinstance(body.at, list) else round(body.at, 1),
                "new_session": new[0], "new_sessions": new}

    @router.post("/sessions/{session_id}/split/keep-whole", dependencies=[Depends(same_origin)])
    def keep_whole(session_id: str, body: Whole) -> dict:
        """It's one conversation: the suggestion to split at `at` goes, and the lab never cuts near it."""
        conn = db()
        sess = session_or_404(conn, session_id)
        if not 0 < body.at < sess["end_s"] - sess["start_s"]:
            raise HTTPException(422, "that point isn't in this conversation")
        with tx(conn):
            S.keep_whole(conn, sess, body.at)
        return {"kept_whole": round(body.at, 1)}

    @router.post("/sessions/{session_id}/group", dependencies=[Depends(same_origin)])
    def group(session_id: str, body: Group) -> dict:
        """One discussion in parts (B35): this and the conversation right before or after it, read as one."""
        conn = db()
        session_or_404(conn, session_id)
        try:
            with tx(conn):
                head = PT.group_with(conn, session_id, body.side)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        processor.kick()
        return {"session": head}

    @router.post("/sessions/{session_id}/ungroup", dependencies=[Depends(same_origin)])
    def ungroup(session_id: str, body: Ungroup) -> dict:
        """Not one discussion (the owner's word, over the lab's or their own): two again, and the lab never groups
        them again."""
        conn = db()
        session_or_404(conn, session_id)
        try:
            with tx(conn):
                PT.ungroup(conn, body.first, body.second)
        except KeyError:
            raise HTTPException(404, "those aren't parts of one discussion") from None
        processor.kick()
        return {"session": session_id}

    @router.get("/sessions/{session_id}/audio")
    def audio(session_id: str) -> FileResponse:
        conn = db()
        sess = session_or_404(conn, session_id, aside_ok=True)
        if sess["audio_deleted_at"]:
            raise HTTPException(410, f"the audio was deleted after {RAW_AUDIO_DAYS} days; the transcript stays")
        from ..privacy import clip
        return FileResponse(clip(sess), media_type="audio/wav", headers=NO_STORE)

    @router.post("/sessions/{session_id}/delete-range", dependencies=[Depends(same_origin)])
    def delete_range(session_id: str, body: DeleteRange) -> dict:
        from ..privacy import delete_range as silence_range
        conn = db()
        session_or_404(conn, session_id)
        try:
            silence_range(conn, session_id, body.start, body.end)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        processor.kick()  # transcribed again from the audio that's left
        return {"todo": SV.todo(conn, session_id)}

    @router.post("/sessions/{session_id}/delete", dependencies=[Depends(same_origin)])
    def delete(session_id: str, body: DeleteSession) -> dict:
        from ..privacy import delete_session
        conn = db()
        session_or_404(conn, session_id, aside_ok=True)
        if body.confirm != "delete":
            raise HTTPException(422, 'confirm with "delete"')
        gone = delete_session(conn, session_id, work=body.work)
        processor.kick()  # voiceprints may have changed: re-match the other conversations
        return {"deleted": session_id, "parts": gone}  # B35: every part of one discussion deleted with it

    @router.post("/sessions/{session_id}/feedback", dependencies=[Depends(same_origin)])
    def feedback(session_id: str, body: Feedback) -> dict:
        conn = db()
        session_or_404(conn, session_id)
        run = row(conn, "SELECT * FROM stage_runs WHERE id=? AND target=? AND stage='insight' AND status='done'",
                  body.run, session_id)
        if not run:
            raise HTTPException(404, "no such insight run for this conversation")
        item = next((it for it in R.items(load_json(run["output"])) if it["id"] == body.item), None)
        if not item:
            raise HTTPException(404, "no such insight")
        if body.verdict not in (None, "", *R.VERDICTS) or body.novelty not in (None, *R.NOVELTY) \
                or body.useful not in (None, *R.USEFUL) or (body.note and len(body.note) > 1000):
            raise HTTPException(422, "unexpected answer")
        conn.execute("INSERT OR IGNORE INTO feedback (insight_run, item, session_id, updated_at) VALUES (?, ?, ?, ?)",
                     (run["id"], item["id"], session_id, time.time()))
        for field in ("verdict", "novelty", "useful", "note"):
            if getattr(body, field) is not None:
                conn.execute(f"UPDATE feedback SET {field}=?, updated_at=? WHERE insight_run=? AND item=?",
                             (getattr(body, field) or None, time.time(), run["id"], item["id"]))
        if body.acted is not None:
            conn.execute("UPDATE feedback SET acted=?, updated_at=? WHERE insight_run=? AND item=?",
                         (int(body.acted), time.time(), run["id"], item["id"]))
        if body.let_go is not None:
            conn.execute("UPDATE feedback SET let_go_at=?, updated_at=? WHERE insight_run=? AND item=?",
                         (time.time() if body.let_go else None, time.time(), run["id"], item["id"]))
        if body.acted is not None or body.let_go is not None:  # the same promise as a commitment: it follows (5 Nov)
            from .. import commitments as CM
            CM.close_paired(conn, CM.paired_in(conn, session_id, load_json(run["output"])).get(item["id"]),
                            acted=body.acted, let_go=body.let_go)
        R.mark_reviewed_if_done(conn, session_id, run["id"])
        return {"feedback": SV.feedback_view(conn, run["id"]), "todo": SV.todo(conn, session_id)}

    @router.post("/sessions/{session_id}/answer", dependencies=[Depends(same_origin)])
    def answer(session_id: str, body: Answer) -> dict:
        """The one question per conversation (since 6 Oct): answering it makes the conversation reviewed."""
        conn = db()
        session_or_404(conn, session_id)
        try:
            with tx(conn):
                R.answer(conn, session_id, body.run, body.new, body.nothing_new)
        except R.AnswerError as e:
            raise HTTPException(e.status, str(e))
        return {"feedback": SV.feedback_view(conn, body.run), "answer": R.answer_for(conn, body.run),
                "todo": SV.todo(conn, session_id)}

    @router.post("/sessions/{session_id}/ticktick", dependencies=[Depends(same_origin)])
    def to_ticktick(session_id: str, body: ToTickTick) -> dict:
        """One "What to do" task into the owner's TickTick, once (a second press returns the first task)."""
        conn = db()
        sess = session_or_404(conn, session_id)
        run = row(conn, "SELECT * FROM stage_runs WHERE id=? AND target=? AND stage='insight' AND status='done'",
                  body.run, session_id)
        if not run:
            raise HTTPException(404, "no such insight run for this conversation")
        tasks = load_json(run["output"]).get("next") or []
        i = int(body.item[1:]) if body.item[:1] == "n" and body.item[1:].isdigit() else -1
        if not 0 <= i < len(tasks):
            raise HTTPException(404, "no such task")
        with _TICKTICK:  # two quick presses (or two tabs) send it once
            return _send_task(conn, sess, session_id, run, tasks, i, body.item)

    def _send_task(conn, sess, session_id, run, tasks, i, item) -> dict:
        from .. import ticktick as TT
        from ..review_any import when_text
        done = TT.sent(conn, run["id"])
        if item not in done:
            when = when_text(sess["started_at"] + sess["start_s"]) if sess["started_at"] else session_id
            task = tasks[i] if "title" in tasks[i] else {"title": tasks[i].get("action", ""), "why": tasks[i].get("why")}
            try:
                got = TT.add(task, when, session_id)
            except TT.TickTickError as e:
                raise HTTPException(502, str(e))
            TT.record(conn, run["id"], item, session_id, got)
        return {"ticktick": TT.sent(conn, run["id"])}

    @router.get("/sessions/{session_id}/ticktick/done")
    def ticktick_done(session_id: str) -> dict:
        """Which sent tasks are completed in TickTick, to suggest marking them acted on (the owner decides).
        Unknown (None) when TickTick can't be asked; the page just doesn't suggest anything then."""
        from .. import ticktick as TT
        conn = db()
        session_or_404(conn, session_id)
        run = row(conn, "SELECT id FROM stage_runs WHERE target=? AND stage='insight' AND is_current=1 AND status='done'", session_id)
        return {"done": TT.done(TT.sent(conn, run["id"])) if run else {}}

    @router.post("/sessions/{session_id}/reflection", dependencies=[Depends(same_origin)])
    def reflection_set(session_id: str, body: OwnerWords) -> dict:
        """B15: how it went, in the owner's words. The review never waits for it; the coach reads it."""
        from .. import intention as IN
        conn = db()
        session_or_404(conn, session_id)
        try:
            return {"reflection": IN.reflect(conn, session_id, body.text)}
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.post("/sessions/{session_id}/focus", dependencies=[Depends(same_origin)])
    def focus_check(session_id: str, body: FocusCheck) -> dict:
        from .. import focus as F
        conn = db()
        sess = session_or_404(conn, session_id)
        when = sess["started_at"] + sess["start_s"] if sess["started_at"] else None
        f = F.at(conn, when)
        if not f or f["id"] != body.focus_id or f["session_id"] == session_id:
            raise HTTPException(409, "that focus wasn't active during this conversation")
        try:
            F.check(conn, body.focus_id, session_id, body.answer)
        except F.FocusError as e:
            raise HTTPException(422, str(e))
        ins = current(conn, session_id, "insight")
        return {"focus": F.for_session(conn, session_id, when),
                "feedback": SV.feedback_view(conn, ins["id"]) if ins else {}}

    @router.post("/sessions/{session_id}/aside", dependencies=[Depends(same_origin)])
    def set_aside(session_id: str, body: Aside) -> dict:
        """Not a conversation (TV, music, something else), or null: it is one (put back if it was set aside,
        and not asked about again; B5)."""
        conn = db()
        session_or_404(conn, session_id, aside_ok=True)
        try:
            if body.undo:
                if AS.unanswer(conn, session_id):
                    processor.kick()
            elif body.kind is None:
                if AS.put_back(conn, session_id):
                    processor.kick()  # carries on from where it stopped
            else:
                AS.mark(conn, session_id, body.kind)
        except KeyError:
            raise HTTPException(404, "no such conversation") from None
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return {"aside": body.kind, "todo": SV.todo(conn, session_id)}

    @router.post("/sessions/{session_id}/keep", dependencies=[Depends(same_origin)])
    def keep(session_id: str) -> dict:
        conn = db()
        if not SESSION_ID.match(session_id) or not conn.execute(
                "UPDATE sessions SET status='active', kept=1, note='restored by you' WHERE id=? AND status='discarded' "
                "AND note LIKE 'Your voice not heard%'",
                (session_id,)).rowcount:
            raise HTTPException(404, "nothing to restore")
        processor.kick()
        return {"restored": session_id}

    @router.post("/sessions/{session_id}/retry", dependencies=[Depends(same_origin)])
    def retry_failed(session_id: str) -> dict:
        conn = db()
        session_or_404(conn, session_id)
        if not retry(conn, session_id):
            raise HTTPException(404, "nothing failed")
        processor.kick()
        return {"todo": SV.todo(conn, session_id)}

    @router.post("/sessions/{session_id}/provisional", dependencies=[Depends(same_origin)])
    def provisional(session_id: str, body: Provisional) -> dict:
        """Clear the provisional mark (J9): set from the CLI with a reason, cleared on the page."""
        conn = db()
        session_or_404(conn, session_id)
        if not body.clear:
            raise HTTPException(422, "set it with `octo provisional SESSION --reason ...`")
        conn.execute("UPDATE sessions SET provisional=NULL WHERE id=?", (session_id,))
        return {"provisional": None}

    @router.post("/sessions/{session_id}/redo-review", dependencies=[Depends(same_origin)])
    def redo_review(session_id: str) -> dict:
        """A rated review is kept when what it was made from changes (planning.pinned_review);
        this asks for a fresh one. The old one and its ratings stay in the database."""
        conn = db()
        session_or_404(conn, session_id)
        ins = current(conn, session_id, "insight")
        if not ins and not PT.whole(conn, session_id):
            raise HTTPException(404, "no review to redo")
        # B35: on one discussion in parts, a review of the whole, answered parts or not ("whole": none of its own yet)
        conn.execute("UPDATE sessions SET redo_review=? WHERE id=?", (ins["id"] if ins else "whole", session_id))
        processor.kick()
        return {"todo": SV.todo(conn, session_id)}
