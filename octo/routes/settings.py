"""Settings (Mac only: the phone listener refuses /api/octo/settings): TickTick, models and keys, you and your principles,
the made-up conversation's review, a test notification. Tokens and keys go in, never out."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import labconfig as LC
from .. import settings as ST
from .common import Lab, same_origin


class ProviderAdd(BaseModel):  # a service the review can run on (octo/providers.py); the key goes in, never out
    kind: str
    label: str = ""
    api_key: str = ""
    base_url: str = ""
    models: list[str] = []


class ProviderUpdate(BaseModel):
    api_key: str | None = None
    models: list[str] | None = None
    daily_calls: int | None = None  # a budget a day on this connection; 0 for none (review 8 Oct, finding 15)


class ProviderTest(BaseModel):
    model: str | None = None


class OwnerBody(BaseModel):  # who the reviews and the coach are about (octo/owner.py)
    name: str = ""
    pronoun: str = "they"
    about: str = ""
    principles_from: str = ""
    never: str = ""


class RubricBody(BaseModel):  # the owner's principles and how the review applies them, as typed (octo/rubric.py)
    principles: str
    rules: str = ""


class Invite(BaseModel):
    name: str
    email: str


class TickTickToken(BaseModel):
    token: str


class TickTickList(BaseModel):
    list_id: str | None = None


def add(router: APIRouter, lab: Lab) -> None:
    db, processor = lab.db, lab.processor

    @router.get("/ticktick")
    def ticktick_status() -> dict:
        from .. import ticktick as TT
        return TT.status()

    # Settings (Mac only: the phone listener refuses /api/octo/settings). The token goes in, never out.
    @router.get("/settings/ticktick")
    def ticktick_settings() -> dict:
        from .. import ticktick as TT
        return TT.settings()

    @router.post("/settings/ticktick", dependencies=[Depends(same_origin)])
    def ticktick_connect(body: TickTickToken) -> dict:
        from .. import ticktick as TT
        try:
            return TT.connect(body.token)
        except TT.TickTickError as e:
            raise HTTPException(400, str(e))

    @router.post("/settings/ticktick/list", dependencies=[Depends(same_origin)])
    def ticktick_list(body: TickTickList) -> dict:
        from .. import ticktick as TT
        try:
            return TT.choose_list(body.list_id)
        except TT.TickTickError as e:
            raise HTTPException(400, str(e))

    @router.post("/settings/ticktick/disconnect", dependencies=[Depends(same_origin)])
    def ticktick_disconnect() -> dict:
        from .. import ticktick as TT
        TT.disconnect()
        return TT.settings()

    # Models and keys (product plan 4.1, Mac only like the rest of /settings): the services the review,
    # the Hindi merge and the coach can run on. Keys go in, never out: the page sees their last four.
    def providers_view() -> dict:
        from .. import claude_code as CC
        from .. import labconfig
        from .. import providers as PV
        conn = db()
        sel = ST.selected(conn, ST.BY_STAGE["insight"])
        # Claude Code on this Mac, apart from the keys (31 Oct): found, version, logged in, and what fixes each.
        return {"connections": PV.listing(), "claude_code": {**CC.status(), "in_use": bool(sel and sel.claude)},
                "claude_models": [{"id": k, "label": v["label"], "price": v["price"], "why": v["why"]}
                                  for k, v in PV.CLAUDE_MODELS.items() if k in PV.OFFERED],
                "review": sel.id if sel else None, "blocked": ST.review_blocked(conn),
                "first_lab": labconfig.origin(conn) == labconfig.FIRST}  # the page's lead (review 5 Nov, item 1)

    @router.get("/settings/providers")
    def providers_list() -> dict:
        return providers_view()

    @router.post("/settings/providers", dependencies=[Depends(same_origin)])
    def providers_add(body: ProviderAdd) -> dict:
        from .. import providers as PV
        try:
            added = PV.add(body.kind, body.label, body.api_key, body.base_url, body.models)
        except PV.ProviderError as e:
            raise HTTPException(400, str(e))
        # Tested before it's kept (onboarding review, 28 Oct): a wrong key or an empty account is said now, not at
        # the first review. A passing problem (rate limit, offline) keeps it and says so.
        try:
            tested = PV.test(added["id"])
        except PV.ProviderError as e:
            if PV.TRANSIENT not in str(e):
                PV.remove(added["id"])
                raise HTTPException(400, f"Not connected: {e}")
            tested = {"ok": False, "error": str(e)}
        return {"added": added, "tested": tested, **providers_view()}

    @router.post("/settings/providers/{conn_id}", dependencies=[Depends(same_origin)])
    def providers_update(conn_id: str, body: ProviderUpdate) -> dict:
        from .. import providers as PV
        try:
            PV.update(conn_id, body.api_key, body.models, body.daily_calls)
        except KeyError:
            raise HTTPException(404, "no such service")
        except PV.ProviderError as e:
            raise HTTPException(400, str(e))
        return providers_view()

    @router.post("/settings/providers/{conn_id}/remove", dependencies=[Depends(same_origin)])
    def providers_remove(conn_id: str) -> dict:
        from .. import providers as PV
        PV.remove(conn_id)
        return providers_view()

    # Set up Octo (product plan 4.2, Mac only). Saving either makes the reviews that read it
    # again, except the ones the owner answered (planning.pinned_review): those keep "Redo the review".
    @router.get("/settings/owner")
    def owner_get() -> dict:
        from .. import owner as O
        return {"profile": O.load(db()), "pronouns": list(O.PRONOUNS)}

    @router.post("/settings/owner", dependencies=[Depends(same_origin)])
    def owner_set(body: OwnerBody) -> dict:
        from .. import owner as O
        try:
            profile = O.save(db(), body.model_dump())
        except ValueError as e:
            raise HTTPException(422, str(e))
        if processor:
            processor.kick()
        return {"profile": profile, "pronouns": list(O.PRONOUNS)}

    # Invite a tester (octo/invite.py): only in a lab whose owner kept a sending account with `octo invite setup`.
    @router.get("/settings/invite")
    def invite_status() -> dict:
        from .. import invite as IV
        st = IV.status()
        if not st["ready"]:
            raise HTTPException(404, "not set up")
        return st

    @router.post("/settings/invite", dependencies=[Depends(same_origin)])
    def invite_send(body: Invite) -> dict:
        from .. import invite as IV
        from .. import owner as O
        if not IV.status()["ready"]:
            raise HTTPException(404, "not set up")
        try:
            return IV.send(body.name, body.email, signed=O.load(db()).get("name", ""))
        except IV.InviteError as e:
            raise HTTPException(400, str(e)) from None

    @router.get("/settings/rubric")
    def rubric_get() -> dict:
        from .. import rubric as RB
        out = RB.sections()
        if LC.origin(db()) != LC.FIRST:  # 31 Oct: a new lab may start from two examples; the first lab never sees them
            out["examples"] = [{"text": t, "why": w} for t, w in RB.EXAMPLES]
        return out

    @router.post("/settings/rubric/preview", dependencies=[Depends(same_origin)])
    def rubric_preview(body: RubricBody) -> list[dict]:
        from .. import rubric as RB
        return RB.preview(body.principles)

    @router.post("/settings/rubric", dependencies=[Depends(same_origin)])
    def rubric_set(body: RubricBody) -> dict:
        from .. import rubric as RB
        out = RB.save(body.principles, body.rules)
        if processor:
            processor.kick()
        return out

    @router.get("/settings/sample")
    def sample_last() -> dict:
        """The last review of the made-up conversation (octo/sample.py), or nothing."""
        from .. import sample as SM
        return SM.last() or {}

    @router.post("/settings/sample", dependencies=[Depends(same_origin)])
    def sample_review() -> dict:
        """Review the made-up conversation with the owner's model and principles: one call, on their key."""
        from .. import providers as PV
        from .. import sample as SM
        try:
            return SM.run(db())
        except (ValueError, PV.ProviderError, OSError) as e:  # OSError: the model's program missing (Claude Code)
            raise HTTPException(400, str(e))

    @router.post("/settings/notify-test", dependencies=[Depends(same_origin)])
    def notify_test() -> dict:
        from .. import notify as N
        via = N.post("Octo can tell you when something's waiting · counts only, never what was said")
        return {"ok": True, "via": via}

    @router.post("/settings/providers/{conn_id}/test", dependencies=[Depends(same_origin)])
    def providers_test(conn_id: str, body: ProviderTest) -> dict:
        from .. import providers as PV
        try:
            return PV.test(conn_id, body.model)
        except KeyError:
            raise HTTPException(404, "no such service")
        except PV.ProviderError as e:
            raise HTTPException(400, str(e))
