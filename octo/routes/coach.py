"""The coach (docs/coach-plan.md §4.3): its notes, asking it, the owner's marks, its focus, seen."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import row, tx
from .common import Lab, Solved, same_origin


class CoachMark(BaseModel):
    part: str                   # work_on | keep | also0 | also1
    verdict: str | None = None  # right | partly | wrong; "" clears it
    novelty: str | None = None  # new | knew; "" clears it


class CoachFocus(BaseModel):
    text: str | None = None     # The owner's wording; the note's try line if not given
    measure: str | None = None  # a number to show with it (octo/measures.py); "" for none; the note's if not given


def add(router: APIRouter, lab: Lab) -> None:
    db, coach = lab.db, lab.coach

    # ---- the coach (docs/coach-plan.md §4.3) ----------------------------------------------------
    @router.get("/coach")
    def coach_summary() -> dict:
        from .. import coach as CO
        return CO.summary(db())

    @router.post("/coach", dependencies=[Depends(same_origin)])
    def coach_ask() -> dict:
        from .. import coach as CO
        conn = db()
        try:
            note_id = CO.start(conn)
        except CO.CoachError as e:
            raise HTTPException(e.status, str(e))
        coach(note_id)
        return CO.summary(conn)

    def note_or_404(conn, note_id: str) -> dict:
        n = row(conn, "SELECT * FROM coach_notes WHERE id=?", note_id)
        if not n:
            raise HTTPException(404, "no such note")
        return n

    @router.get("/coach/{note_id}")
    def coach_note(note_id: str) -> dict:
        from .. import coach as CO
        conn = db()
        return CO.view(conn, note_or_404(conn, note_id))

    @router.post("/coach/{note_id}/feedback", dependencies=[Depends(same_origin)])
    def coach_feedback(note_id: str, body: CoachMark) -> dict:
        from .. import coach as CO
        conn = db()
        try:
            return {"feedback": CO.rate(conn, note_id, body.part, body.verdict, body.novelty)}
        except CO.CoachError as e:
            raise HTTPException(e.status, str(e))

    @router.post("/coach/{note_id}/focus", dependencies=[Depends(same_origin)])
    def coach_focus(note_id: str, body: CoachFocus) -> dict:
        from .. import coach as CO
        from .. import focus as F
        conn = db()
        try:
            with tx(conn):
                CO.make_focus(conn, note_id, body.text, body.measure)
        except CO.CoachError as e:
            raise HTTPException(e.status, str(e))
        return {"focus": F.summary(conn), "note": CO.view(conn, note_or_404(conn, note_id))}

    @router.put("/coach/{note_id}/solved", dependencies=[Depends(same_origin)])
    def coach_solved(note_id: str, body: Solved) -> dict:
        from .. import coach as CO
        from .. import solved as SO
        conn = db()
        n = note_or_404(conn, note_id)
        try:
            SO.put(conn, body.who, body.what, body.source, note_id=note_id)
        except SO.SolvedError as e:
            raise HTTPException(422, str(e))
        return CO.view(conn, n)

    @router.post("/coach/{note_id}/seen", dependencies=[Depends(same_origin)])
    def coach_seen(note_id: str) -> dict:
        from .. import coach as CO
        conn = db()
        note_or_404(conn, note_id)
        CO.seen(conn, note_id)
        return {"ok": True}
