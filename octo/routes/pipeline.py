"""The Pipeline page (Mac only): every step's model and why, Claude's use, what a switch would redo, switching."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import sessions as SV
from .. import settings as ST
from .common import Lab, same_origin


class Choose(BaseModel):
    choice: str


def add(router: APIRouter, lab: Lab) -> None:
    db, processor = lab.db, lab.processor

    @router.get("/pipeline")
    def pipeline_steps() -> list[dict]:
        return ST.describe(db())

    @router.get("/pipeline/claude")
    def pipeline_claude() -> dict:
        """Claude in the last 5 hours (Claude's usage window) and since midnight (Mac only, like the page)."""
        from datetime import datetime
        conn = db()
        midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        return {"last_5h": SV.claude_usage(conn, time.time() - 5 * 3600), "today": SV.claude_usage(conn, midnight),
                "claude_code": (ST.reader(conn) or {}).get("kind") == "claude-code"}

    @router.get("/pipeline/{stage}/impact")
    def pipeline_impact(stage: str) -> dict:
        if stage not in ST.BY_STAGE:
            raise HTTPException(404, "no such step")
        return ST.impact(db(), stage)

    @router.post("/pipeline/{stage}", dependencies=[Depends(same_origin)])
    def pipeline_choose(stage: str, body: Choose) -> dict:
        """Switch a step's model. Only choices defined in octo/settings.py, and only if they can run here."""
        conn = db()
        try:
            before = ST.selected(conn, ST.BY_STAGE[stage]) if stage in ST.BY_STAGE else None
            impact = ST.impact(conn, stage) if stage in ST.BY_STAGE else None
            chosen = ST.choose(conn, stage, body.choice)
        except KeyError:
            raise HTTPException(404, "no model to choose for that step")
        except ValueError as e:
            raise HTTPException(422, str(e))
        if processor and (not before or before.id != chosen.id):
            processor.kick()
        return {"selected": chosen.id, "impact": impact, "steps": ST.describe(conn)}
