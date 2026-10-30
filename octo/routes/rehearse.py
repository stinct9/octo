"""Rehearsals (B26, octo/rehearse.py): typed, with the model as the other person; kept apart (K4)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .common import DeleteSession, Lab, RehearsalLine, same_origin


class RehearsalStart(BaseModel):  # B26: someone from People, or someone described
    person_id: str | None = Field(default=None, max_length=64)
    who: str | None = Field(default=None, max_length=300)
    hard: str = Field(max_length=300)
    want: str = Field(max_length=300)


def add(router: APIRouter, lab: Lab) -> None:
    db = lab.db

    @router.get("/rehearsals")
    def rehearsals() -> dict:
        from .. import rehearse as RH
        return RH.summary(db())

    def rehearsing(fn: Callable[[], dict]) -> dict:
        from .. import rehearse as RH
        try:
            return fn()
        except RH.RehearsalError as e:
            raise HTTPException(e.status, str(e)) from None

    @router.post("/rehearsals", dependencies=[Depends(same_origin)])
    def rehearsal_start(body: RehearsalStart) -> dict:
        from .. import rehearse as RH
        conn = db()
        return rehearsing(lambda: RH.view(conn, RH.start(conn, body.person_id, body.who, body.hard, body.want)))

    @router.get("/rehearsals/{rehearsal_id}")
    def rehearsal(rehearsal_id: str) -> dict:
        from .. import rehearse as RH
        if not (r := RH.view(db(), rehearsal_id)):
            raise HTTPException(404, "no such rehearsal")
        return r

    @router.post("/rehearsals/{rehearsal_id}/say", dependencies=[Depends(same_origin)])
    def rehearsal_say(rehearsal_id: str, body: RehearsalLine) -> dict:  # the call runs here, off the event loop (a def)
        from .. import rehearse as RH
        return rehearsing(lambda: RH.say(db(), rehearsal_id, body.text))

    @router.post("/rehearsals/{rehearsal_id}/stop", dependencies=[Depends(same_origin)])
    def rehearsal_stop(rehearsal_id: str) -> dict:
        from .. import rehearse as RH
        return rehearsing(lambda: RH.stop(db(), rehearsal_id))

    @router.post("/rehearsals/{rehearsal_id}/delete", dependencies=[Depends(same_origin)])
    def rehearsal_delete(rehearsal_id: str, body: DeleteSession) -> dict:
        from .. import rehearse as RH
        if body.confirm != "delete":
            raise HTTPException(422, 'confirm with "delete"')
        if not RH.delete(db(), rehearsal_id):
            raise HTTPException(404, "no such rehearsal")
        return {"deleted": rehearsal_id}
