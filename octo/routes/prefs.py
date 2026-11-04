"""The owner's choices on the pages (4 Nov): which parts are folded, the calendar's view. Kept in the lab (app_config
`ui_prefs`), not each browser's storage, so the Octo app's window, a browser on the Mac and the phone's Lab tab agree.
A choice is the owner's, so the last one saved wins."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, StringConstraints

from ..db import config
from .common import Lab, same_origin

MAX_PREFS = 200  # far more than the pages have; a page can't fill the table


class Pref(BaseModel):
    value: bool | Annotated[str, StringConstraints(max_length=64)]


def add(router: APIRouter, lab: Lab) -> None:
    db = lab.db

    @router.get("/prefs")
    def prefs() -> dict:
        return config(db(), "ui_prefs", {})

    @router.put("/prefs/{key}", dependencies=[Depends(same_origin)])
    def set_pref(body: Pref, key: str = Path(pattern=r"^[a-z0-9:._-]{1,64}$")) -> dict:
        conn = db()
        if key not in (now := config(conn, "ui_prefs", {})) and len(now) >= MAX_PREFS:
            raise HTTPException(422, "too many preferences")
        # One statement, so two pages saving at once each keep theirs: the key is checked above, safe in the JSON path.
        conn.execute("INSERT INTO app_config (key, value, updated_at) VALUES ('ui_prefs', json_object(?, json(?)), "
                     "strftime('%s','now')) ON CONFLICT (key) DO UPDATE SET value=json_set(value, ?, json(?)), "
                     "updated_at=excluded.updated_at",
                     (key, json.dumps(body.value), f'$."{key}"', json.dumps(body.value)))
        return config(conn, "ui_prefs", {})

