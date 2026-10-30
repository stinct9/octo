"""Fillers over time (docs/coach-plan.md §4.2): the list, the live cue's, hedges, the check by ear."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .common import FillerWord, Lab, same_origin, session_or_404


class LiveCue(BaseModel):
    mac: bool


class FillerCheckIn(BaseModel):
    """A filler check by ear: the stretch listened to and how many fillers the owner heard themselves say."""
    session_id: str
    start_s: float
    end_s: float
    by_ear: int


def add(router: APIRouter, lab: Lab) -> None:
    db = lab.db

    # ---- fillers over time (docs/coach-plan.md §4.2) ------------------------------------------
    @router.get("/fillers")
    def fillers() -> dict:
        from .. import fillers as FL
        return FL.summary(db())

    @router.post("/fillers/words", dependencies=[Depends(same_origin)])
    def filler_word(body: FillerWord) -> dict:
        from .. import fillers as FL
        conn = db()
        try:
            FL.set_phrase(conn, body.phrase, body.on, "suggested" if body.source == "suggested" else "typed")
        except FL.FillerError as e:
            raise HTTPException(422, str(e))
        return FL.summary(conn)

    @router.get("/fillers/live")
    def fillers_live() -> dict:
        """B21: the owner's list for the live cue on the phone and in the Octo app, and the Mac's switch."""
        from .. import fillers as FL
        return FL.live(db())

    @router.post("/fillers/live", dependencies=[Depends(same_origin)])
    def fillers_live_set(body: LiveCue) -> dict:
        from .. import fillers as FL
        return FL.set_live_mac(db(), body.mac)

    @router.post("/delivery/hedges", dependencies=[Depends(same_origin)])
    def hedge_word(body: FillerWord) -> dict:
        """A word on or off the owner's hedge list (octo/delivery.py, B10): counted at read time, nothing re-runs."""
        from .. import delivery as D
        from .. import fillers as FL
        try:
            return {"phrases": D.set_hedge(db(), body.phrase, body.on)}
        except FL.FillerError as e:
            raise HTTPException(422, str(e))

    @router.get("/fillers/check")
    def filler_check(session_id: str | None = None) -> dict:
        """What to listen to for the check by ear: conversations to choose from and, for the one
        chosen (the newest by default), the stretch with the most of the owner's speech."""
        from .. import fillers as FL
        conn = db()
        cands = FL.check_candidates(conn)
        sid = session_id or (cands[0]["session_id"] if cands else None)
        if session_id:
            session_or_404(conn, session_id)
        return {"candidates": cands, "session_id": sid, "window": FL.check_window(conn, sid) if sid else None,
                "summary": FL.checks_summary(conn)}

    @router.post("/fillers/check", dependencies=[Depends(same_origin)])
    def filler_check_save(body: FillerCheckIn) -> dict:
        from .. import fillers as FL
        conn = db()
        session_or_404(conn, body.session_id)
        try:
            return FL.save_check(conn, body.session_id, body.start_s, body.end_s, body.by_ear)
        except FL.FillerError as e:
            raise HTTPException(422, str(e))
