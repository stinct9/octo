"""What a paired phone may call (octo/phone_api.py PHONE_ROUTES), at home and away.

REVIEWED below is the decision, written twice on purpose: a route added to the gate's list, a route the gate would
open by accident (a Mac route that one of its templates also matches), or a listed route that no longer exists,
fails here until someone decides. The product review's §2.3 ("what belongs where") is the reasoning.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import bench.api as api_mod
from bench.runner import Runner
from bench.store import RunStore
from octo import phone as PH
from octo import phone_api as PA
from octo.api import make_router
from octo.db import connect

A, H = PA.ANYWHERE, PA.HOME
REVIEWED = {
    ("GET", "/api/octo/sessions"): A,
    ("GET", "/api/octo/sessions/changed"): A,
    ("GET", "/api/octo/search"): A,
    ("GET", "/api/octo/ask"): A,  # B24: answers read from the transcripts, as the Lab tab's pages are
    ("GET", "/api/octo/ask/{ask_id}"): A,
    ("GET", "/api/octo/sessions/{session_id}"): A,
    ("GET", "/api/octo/sessions/{session_id}/audio"): A,
    ("GET", "/api/octo/sessions/{session_id}/ticktick/done"): A,
    ("GET", "/api/octo/actions"): A,
    ("GET", "/api/octo/commitments"): A, ("POST", "/api/octo/commitments/{commitment_id}"): A,  # B14
    ("POST", "/api/octo/commitments/{commitment_id}/ticktick"): A,
    ("GET", "/api/octo/commitments/check"): A, ("POST", "/api/octo/commitments/{commitment_id}/check"): A,  # the owner's check
    ("GET", "/api/octo/actions/ticktick"): A,
    ("GET", "/api/octo/ticktick"): A,
    ("GET", "/api/octo/focus"): A,
    ("GET", "/api/octo/fillers"): A,
    ("GET", "/api/octo/fillers/live"): A,  # B21: read only; the Mac's switch stays on the Mac
    ("GET", "/api/octo/coach"): A,
    ("GET", "/api/octo/coach/{note_id}"): A,
    ("GET", "/api/octo/progress"): A,
    ("GET", "/api/octo/progress/states"): A,
    ("GET", "/api/octo/incoming"): A,
    ("GET", "/api/octo/aside"): A,
    ("GET", "/api/octo/questions"): A,
    ("GET", "/api/octo/questions/check"): A,
    ("GET", "/api/octo/status"): A,
    ("GET", "/api/octo/device"): A,
    ("GET", "/api/octo/people"): A,
    ("GET", "/api/octo/people/{person_id}"): A,
    ("GET", "/api/octo/people/{person_id}/quiet"): A,
    ("GET", "/api/octo/week"): A,
    ("GET", "/api/octo/agenda"): A,
    ("GET", "/api/octo/phone/hello"): A,
    ("POST", "/api/octo/sessions/{session_id}/answer"): A,
    ("POST", "/api/octo/sessions/{session_id}/feedback"): A,
    ("POST", "/api/octo/actions/let-go"): A,
    ("POST", "/api/octo/sessions/{session_id}/focus"): A,
    ("POST", "/api/octo/sessions/{session_id}/reflection"): A, ("POST", "/api/octo/people/{person_id}/intention"): A,  # B15
    ("POST", "/api/octo/marks"): A,  # B16
    ("POST", "/api/octo/marks/{mark_id}/delete"): A,  # its Undo (UX decisions, 22 Oct): the phone's own, within a minute
    ("POST", "/api/octo/sessions/{session_id}/speakers/{letter}"): A,
    ("POST", "/api/octo/sessions/{session_id}/ticktick"): A,
    ("POST", "/api/octo/sessions/{session_id}/keep"): A,
    ("POST", "/api/octo/sessions/{session_id}/aside"): A,
    ("POST", "/api/octo/sessions/{session_id}/retry"): A,
    ("POST", "/api/octo/sessions/{session_id}/provisional"): A,
    ("POST", "/api/octo/sessions/{session_id}/redo-review"): A,
    ("POST", "/api/octo/questions/check"): A,
    ("POST", "/api/octo/fillers/words"): A,
    ("POST", "/api/octo/delivery/hedges"): A,
    ("POST", "/api/octo/focus"): A,
    ("POST", "/api/octo/focus/stop"): A,
    ("PUT", "/api/octo/focus/{focus_id}/solved"): A,  # B38: the owner's words, like the focus
    ("POST", "/api/octo/coach"): A,
    ("POST", "/api/octo/ask"): A,  # B24: as the coach, a call on the Mac within the day's 60
    ("POST", "/api/octo/ask/{ask_id}/keep"): A,
    ("POST", "/api/octo/ask/{ask_id}/delete"): A,  # an answer, not a conversation: away from home too
    ("GET", "/api/octo/calendar/month"): A, ("GET", "/api/octo/calendar/day"): A,  # B37: from above, read only
    # B36: a plan before a conversation, made on the Mac as Ask's; deleting a plan, never a conversation
    ("GET", "/api/octo/people/{person_id}/prepare"): A, ("GET", "/api/octo/people/{person_id}/prepare/pick"): A,
    ("POST", "/api/octo/people/{person_id}/prepare"): A, ("POST", "/api/octo/people/{person_id}/prepare/delete"): A,
    ("POST", "/api/octo/people/prepare"): A,  # someone new, by name, to prepare for
    ("POST", "/api/octo/coach/{note_id}/feedback"): A,
    ("PUT", "/api/octo/coach/{note_id}/solved"): A,
    ("POST", "/api/octo/coach/{note_id}/focus"): A,
    ("POST", "/api/octo/coach/{note_id}/seen"): A,
    ("POST", "/api/octo/people/{person_id}/consent"): A,
    ("POST", "/api/octo/people/{person_id}/quiet"): A,
    ("POST", "/api/octo/week/dismiss"): A,
    ("GET", "/api/octo/drills"): A,
    ("GET", "/api/octo/drills/{drill_id}"): A,
    ("GET", "/api/octo/drills/{drill_id}/audio"): A,
    ("POST", "/api/octo/drills"): A,
    ("POST", "/api/octo/drills/prompts"): A,
    ("POST", "/api/octo/drills/{drill_id}/delete"): H,
    # B26: rehearsals read and played anywhere (the calls run on the Mac, in the day's 60); deleting from home only
    ("GET", "/api/octo/rehearsals"): A, ("GET", "/api/octo/rehearsals/{rehearsal_id}"): A,
    ("POST", "/api/octo/rehearsals"): A, ("POST", "/api/octo/rehearsals/{rehearsal_id}/say"): A,
    ("POST", "/api/octo/rehearsals/{rehearsal_id}/stop"): A,
    ("POST", "/api/octo/rehearsals/{rehearsal_id}/delete"): H,
    # B31: talking a conversation through anywhere (each reply runs on the Mac, in the day's 60); Start over from home only
    ("GET", "/api/octo/sessions/{session_id}/talk"): A, ("POST", "/api/octo/sessions/{session_id}/talk"): A,
    ("POST", "/api/octo/sessions/{session_id}/talk/clear"): H,
    ("POST", "/api/octo/recordings"): A,
    ("POST", "/api/octo/device/recording"): A,
    ("GET", "/api/octo/prefs"): A, ("PUT", "/api/octo/prefs/{key}"): A,  # folds and views: the page's look only (4 Nov)
    ("POST", "/api/octo/sessions/{session_id}/delete"): H,
    ("POST", "/api/octo/people/forget"): H,
    ("POST", "/api/octo/people/rename"): H,
    ("POST", "/api/octo/people/{person_id}/consent/withdraw"): H,
}


def test_the_gate_holds_the_reviewed_list():
    assert PA.PHONE_ROUTES == REVIEWED


@pytest.fixture
def routes(tmp_path):
    """Every API route the real app has, as (method, path template), from its OpenAPI description."""
    app = api_mod.create_app(Runner(store=RunStore(tmp_path)), steps={}, run_all=[], poll_device=False)
    return {(m.upper(), path) for path, ops in app.openapi()["paths"].items() for m in ops if path.startswith("/api/")}


def test_every_listed_route_exists(routes):
    assert set(REVIEWED) - routes == set()


def test_the_gate_answers_each_route_as_reviewed(routes):
    """A sample request for every route gets exactly its reviewed answer: a Mac route that a phone template would
    also match (say a new GET /api/octo/coach/history beside /coach/{note_id}) fails here."""
    def sample(path: str) -> str:
        import re
        return re.sub(r"\{[^}]+\}", "x1", path)
    wrong = {(m, p): PA.phone_rule(m, sample(p)) for m, p in routes if PA.phone_rule(m, sample(p)) != REVIEWED.get((m, p))}
    assert wrong == {}
    assert len(routes) > len(REVIEWED) + 30  # the Mac's own routes are most of them


@pytest.fixture
def phones(tmp_path, monkeypatch):
    """A paired phone on the home Wi-Fi socket and the same phone through Tailscale."""
    monkeypatch.setattr(PH, "DIR", tmp_path / "phone")
    db = lambda: connect(tmp_path / "t.db")
    app = FastAPI()
    app.include_router(make_router(db, processor=type("P", (), {"kick": lambda self: None})()))
    PA.install(app, db)
    token = PH.add_device(db(), "iPhone")
    cookie = {"Cookie": f"{PH.COOKIE}={token}"}
    home = TestClient(app, base_url=f"https://192.168.1.20:{PH.PORT}", headers=cookie)
    away = TestClient(app, base_url=f"https://100.101.102.103:{PH.PORT}", headers=cookie)
    return home, away


def test_reading_works_at_home_and_away(phones):
    for phone in phones:
        assert phone.get("/api/octo/sessions").status_code == 200
        assert phone.get("/api/octo/people").status_code == 200


def test_deleting_is_at_home_only(phones):
    home, away = phones
    assert home.post("/api/octo/sessions/ses_x/delete", json={"confirm": "delete"}).status_code == 404  # past the gate
    r = away.post("/api/octo/sessions/ses_x/delete", json={"confirm": "delete"})
    assert r.status_code == 403 and r.json()["at_home_only"]
    assert away.post("/api/octo/people/forget", json={"name": "Asha", "confirm": "asha"}).status_code == 403
    # A proxy on the home socket isn't home (a forwarded request could have come from anywhere).
    assert home.post("/api/octo/sessions/ses_x/delete", json={"confirm": "delete"},
                     headers={"X-Forwarded-For": "8.8.8.8"}).status_code == 403


def test_precision_work_settings_and_the_bench_are_the_macs(phones):
    for phone in phones:
        for path in ("/api/octo/sessions/ses_x/split", "/api/octo/sessions/ses_x/join-back", "/api/octo/sessions/ses_x/group",
                     "/api/octo/sessions/ses_x/ungroup", "/api/octo/sessions/ses_x/speakers/A/merge",
                     "/api/octo/sessions/ses_x/lines/3", "/api/octo/sessions/ses_x/corrections/undo",
                     "/api/octo/sessions/ses_x/exclude", "/api/octo/sessions/ses_x/delete-range",
                     "/api/octo/fillers/check", "/api/octo/settings/owner", "/api/octo/pipeline/insight"):
            r = phone.post(path, json={})
            assert r.status_code == 403 and "Mac only" in r.json()["detail"], path
        assert phone.get("/api/octo/settings/providers").status_code == 403
        assert phone.get("/api/octo/beta").status_code == 403
        assert phone.get("/api/octo/nothing-here").status_code == 403  # unknown: the Mac's, not a 404 that maps routes
