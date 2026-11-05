"""The Mac's key (octo/mackey.py, review 8 Oct finding 4): the Mac's socket answers /api only to a browser let in."""

import os
import threading
import time
from pathlib import Path

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
    assert mac.get("/let-in", params={"c": "wrong"}, follow_redirects=False).status_code == 403
    r = mac.get("/let-in", params={"c": MK.let_in_code(), "to": "/#/phone"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/#/phone"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{MK.COOKIE}=") and "HttpOnly" in cookie and "samesite=strict" in cookie.lower()
    assert "Secure" not in cookie  # plain HTTP on 127.0.0.1: a Secure cookie would never come back
    assert mac.get("/api/octo/sessions").status_code == 200
    # Each visit renews it, so a browser used within 400 days stays in.
    assert MK.COOKIE in mac.get("/api/status").headers.get("set-cookie", "")


def test_the_let_in_link_never_holds_the_key_and_works_once_within_a_minute(app):
    """Security review L4 (28 Oct): the link was the key itself, kept in browser history and history sync."""
    key = MK.key()
    url = MK.let_in_url()
    assert key not in url and "c=" in url and "k=" not in url
    code = url.split("c=")[1].split("&")[0]
    mac = TestClient(app)
    assert mac.get("/let-in", params={"c": code}, follow_redirects=False).status_code == 303
    again = TestClient(app).get("/let-in", params={"c": code}, follow_redirects=False)
    assert again.status_code == 403 and "used or is out of date" in again.text  # spent
    assert TestClient(app).get("/let-in", params={"k": key}, follow_redirects=False).status_code == 403  # the old link
    old = MK.let_in_code(now=time.time() - MK.CODE_S - 1)
    assert TestClient(app).get("/let-in", params={"c": old}, follow_redirects=False).status_code == 403
    forged = MK.let_in_code(value="another key")
    assert TestClient(app).get("/let-in", params={"c": forged}, follow_redirects=False).status_code == 403
    assert not MK.redeem("1.2", value=key) and not MK.redeem("x.y.z", value=key) and not MK.redeem("", value=key)


def test_rotate_signs_everyone_out_and_the_app_and_phone_carry_on(app):
    mac = TestClient(app, headers={"Cookie": MK.cookie_header()})
    assert mac.get("/api/octo/sessions").status_code == 200
    unspent = MK.let_in_code()
    token = PH.add_device(app.state.db(), "iPhone")
    old = MK.key()
    new = MK.rotate()
    assert new != old and MK.key() == new and oct(os.stat(MK.FILE).st_mode & 0o777) == "0o600"
    assert mac.get("/api/octo/sessions").status_code == 401  # no restart needed
    assert TestClient(app).get("/let-in", params={"c": unspent}, follow_redirects=False).status_code == 403
    # The Octo app reads the new key: the lab proves it holds it, and a code made with it lets the window in.
    c = "0123456789abcdef0123"
    assert TestClient(app).get("/mac-hello", params={"c": c}).json()["proof"] == MK.proof(c, new)
    window = TestClient(app)
    assert window.get("/let-in", params={"c": MK.let_in_code(value=new)}, follow_redirects=False).status_code == 303
    assert window.get("/api/octo/sessions").status_code == 200
    # A paired phone has its own pairing: the rotate doesn't touch it.
    phone = TestClient(app, base_url=f"https://192.168.1.20:{PH.PORT}", headers={"Cookie": f"{PH.COOKIE}={token}"})
    assert phone.get("/api/octo/sessions").status_code == 200


def test_the_octo_app_makes_its_code_as_the_lab_checks_it():
    """macapp/Octo.swift's letIn: the same label and shape as mackey.let_in_code, and never the key in the link."""
    src = (Path(__file__).parents[1] / "macapp" / "Octo.swift").read_text()
    body = src[src.index("static func letIn("):]
    body = body[:body.index("\n    }\n")]
    assert '"octo-let-in:"' in body and 'name: "c"' in body and 'name: "k"' not in body
    assert "UInt8.random" in body and "(0..<12)" in body  # 24 hex characters of nonce, as let_in_code
    # The app hands the default browser only the schemes it lets the main frame open (security review L10).
    assert src.count("Self.opensOutside(url)") == 2 and "if let url = action.request.url { NSWorkspace" not in src


def test_let_in_never_sends_the_browser_to_another_site(app):
    mac = TestClient(app)
    for to in ("//evil.example/x", "https://evil.example", "/\\evil.example", "javascript:alert(1)"):
        r = mac.get("/let-in", params={"c": MK.let_in_code(), "to": to}, follow_redirects=False)
        assert r.headers["location"] == "/", to


def test_a_phone_uses_its_own_pairing_and_is_never_let_in_as_the_mac(app):
    token = PH.add_device(app.state.db(), "iPhone")
    phone = TestClient(app, base_url=f"https://192.168.1.20:{PH.PORT}", headers={"Cookie": f"{PH.COOKIE}={token}"})
    assert phone.get("/api/octo/sessions").status_code == 200  # no Mac key needed on the phone's socket
    assert phone.get("/let-in", params={"c": MK.let_in_code()}, follow_redirects=False).status_code == 404
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
