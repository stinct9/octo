"""Who's who in one conversation: confirm, reject or name a speaker (its voiceprint follows in the background), merge two,
move a line, undo a correction, leave someone out."""

from __future__ import annotations

import subprocess
import sys
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import ROOT
from .. import corrections as C
from .. import sessions as SV
from .. import voice as V
from ..db import row, tx
from ..pipeline import current
from .common import LETTER, Lab, NAME, same_origin, session_or_404


_PRINTS = threading.Lock()  # voiceprints from answers: one process (and one voice model) at a time


class SpeakerAction(BaseModel):
    action: str  # confirm | reject | name
    name: str | None = None
    invitee: str | None = Field(None, max_length=300)  # B23: named from this calendar invitee: the match is kept


class Merge(BaseModel):
    into: str  # the letter that stays


class MoveLine(BaseModel):
    to: str


class Exclude(BaseModel):
    letter: str
    left_out: bool


def add(router: APIRouter, lab: Lab) -> None:
    db, processor = lab.db, lab.processor

    def _voiceprint(session_id: str, letter: str, name: str) -> None:
        # The same command as the terminal, in its own process, so the speaker model's memory isn't held
        # by the server afterwards. It keeps the print only if the answer still stands when it's done.
        with _PRINTS:
            r = subprocess.run([sys.executable, "-m", "octo.cli", "label", session_id, letter, name, "--print-only"],
                               cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"voiceprint for {session_id} {letter} failed: "
                  f"{(r.stderr.strip().splitlines() or ['no output'])[-1]}", file=sys.stderr)
        processor.kick()  # a new print: re-identify the recent conversations once they settle

    @router.post("/sessions/{session_id}/speakers/{letter}", dependencies=[Depends(same_origin)])
    def speaker_action(session_id: str, letter: str, body: SpeakerAction) -> dict:
        conn = db()
        session_or_404(conn, session_id)
        sp = next((x for x in SV.speakers(conn, session_id) if x["letter"] == letter), None) if LETTER.match(letter) else None
        if not sp:
            raise HTTPException(404, "no such speaker")
        def label(name: str) -> None:
            # The answer is saved now. Their voiceprint (D1) takes 5-10 s (the voice model loads each
            # time), so it follows in the background; until 9 Oct the answer waited for it, and a
            # refresh in those seconds asked the same question again.
            dz = current(conn, session_id, "diarize")
            if not dz or dz["status"] != "done":
                raise HTTPException(409, "speakers aren't separated yet")
            with tx(conn):
                person = V.ensure_person(conn, name)
                V.drop_label(conn, session_id, letter)  # relabelling replaces, never adds a second voice
                conn.execute("INSERT OR REPLACE INTO session_speakers (session_id, speaker, person_id, state, similarity, "
                             "diarize_run) VALUES (?, ?, ?, 'confirmed', NULL, ?)", (session_id, letter, person["id"], dz["id"]))
            threading.Thread(target=_voiceprint, args=(session_id, letter, person["name"]), daemon=True,
                             name="octo-voiceprint").start()

        if body.action == "confirm":
            if sp["state"] not in ("suggested", "auto"):
                raise HTTPException(409, "nothing to confirm: name them instead")
            # Confirming adds a voiceprint from this conversation too: that's how a person's prints
            # come to cover them loud, quiet, near and across the room.
            who = row(conn, "SELECT p.name FROM session_speakers ss JOIN people p ON p.id = ss.person_id "
                            "WHERE ss.session_id=? AND ss.speaker=?", session_id, letter)
            label(who["name"])
        elif body.action == "reject":
            V.drop_label(conn, session_id, letter)
            conn.execute("UPDATE session_speakers SET state='rejected', person_id=NULL, similarity=NULL "
                         "WHERE session_id=? AND speaker=?", (session_id, letter))
        elif body.action == "name":
            name = (body.name or "").strip()
            if not NAME.match(name):
                raise HTTPException(422, "a name: letters first, up to 40 characters")
            label(name)
            from .. import events as EV
            cal = EV.for_session(conn, session_or_404(conn, session_id)) if body.invitee else None
            if cal and any(p["key"] == body.invitee for p in cal["invitees"]):  # only one this conversation offered
                EV.keep_match(conn, body.invitee, V.person_by_name(conn, name)["id"])
        else:
            raise HTTPException(422, "action must be confirm, reject or name")
        C.merge_same_person(conn, session_id)  # reconciled on every change, a reject included (M22)
        processor.kick()
        return {"speakers": SV.speakers(conn, session_id), "todo": SV.todo(conn, session_id)}

    def _fixed(conn, session_id: str) -> dict:
        processor.kick()  # numbers and insights re-run on the corrected speakers
        sp = SV.speakers(conn, session_id)
        _, out, fixes = SV.diarized(conn, session_id)
        return {"speakers": sp, "todo": SV.todo(conn, session_id), "lines": out["lines"], "turns": out["turns"],
                "corrections": sum(1 for f in fixes if f["kind"] != "exclude"),
                "left_out": sorted({f["letter"] for f in fixes if f["kind"] == "exclude"})}

    @router.post("/sessions/{session_id}/speakers/{letter}/merge", dependencies=[Depends(same_origin)])
    def merge(session_id: str, letter: str, body: Merge) -> dict:
        conn = db()
        session_or_404(conn, session_id)
        dz, out, _ = SV.diarized(conn, session_id)
        if not dz or letter == body.into or not {letter, body.into} <= set(out["speakers"]):
            raise HTTPException(404, "no such speakers to merge")
        C.add(conn, session_id, dz["id"], "merge", {"from": letter, "into": body.into})
        # Two different confirmed people merged: the owner is saying they're one. The merged-away letter's
        # label and voiceprint would otherwise keep teaching the wrong person's voice (M32).
        a, b = (row(conn, "SELECT person_id FROM session_speakers WHERE session_id=? AND speaker=? AND state='confirmed' "
                          "AND diarize_run=?", session_id, x, dz["id"]) for x in (letter, body.into))
        if a and b and a["person_id"] != b["person_id"]:
            V.drop_label(conn, session_id, letter)
            conn.execute("UPDATE session_speakers SET person_id=? WHERE session_id=? AND speaker=?",
                         (b["person_id"], session_id, letter))
        return _fixed(conn, session_id)

    @router.post("/sessions/{session_id}/lines/{line_id}", dependencies=[Depends(same_origin)])
    def move_line(session_id: str, line_id: int, body: MoveLine) -> dict:
        conn = db()
        session_or_404(conn, session_id)
        dz, out, _ = SV.diarized(conn, session_id)
        line = next((ln for ln in out["lines"] if ln["id"] == line_id), None) if out else None
        if not line or body.to not in out["speakers"]:
            raise HTTPException(404, "no such line or speaker")
        if line["speaker"] != body.to:
            C.add(conn, session_id, dz["id"], "line", {"line": line_id, "to": body.to})
        return _fixed(conn, session_id)

    @router.post("/sessions/{session_id}/corrections/undo", dependencies=[Depends(same_origin)])
    def undo(session_id: str) -> dict:
        conn = db()
        session_or_404(conn, session_id)
        dz, _, _ = SV.diarized(conn, session_id)
        if not dz or not C.undo_last(conn, session_id, dz["id"]):
            raise HTTPException(404, "nothing to undo")
        return _fixed(conn, session_id)

    @router.post("/sessions/{session_id}/exclude", dependencies=[Depends(same_origin)])
    def exclude(session_id: str, body: Exclude) -> dict:
        conn = db()
        session_or_404(conn, session_id)
        dz, out, fixes = SV.diarized(conn, session_id)
        sp = next((x for x in SV.speakers(conn, session_id) if x["letter"] == body.letter), None)
        if not dz or not sp:
            raise HTTPException(404, "no such speaker")
        if sp["self"]:
            raise HTTPException(409, "the insights are about you: you can't be left out")
        for f in fixes:
            if f["kind"] == "exclude" and f["letter"] == body.letter:
                conn.execute("DELETE FROM corrections WHERE id=?", (f["id"],))
        if body.left_out:
            C.add(conn, session_id, dz["id"], "exclude", {"letter": body.letter})
        return _fixed(conn, session_id)
