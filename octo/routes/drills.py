"""Practice drills (B18, octo/drills.py): takes, prompts, a take and its audio, deleting one; kept apart (K4)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from .. import HOME
from ..privacy import RAW_AUDIO_DAYS
from ..db import row
from ..api import _receive
from .common import AUDIO_TYPES, DeleteSession, FillerWord, Lab, NO_STORE, same_origin, upload_origin


def add(router: APIRouter, lab: Lab) -> None:
    db, processor = lab.db, lab.processor

    @router.get("/drills")
    def drills() -> dict:
        from .. import drills as DR
        return DR.summary(db())

    @router.post("/drills", dependencies=[Depends(upload_origin)])
    async def drill_take(request: Request, prompt: str, name: str = "take.m4a") -> dict:
        from .. import drills as DR
        ext = Path(name).suffix.lower()
        if ext not in AUDIO_TYPES:
            raise HTTPException(415, f"not an audio file this can read ({', '.join(sorted(AUDIO_TYPES))})")
        if not prompt.strip() or len(prompt) > 200:
            raise HTTPException(422, "which prompt it was")
        tmp = await _receive(request, ext)
        try:
            did = await run_in_threadpool(lambda: DR.add(db(), tmp, prompt.strip()))
        except ValueError as e:
            raise HTTPException(415, str(e)) from None
        finally:
            tmp.unlink(missing_ok=True)
        processor.kick()
        return {"id": did}

    @router.post("/drills/prompts", dependencies=[Depends(same_origin)])
    def drill_prompt(body: FillerWord) -> dict:
        from .. import drills as DR
        try:
            DR.set_prompt(db(), body.phrase, body.on)
        except DR.DrillError as e:
            raise HTTPException(422, str(e))
        return DR.summary(db())

    @router.get("/drills/{drill_id}")
    def drill(drill_id: str) -> dict:
        from .. import drills as DR
        if not (d := DR.detail(db(), drill_id)):
            raise HTTPException(404, "no such take")
        return d

    @router.get("/drills/{drill_id}/audio")
    def drill_audio(drill_id: str) -> FileResponse:
        d = row(db(), "SELECT path, audio_deleted_at FROM drills WHERE id=?", drill_id)
        if not d:
            raise HTTPException(404, "no such take")
        if d["audio_deleted_at"]:
            raise HTTPException(410, f"the audio was deleted after {RAW_AUDIO_DAYS} days; the numbers stay")
        if not (HOME / d["path"]).is_file():  # FileResponse answers a missing file with a 500
            raise HTTPException(404, "the take's audio isn't on this Mac")
        return FileResponse(HOME / d["path"], media_type="audio/flac", headers=NO_STORE)

    @router.post("/drills/{drill_id}/delete", dependencies=[Depends(same_origin)])
    def drill_delete(drill_id: str, body: DeleteSession) -> dict:
        from .. import drills as DR
        if body.confirm != "delete":
            raise HTTPException(422, 'confirm with "delete"')
        if not DR.delete(db(), drill_id):
            raise HTTPException(404, "no such take")
        return {"deleted": drill_id}
