"""What every route module shares (octo/routes/): the id checks, the write guards, the upload reader,
`session_or_404`, and `Lab`, what make_router hands each module: the database, the processor, the threads."""

from __future__ import annotations

import re
import threading
from types import SimpleNamespace

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..db import row

SESSION_ID = re.compile(r"^ses_[0-9]{8}_[0-9a-f]{8}$")
COMMITMENT_ID = re.compile(r"^com_[0-9]{8}_[0-9a-f]{8}$")
LETTER = re.compile(r"^[A-Z]{1,2}$")
AUDIO_TYPES = {".m4a", ".mp3", ".wav", ".aac", ".mp4", ".caf", ".aif", ".aiff", ".flac", ".ogg", ".opus", ".3gp"}
SOURCES = {"phone", "file", "device", "bench", "call"}  # call: the Octo app's two channels (B19)
NAME = re.compile(r"^[^\W\d_][\w .'-]{0,39}$")  # a person's name: letters first, 40 characters at most

# Transcripts and audio aren't kept in the browser's cache, so a Forget or Delete isn't undone by a
# cached copy (review 4 Oct, M15). Set on every /api/octo response, on both listeners.
NO_STORE = {"Cache-Control": "no-store"}
_TICKTICK = threading.Lock()  # sending a task: check, send, record, one at a time


class RehearsalLine(BaseModel):
    text: str = Field(default="", max_length=2000)
    threshold: float | None = Field(None, ge=0.5, lt=1.0)


class DeleteSession(BaseModel):
    confirm: str  # must be "delete": one stray click can't do this
    work: bool = False


class FillerWord(BaseModel):
    phrase: str
    on: bool = True
    source: str = "typed"  # typed | suggested


class Solved(BaseModel):
    """B38: who has solved this, in the owner's words; who and what both empty clears it."""
    who: str = ""
    what: str = ""
    source: str | None = None


class OwnerWords(BaseModel):
    text: str | None = None


def _origin_ok(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin.split("://", 1)[-1] != request.headers.get("host"):
        raise HTTPException(403, "not from this app")


def upload_origin(request: Request) -> None:
    """Uploads can't be JSON, so they need a custom header instead: a cross-site page can only
    send one after a CORS preflight, which this server never answers."""
    if request.headers.get("x-octo-upload") != "1":
        raise HTTPException(403, "uploads come from this app only")
    _origin_ok(request)


def same_origin(request: Request) -> None:
    """Writes only from this app's own pages: JSON body (a cross-site form can't send one without
    a CORS preflight, which this server never answers) and, when present, a matching Origin."""
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(415, "JSON only")
    _origin_ok(request)


def no_store(response: Response) -> None:
    response.headers.update(NO_STORE)  # JSON answers; file answers set it themselves


def session_or_404(conn, session_id: str, aside_ok: bool = False) -> dict:
    """An active conversation; with `aside_ok`, one set aside as not a conversation too (B5): it can
    still be opened, heard, put back and deleted, and nothing else."""
    if not SESSION_ID.match(session_id):
        raise HTTPException(404, "no such conversation")
    sess = row(conn, "SELECT s.*, r.source, r.original, r.started_at, r.path, r.audio_deleted_at FROM sessions s "
                     "JOIN recordings r ON r.id = s.recording_id WHERE s.id=? AND s.status IN ('active', ?)",
               session_id, "set_aside" if aside_ok else "active")
    if not sess:
        raise HTTPException(404, "no such conversation")
    return sess


class Lab(SimpleNamespace):
    """What make_router made for this router, read by the route modules' `add`: `db`, `processor`, `syncer`,
    `watchdog`, `fetcher`, `coach`, `asker`, `talker`."""
