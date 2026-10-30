"""People: everyone with a voiceprint, one person's page, rename or merge, forget, their consent, the intention for next
time, and "Octo hasn't heard from…"."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import corrections as C
from .. import voice as V
from ..db import load_json, row, rows
from ..pipeline import current
from .common import Lab, NAME, OwnerWords, same_origin


class ConsentBody(BaseModel):  # how someone agreed to be recognised by voice (product plan 4.6)
    how: str = "asked in person"
    note: str = ""


class Rename(BaseModel):
    name: str
    to: str
    merge: bool = False  # `to` is someone else already: they're the same person


class Forget(BaseModel):
    name: str
    confirm: str


class QuietMute(BaseModel):
    muted: bool


def add(router: APIRouter, lab: Lab) -> None:
    db, processor = lab.db, lab.processor

    @router.post("/people/rename", dependencies=[Depends(same_origin)])
    def rename(body: Rename) -> dict:
        conn = db()
        to = body.to.strip()
        if not NAME.match(to):
            raise HTTPException(422, "a name: letters first, up to 40 characters")
        p, other = V.person_by_name(conn, body.name), V.person_by_name(conn, to)
        if not p:
            raise HTTPException(404, "no such person")
        if other and other["id"] != p["id"] and not body.merge:
            raise HTTPException(409, f"{other['name']} already exists")
        done, left = V.rename(conn, p["name"], to)
        # Whoever is left (the owner, when one of the two was them): a conversation may now have them twice,
        # or a merge made under the old name may no longer hold (M22).
        for r in rows(conn, "SELECT DISTINCT session_id FROM session_speakers WHERE person_id=?", left["id"]):
            C.merge_same_person(conn, r["session_id"])
        processor.kick()  # names are in what reviews say: unrated ones are redone; rated ones offer a redo
        return {"done": done, "name": left["name"]}

    @router.post("/people/forget", dependencies=[Depends(same_origin)])
    def forget(body: Forget) -> dict:
        from ..voice import forget as forget_person, person_by_name

        conn = db()
        p = person_by_name(conn, body.name)
        if not p:
            raise HTTPException(404, "no such person")
        if body.confirm.strip().lower() != p["name"].lower():
            raise HTTPException(422, "type their name to confirm")
        if p["is_self"]:
            raise HTTPException(409, "that's you: re-enrol instead of forgetting")
        from ..parts import with_groups  # B35: the review of a whole discussion they were in names them too
        named = with_groups(conn, [r["session_id"] for r in rows(
            conn, "SELECT DISTINCT session_id FROM session_speakers WHERE person_id=?", p["id"])])
        forget_person(conn, p["name"])
        # Their name is in what those reviews say: redo them, rated or not (planning.pinned_review).
        for sid in named:
            if ins := current(conn, sid, "insight"):
                conn.execute("UPDATE sessions SET redo_review=? WHERE id=?", (ins["id"], sid))
        processor.kick()
        return {"forgotten": p["name"]}

    @router.get("/people/{person_id}/quiet")
    def person_quiet(person_id: str) -> dict:
        from .. import week as W
        conn = db()
        p = row(conn, "SELECT id, name, is_self FROM people WHERE id=?", person_id)
        if not p:
            raise HTTPException(404, "no such person")
        got = None if p["is_self"] else W.heard(conn, p["id"], p["name"])
        return got or {"person_id": p["id"], "name": p["name"], "usual_days": None, "since_days": None,
                       "muted": p["id"] in W.muted(conn), "text": None}

    @router.post("/people/{person_id}/quiet", dependencies=[Depends(same_origin)])
    def person_quiet_mute(person_id: str, body: QuietMute) -> dict:
        from .. import week as W
        conn = db()
        if not row(conn, "SELECT 1 FROM people WHERE id=? AND is_self=0", person_id):
            raise HTTPException(404, "no such person")
        W.mute(conn, person_id, body.muted)
        return person_quiet(person_id)

    @router.get("/people")
    def people() -> list[dict]:
        conn = db()
        out = []
        for p in rows(conn, "SELECT * FROM people ORDER BY is_self DESC, name"):
            prints = rows(conn, "SELECT seconds, source FROM voiceprints WHERE person_id=?", p["id"])
            heard = rows(conn, "SELECT DISTINCT ss.session_id, r.started_at + s.start_s AS at FROM session_speakers ss "
                               "JOIN sessions s ON s.id = ss.session_id JOIN recordings r ON r.id = s.recording_id "
                               "WHERE ss.person_id=? AND ss.state='confirmed' AND s.status='active'", p["id"])
            out.append({
                "id": p["id"], "name": p["name"], "self": bool(p["is_self"]),
                "consent": load_json(p["consent"]), "needs_consent": V.needs_consent(conn) and not p["is_self"],
                "voiceprints": len(prints), "seconds": round(sum(v["seconds"] for v in prints), 1),
                "enrolled": sum(1 for v in prints if v["source"].startswith("enrol:")),
                "conversations": len(heard),
                "last_heard": max((h["at"] for h in heard if h["at"]), default=None),
            })
        return out

    # Agreement to be recognised by voice (product plan 4.6). Recording it lets confirmations keep a
    # voiceprint from then on; withdrawing it deletes every voiceprint of theirs (their name stays).
    @router.post("/people/{person_id}/consent", dependencies=[Depends(same_origin)])
    def consent_record(person_id: str, body: ConsentBody) -> dict:
        conn = db()
        if not row(conn, "SELECT 1 FROM people WHERE id=?", person_id):
            raise HTTPException(404, "no such person")
        return {"consent": V.record_consent(conn, person_id, body.how, body.note)}

    @router.post("/people/{person_id}/consent/withdraw", dependencies=[Depends(same_origin)])
    def consent_withdraw(person_id: str) -> dict:
        conn = db()
        if not row(conn, "SELECT 1 FROM people WHERE id=?", person_id):
            raise HTTPException(404, "no such person")
        n = V.withdraw_consent(conn, person_id)
        if n:
            processor.kick()  # fewer voiceprints: the other conversations are matched again
        return {"voiceprints_deleted": n}

    @router.post("/people/{person_id}/intention", dependencies=[Depends(same_origin)])
    def intention_set(person_id: str, body: OwnerWords) -> dict:
        """B15: the next conversation where they're confirmed takes it, and its review reads it (octo/intention.py)."""
        from .. import intention as IN
        conn = db()
        if not row(conn, "SELECT 1 FROM people WHERE id=? AND is_self=0", person_id):
            raise HTTPException(404, "no such person")
        try:
            return {"intention": IN.set_open(conn, person_id, body.text)}
        except ValueError as e:
            raise HTTPException(422, str(e))

    @router.get("/people/{person_id}")
    def person(person_id: str) -> dict:
        """One person's page, for before you talk (octo/person.py): no model call."""
        from .. import person as PE
        got = PE.page(db(), person_id)
        if not got:
            raise HTTPException(404, "no such person")
        return got
