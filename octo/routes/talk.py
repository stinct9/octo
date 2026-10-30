"""Talk a conversation through (B31, octo/talk.py): the thread, the owner's message, Start over; its effort (Settings)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .common import Lab, RehearsalLine, same_origin, session_or_404


class TalkEffort(BaseModel):
    effort: str


def add(router: APIRouter, lab: Lab) -> None:
    db, talker = lab.db, lab.talker

    from .. import talk as TK

    talker = talker or (lambda session_id: TK.run_in_background(db, session_id))

    @router.get("/sessions/{session_id}/talk")
    def talk(session_id: str) -> dict:
        from .. import talk as TK
        conn = db()
        session_or_404(conn, session_id)
        return TK.view(conn, session_id)

    @router.post("/sessions/{session_id}/talk", dependencies=[Depends(same_origin)])
    def talk_say(session_id: str, body: RehearsalLine) -> dict:
        from .. import talk as TK
        conn = db()
        session_or_404(conn, session_id)
        try:
            got = TK.say(conn, session_id, body.text)
        except TK.TalkError as e:
            raise HTTPException(e.status, str(e)) from None
        talker(session_id)
        return got

    @router.post("/sessions/{session_id}/talk/clear", dependencies=[Depends(same_origin)])
    def talk_clear(session_id: str) -> dict:
        from .. import talk as TK
        conn = db()
        session_or_404(conn, session_id)
        return TK.clear(conn, session_id)

    # How hard it thinks (Jay 29 Oct): low / medium / high, medium until changed. Mac only, like the rest of /settings.
    @router.get("/settings/talk")
    def talk_settings() -> dict:
        return {"effort": TK.effort(db()), "efforts": list(TK.EFFORTS)}

    @router.post("/settings/talk", dependencies=[Depends(same_origin)])
    def talk_settings_set(body: TalkEffort) -> dict:
        try:
            return TK.set_effort(db(), body.effort) | {"efforts": list(TK.EFFORTS)}
        except TK.TalkError as e:
            raise HTTPException(e.status, str(e)) from None
