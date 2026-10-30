"""The Mac's key (octo/mackey.py, review 8 Oct finding 4): the Mac's socket answers /api only to a browser let in."""

import os
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import mackey as MK
from octo import phone as PH
from octo import phone_api as PA
from octo.api import make_router
from octo.db import connect


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(PA, "REQUIRE_MAC_KEY", True)
    monkeypatch.setattr(MK, "FILE", tmp_path / "mac.key")
    monkeypatch.setattr(PH, "DIR", tmp_path / "phone")
    db = lambda: connect(tmp_path / "t.db")
    app = FastAPI()
    app.include_router(make_router(db, processor=type("P", (), {"kick": lambda self: None})()))
    PA.require_mac_key(app)
    PA.install(app, db)

    @app.get("/api/status")  # stands in for a status route the page asks on every visit
    def status() -> dict:
        return {}

    @app.get("/")  # stands in for the page itself
    def page() -> dict:
        return {"page": True}

    app.state.db = db
    return app


def test_without_the_key_the_api_says_how_and_the_page_still_loads(app):
    mac = TestClient(app)
    r = mac.get("/api/octo/sessions")
    assert r.status_code == 401 and r.json()["let_in"] and "octo open" in r.json()["detail"]
    assert isinstance(r.json()["app"], bool)  # the Octo app is offered first when it exists (octo/launcher.py)
    assert mac.post("/api/octo/people/forget", json={"name": "A", "confirm": "a"}).status_code == 401
    assert mac.get("/").status_code == 200  # the page shell has no data; it shows how to let the browser in


def test_the_key_is_private_and_made_once(app):
    key = MK.key()
    assert len(key) >= 40 and oct(os.stat(MK.FILE).st_mode & 0o777) == "0o600"
    assert MK.key() == key


def test_let_in_sets_the_cookie_and_goes_on_to_the_page(app):
    mac = TestClient(app)
    assert mac.get("/let-in", params={"k": "wrong"}, follow_redirects=False).status_code == 403
    r = mac.get("/let-in", params={"k": MK.key(), "to": "/#/phone"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/#/phone"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{MK.COOKIE}=") and "HttpOnly" in cookie and "samesite=strict" in cookie.lower()
    assert "Secure" not in cookie  # plain HTTP on 127.0.0.1: a Secure cookie would never come back
    assert mac.get("/api/octo/sessions").status_code == 200
    # Each visit renews it, so a browser used within 400 days stays in.
    assert MK.COOKIE in mac.get("/api/status").headers.get("set-cookie", "")


def test_let_in_never_sends_the_browser_to_another_site(app):
    mac = TestClient(app)
    for to in ("//evil.example/x", "https://evil.example", "/\\evil.example", "javascript:alert(1)"):
        r = mac.get("/let-in", params={"k": MK.key(), "to": to}, follow_redirects=False)
        assert r.headers["location"] == "/", to


def test_a_phone_uses_its_own_pairing_and_is_never_let_in_as_the_mac(app):
    token = PH.add_device(app.state.db(), "iPhone")
    phone = TestClient(app, base_url=f"https://192.168.1.20:{PH.PORT}", headers={"Cookie": f"{PH.COOKIE}={token}"})
    assert phone.get("/api/octo/sessions").status_code == 200  # no Mac key needed on the phone's socket
    assert phone.get("/let-in", params={"k": MK.key()}, follow_redirects=False).status_code == 404
    # The Mac's key on a phone socket opens nothing the phone couldn't already reach.
    r = phone.get("/api/octo/settings/providers", headers={"Cookie": f"{PH.COOKIE}={token}; {MK.COOKIE}={MK.key()}"})
    assert r.status_code == 403


def test_two_processes_making_the_key_at_once_agree(tmp_path):
    path = tmp_path / "k" / "mac.key"
    got = []
    threads = [threading.Thread(target=lambda: got.append(MK.key(path))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(got)) == 1 and path.read_text().strip() == got[0]
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]


def test_tools_on_this_mac_send_the_key(app):
    assert MK.cookie_header() == f"{MK.COOKIE}={MK.key()}"
    mac = TestClient(app, headers={"Cookie": MK.cookie_header()})
    assert mac.get("/api/octo/sessions").status_code == 200


def test_without_the_phone_gate_another_host_name_still_needs_the_key(tmp_path, monkeypatch):
    """Security review L1 (28 Oct): the demo lab has no phone gate, and asked as evil.example it answered without the key."""
    monkeypatch.setattr(PA, "REQUIRE_MAC_KEY", True)
    monkeypatch.setattr(MK, "FILE", tmp_path / "mac.key")
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    PA.require_mac_key(app)
    c = TestClient(app, base_url="http://evil.example:8813")
    assert c.get("/api/octo/sessions").status_code == 401
    c.cookies.set(MK.COOKIE, MK.key())
    assert c.get("/api/octo/sessions").status_code == 200
