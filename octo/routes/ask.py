"""Ask your conversations (B24, octo/ask.py): ask, read an answer, keep it, delete it."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .common import Lab, same_origin


class AskBody(BaseModel):
    question: str


def add(router: APIRouter, lab: Lab) -> None:
    db, asker = lab.db, lab.asker

    # ---- ask your conversations (B24, octo/ask.py) ----------------------------------------------
    from .. import ask as AK

    asker = asker or (lambda ask_id: AK.ask_in_background(db, ask_id))

    @router.get("/ask")
    def ask_summary() -> dict:
        return AK.summary(db())

    @router.post("/ask", dependencies=[Depends(same_origin)])
    def ask_start(body: AskBody) -> dict:
        try:
            got = AK.start(db(), body.question)
        except AK.AskError as e:
            raise HTTPException(e.status, str(e))
        if got["status"] == "running":
            asker(got["id"])
        return got

    @router.get("/ask/{ask_id}")
    def ask_one(ask_id: str) -> dict:
        try:
            return AK.get(db(), ask_id)
        except AK.AskError as e:
            raise HTTPException(e.status, str(e))

    @router.post("/ask/{ask_id}/keep", dependencies=[Depends(same_origin)])
    def ask_keep(ask_id: str) -> dict:
        try:
            return AK.keep(db(), ask_id)
        except AK.AskError as e:
            raise HTTPException(e.status, str(e))

    @router.post("/ask/{ask_id}/delete", dependencies=[Depends(same_origin)])
    def ask_delete(ask_id: str) -> dict:
        AK.delete(db(), ask_id)
        return {"ok": True}
