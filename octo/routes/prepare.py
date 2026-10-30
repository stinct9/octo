"""Prepare for a conversation (B36, octo/prepare.py): what it would read, make a plan, read it, delete it."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .common import Lab, same_origin


class SomeoneNew(BaseModel):
    name: str
    invitee: str | None = None  # the calendar invitee's key (events.invitee_key), kept as them


class PrepareBody(BaseModel):
    about: str | None = None
    names: list[str] = []
    mentions: list[str] | None = None  # the mention conversations left ticked on the page (session ids); the rest aren't sent


def add(router: APIRouter, lab: Lab) -> None:
    db = lab.db

    from .. import prepare as PP

    def make(person_id: str) -> None:
        PP.run_in_background(db, person_id)  # looked up at call time, so a test can run it in place

    def answer(fn):
        try:
            return fn()
        except PP.PrepareError as e:
            raise HTTPException(e.status, str(e)) from None

    @router.post("/people/prepare", dependencies=[Depends(same_origin)])
    def prepare_someone_new(body: SomeoneNew) -> dict:
        """Someone not in People yet: added by name (no voiceprint), then prepared for like anyone."""
        return {"person_id": answer(lambda: PP.someone_new(db(), body.name, body.invitee))}

    @router.get("/people/{person_id}/prepare")
    def prepare_view(person_id: str) -> dict:
        return answer(lambda: PP.view(db(), person_id))

    @router.get("/people/{person_id}/prepare/pick")
    def prepare_pick(person_id: str, names: list[str] = Query(default=[]), skip: list[str] = Query(default=[])) -> dict:
        return answer(lambda: PP.preview(PP.pick(db(), person_id, names, skip)))

    @router.post("/people/{person_id}/prepare", dependencies=[Depends(same_origin)])
    def prepare_start(person_id: str, body: PrepareBody) -> dict:
        got = answer(lambda: PP.start(db(), person_id, body.about, body.names, body.mentions))
        make(person_id)
        return got

    @router.post("/people/{person_id}/prepare/delete", dependencies=[Depends(same_origin)])
    def prepare_delete(person_id: str) -> dict:
        conn = db()
        PP.delete(conn, person_id)
        return answer(lambda: PP.view(conn, person_id))
