"""Invite a tester (octo/invite.py): the Gmail account is kept only by `octo invite setup`; without it there's no card
and no route, so a public copy of this code can't send as anyone."""

import smtplib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import invite as IV
from octo.api import make_router
from octo.db import connect

LINE = "curl -fsSL https://beta.example.org/0123abcd/install.sh | bash"


class FakeSMTP:
    sent: list = []
    refuse = False

    def close(self):
        pass

    def __init__(self, host, port, timeout):
        assert (host, port) == IV.SMTP

    def starttls(self):
        pass

    def ehlo(self):
        self.esmtp_features = {"auth": "PLAIN LOGIN"}

    def login(self, user, password):
        if self.refuse or password != "abcdabcdabcdabcd":
            raise smtplib.SMTPAuthenticationError(535, b"no")

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)

    def quit(self):
        pass


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(IV, "CONFIG", tmp_path / "invite.json")
    monkeypatch.setattr(IV.smtplib, "SMTP", FakeSMTP)
    FakeSMTP.sent = []


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    return TestClient(app, base_url="http://127.0.0.1:8765", headers={"Origin": "http://127.0.0.1:8765"})


def test_a_lab_without_the_account_has_no_invite_card_and_its_route_refuses(client):
    assert client.get("/api/octo/settings/invite").status_code == 404
    assert client.post("/api/octo/settings/invite", json={"name": "Robin", "email": "robin@example.org"}).status_code == 404
    assert not FakeSMTP.sent


def test_setup_keeps_the_account_only_once_gmail_accepts_it(client):
    with pytest.raises(IV.InviteError, match="app password"):
        IV.setup("owner@example.org", "wrong", LINE, smtp=FakeSMTP)
    with pytest.raises(IV.InviteError, match="install line"):
        IV.setup("owner@example.org", "abcd abcd abcd abcd", "curl https://x | sh", smtp=FakeSMTP)
    assert not IV.CONFIG.exists()
    IV.setup("Owner@Example.org", "abcd abcd abcd abcd", LINE, smtp=FakeSMTP)
    assert oct(IV.CONFIG.stat().st_mode & 0o777) == "0o600"
    st = client.get("/api/octo/settings/invite").json()
    assert st == {"ready": True, "from": "owner@example.org", "link": True, "sent": []}  # no password, no line
    IV.setup("owner@example.org", "", "", smtp=FakeSMTP)  # blank keeps what's kept
    assert IV.load()["line"] == LINE


def test_an_invitation_carries_the_line_and_is_counted(client):
    IV.setup("owner@example.org", "abcdabcdabcdabcd", LINE, smtp=FakeSMTP)
    r = client.post("/api/octo/settings/invite", json={"name": " Robin\nLee ", "email": "Robin@Example.org"})
    assert r.status_code == 200 and r.json()["sent"][0]["name"] == "Robin Lee"
    msg = FakeSMTP.sent[0]
    assert msg["To"] == "robin@example.org" and msg["From"] == "Octo <owner@example.org>"
    assert "Hi Robin Lee," in msg.get_content() and LINE in msg.get_content()
    assert client.post("/api/octo/settings/invite", json={"name": "x", "email": "not-an-email"}).status_code == 400


def test_twenty_a_day_at_most():
    IV.setup("owner@example.org", "abcdabcdabcdabcd", LINE, smtp=FakeSMTP)
    for i in range(IV.PER_DAY):
        IV.send(f"T{i}", f"t{i}@example.org", now=lambda: 1000.0 + i, smtp=FakeSMTP)
    with pytest.raises(IV.InviteError, match="tomorrow"):
        IV.send("One more", "x@example.org", now=lambda: 2000.0, smtp=FakeSMTP)
    IV.send("Next day", "y@example.org", now=lambda: 1000.0 + 86400 + IV.PER_DAY, smtp=FakeSMTP)


def test_a_refused_login_later_says_what_to_do():
    IV.setup("owner@example.org", "abcdabcdabcdabcd", LINE, smtp=FakeSMTP)
    FakeSMTP.refuse = True
    try:
        with pytest.raises(IV.InviteError, match="App passwords"):
            IV.send("Robin", "robin@example.org", smtp=FakeSMTP)
    finally:
        FakeSMTP.refuse = False
    assert IV.status()["sent"] == []


class GoogleLike(smtplib.SMTP):
    """smtplib's own login against what smtp.gmail.com did on 4 Nov: 535 to PLAIN, then it hung up on LOGIN."""
    tried: list = []

    def __init__(self, host, port, timeout):
        super().__init__()

    def starttls(self):
        pass

    def ehlo(self, name=""):
        self.ehlo_resp, self.does_esmtp = b"ok", True
        self.esmtp_features = {"auth": "LOGIN PLAIN XOAUTH2"}
        return 250, b"ok"

    def auth(self, mechanism, authobject, *, initial_response_ok=True):
        GoogleLike.tried.append(mechanism)
        if len(GoogleLike.tried) > 1:
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    def quit(self):
        raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")  # it had hung up


def test_a_refused_password_says_so_not_that_gmail_was_unreachable():
    GoogleLike.tried = []
    with pytest.raises(IV.InviteError, match="Google refused that"):
        IV.setup("owner@example.org", "wrongwrongwrongw", LINE, smtp=GoogleLike)
    assert GoogleLike.tried == ["PLAIN"]


def test_it_can_send_as_an_alias_while_signing_in_as_the_account(monkeypatch):  # 4 Nov: support@ on the owner's own login
    logins = []
    monkeypatch.setattr(FakeSMTP, "login", lambda self, user, password: logins.append(user))
    IV.setup("owner@example.org", "abcdabcdabcdabcd", LINE, "Support@Example.org", smtp=FakeSMTP)
    assert IV.status()["from"] == "support@example.org"
    IV.send("Robin", "robin@example.org", smtp=FakeSMTP)
    assert FakeSMTP.sent[-1]["From"] == "Octo <support@example.org>" and logins[-1] == "owner@example.org"
    IV.setup("owner@example.org", "", "", smtp=FakeSMTP)  # blank keeps the alias
    assert IV.status()["from"] == "support@example.org"
    IV.setup("owner@example.org", "", "", "owner@example.org", smtp=FakeSMTP)  # the account's own address: no alias
    assert IV.status()["from"] == "owner@example.org" and "as" not in IV.load()
    with pytest.raises(IV.InviteError, match="alias"):
        IV.setup("owner@example.org", "", "", "support", smtp=FakeSMTP)
