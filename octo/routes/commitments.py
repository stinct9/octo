"""Commitments both ways (B14): with a person, marked, into TickTick, and the owner's check."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..db import row
from .common import COMMITMENT_ID, Lab, _TICKTICK, same_origin, session_or_404


class CommitmentMark(BaseModel):
    state: Literal["done", "dropped", "did", "didnt"] | None = None


class CommitmentVerdict(BaseModel):
    verdict: Literal["right", "partly", "wrong"] | None = None


def add(router: APIRouter, lab: Lab) -> None:
    db = lab.db

    # B14: commitments both ways. Marking one is the owner's note on what was said, never "acted" on Progress.
    def commitment_or_404(conn, commitment_id: str) -> dict:
        from .. import commitments as CM
        c = CM.get(conn, commitment_id) if COMMITMENT_ID.match(commitment_id) else None
        if not c:
            raise HTTPException(404, "no such commitment")
        session_or_404(conn, c["session_id"])
        return c

    @router.get("/commitments/check")
    def commitments_check() -> dict:
        from .. import commitments as CM
        return CM.to_check(db())

    @router.post("/commitments/{commitment_id}/check", dependencies=[Depends(same_origin)])
    def commitment_judge(commitment_id: str, body: CommitmentVerdict) -> dict:
        from .. import commitments as CM
        conn = db()
        commitment_or_404(conn, commitment_id)
        try:
            return CM.judge(conn, commitment_id, body.verdict)
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.get("/commitments")
    def commitments_with(person: str) -> dict:
        from .. import commitments as CM
        from .. import ticktick as TT
        conn = db()
        if not row(conn, "SELECT 1 FROM people WHERE id=?", person):
            raise HTTPException(404, "no such person")
        return {"items": CM.for_person(conn, person), "ticktick_connected": TT.status()["connected"]}

    @router.post("/commitments/{commitment_id}", dependencies=[Depends(same_origin)])
    def commitment_mark(commitment_id: str, body: CommitmentMark) -> dict:
        from .. import commitments as CM
        conn = db()
        commitment_or_404(conn, commitment_id)
        try:
            return CM.mark(conn, commitment_id, body.state)
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.post("/commitments/{commitment_id}/ticktick", dependencies=[Depends(same_origin)])
    def commitment_ticktick(commitment_id: str) -> dict:
        from .. import commitments as CM
        from .. import ticktick as TT
        conn = db()
        commitment_or_404(conn, commitment_id)
        with _TICKTICK:  # two quick presses send it once
            try:
                return CM.to_ticktick(conn, commitment_id)
            except ValueError as e:
                raise HTTPException(422, str(e))
            except TT.TickTickError as e:
                raise HTTPException(502, str(e))
