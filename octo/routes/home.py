"""Home's cards: the focus, a mark from the phone, your week, today's agenda."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import review as R
from ..db import load_json, row
from .common import Lab, same_origin, session_or_404


class WeekBody(BaseModel):
    week: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


class StartFocus(BaseModel):
    text: str
    session_id: str | None = None
    run: str | None = None
    item: str | None = None
    measure: str | None = None  # "fillers": the focus shows the owner's fillers per 100 words since it started


def add(router: APIRouter, lab: Lab) -> None:
    db = lab.db

    # B34: home's agenda, read from the calendar's events (B23); empty without a calendar.
    @router.get("/agenda")
    def agenda() -> list[dict]:
        from .. import events as EV
        return EV.agenda(db())

    # B17: your week, and "Octo hasn't heard from…". Read at request time; nothing re-runs.
    @router.get("/week")
    def week_card(week: str | None = None) -> dict | None:
        from datetime import date
        from .. import week as W
        conn = db()
        if week is None:
            return W.current(conn)
        try:
            d = date.fromisoformat(week)
        except ValueError:
            raise HTTPException(422, "week is a date, YYYY-MM-DD")
        if d.weekday():
            raise HTTPException(422, "week starts on a Monday")
        return W.card(conn, d)

    @router.post("/week/dismiss", dependencies=[Depends(same_origin)])
    def week_dismiss(body: WeekBody) -> dict:
        from .. import week as W
        try:
            W.dismiss(db(), body.week)
        except ValueError:
            raise HTTPException(422, "week is a date, YYYY-MM-DD")
        return {"ok": True}

    @router.get("/focus")
    def focus_summary() -> dict:
        from .. import focus as F
        return F.summary(db())

    @router.post("/focus", dependencies=[Depends(same_origin)])
    def focus_start(body: StartFocus) -> dict:
        from .. import focus as F
        conn = db()
        if body.session_id is not None or body.run is not None or body.item is not None:
            session_or_404(conn, body.session_id or "")
            run = row(conn, "SELECT * FROM stage_runs WHERE id=? AND target=? AND stage='insight' AND status='done'",
                      body.run, body.session_id)
            if not run or not any(it["id"] == body.item for it in R.items(load_json(run["output"]))):
                raise HTTPException(404, "no such insight")
        try:
            F.start(conn, body.text, body.session_id, body.run, body.item, measure=body.measure)
        except F.FocusError as e:
            raise HTTPException(422, str(e))
        return F.summary(conn)

    @router.post("/focus/stop", dependencies=[Depends(same_origin)])
    def focus_stop() -> dict:
        from .. import focus as F
        conn = db()
        F.stop(conn)
        return F.summary(conn)

    @router.post("/marks", dependencies=[Depends(same_origin)])
    def mark_now() -> dict:
        """B16: the phone's Mark button. The Mac's clock places it; it lands in whatever conversation was going on."""
        from .. import marks as MK
        return {"mark": MK.add(db())}

    @router.post("/marks/{mark_id}/delete", dependencies=[Depends(same_origin)])
    def mark_undo(mark_id: str) -> dict:
        """The phone's Undo on a mark it just made (within a minute); anything else is refused."""
        from .. import marks as MK
        if not MK.undo(db(), mark_id):
            raise HTTPException(409, "only a mark this phone made in the last minute can be undone")
        return {"ok": True}
