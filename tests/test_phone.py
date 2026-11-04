"""Phone access: pairing codes, the paired-phone gate, Mac-only settings."""

import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import phone as PH
from octo.api import make_router
from octo.db import connect
from octo.phone_api import install


def test_a_code_works_once_expires_and_dies_after_five_wrong_tries(monkeypatch):
    p = PH.Pairing()
    code = p.new_code()["code"]
    assert len(code) == 8 and code.isdigit()
    assert p.use(code) and not p.use(code)  # once only
    code = p.new_code()["code"]
    for _ in range(PH.CODE_TRIES):
        assert not p.use("00000000" if code != "00000000" else "11111111")
    assert not p.use(code)  # voided by the wrong tries
    code = p.new_code()["code"]
    later = time.time() + PH.CODE_TTL_S + 1
    monkeypatch.setattr(PH.time, "time", lambda: later)
    assert not p.use(code)  # expired


@pytest.fixture
def clients(tmp_path, monkeypatch):
    monkeypatch.setattr(PH, "DIR", tmp_path / "phone")
    db = lambda: connect(tmp_path / "t.db")
    app = FastAPI()
    app.include_router(make_router(db, processor=type("P", (), {"kick": lambda self: None})()))
    install(app, db)

    @app.get("/api/steps")  # stands in for a bench endpoint
    def steps() -> list:
        return []

    mac = TestClient(app, base_url="http://testserver")
    phone = TestClient(app, base_url=f"https://testserver:{PH.PORT}")
    return mac, phone


def test_an_unpaired_phone_gets_nothing(clients):
    mac, phone = clients
    assert phone.get("/api/octo/sessions").status_code == 401
    r = phone.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/pair"
    assert phone.get("/pair").status_code == 200
    assert phone.post("/api/pair", json={"code": "12345678"}).status_code == 403  # no code shown on the Mac
    assert mac.get("/api/octo/sessions").status_code == 200  # the Mac is unaffected


def test_pairing_then_what_a_phone_may_and_may_not_do(clients):
    mac, phone = clients
    code = mac.post("/api/octo/phone/code", json={}).json()["code"]
    r = phone.post("/api/pair", json={"code": code, "name": "iPhone"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "samesite=strict" in cookie.lower()
    assert phone.get("/api/octo/sessions").status_code == 200
    assert phone.get("/api/steps").status_code == 403  # the bench stays on the Mac
    assert phone.get("/api/octo/phone").status_code == 403  # so do these settings
    assert phone.post("/api/octo/phone/code", json={}).status_code == 403  # a phone can't mint codes
    assert phone.get("/api/octo/pipeline").status_code == 403  # nor see or switch models
    assert phone.post("/api/octo/pipeline/diarize", json={"choice": "pyannote-community-1"}).status_code == 403
    assert phone.get("/api/octo/settings/ticktick").status_code == 403  # nor see or set a TickTick token
    assert phone.post("/api/octo/settings/ticktick", json={"token": "x"}).status_code == 403
    devices = mac.get("/api/octo/phone").json()["devices"]
    assert [d["name"] for d in devices] == ["iPhone"]
    assert mac.post("/api/octo/phone/unpair", json={"id": devices[0]["id"]}).status_code == 200
    assert phone.get("/api/octo/sessions").status_code == 401  # unpaired: locked out again


def test_the_https_listener_really_serves_with_the_macs_own_certificate(tmp_path, monkeypatch):
    """Starts the real listener (on 127.0.0.1, a spare port), checks the TLS chain against the CA,
    and that a taken port is reported instead of exiting the process."""
    import asyncio
    import socket
    import ssl

    import httpx

    from octo.phone_api import PhoneServer

    monkeypatch.setattr(PH, "DIR", tmp_path / "phone")
    monkeypatch.setattr(PH, "hostnames", lambda: [])
    monkeypatch.setattr(PH, "addresses", lambda: ["127.0.0.1"])
    monkeypatch.setattr(PH, "tailscale_ips", lambda: [])
    monkeypatch.setattr(PH, "at_home", lambda: True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    app = FastAPI()
    install(app, lambda: connect(tmp_path / "t.db"))
    server = PhoneServer(app, host="127.0.0.1", port=port)
    app.state.phone_server = server

    async def scenario():
        await server.start()
        assert server.running and server.error is None
        ctx = ssl.create_default_context(cafile=str(PH.DIR / "ca.crt"))
        async with httpx.AsyncClient(verify=ctx) as client:
            for _ in range(50):
                try:
                    r = await client.get(f"https://127.0.0.1:{port}/pair")
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(0.05)
            assert r.status_code == 200 and "Pair with your Mac" in r.text
            r = await client.get(f"https://127.0.0.1:{port}/api/octo/sessions")
            assert r.status_code == 401  # the gate applies on this listener
        clash = PhoneServer(app, host="127.0.0.1", port=port)
        await clash.start()  # the port is taken: an error, not sys.exit
        assert not clash.running and "in use" in clash.error
        await server.stop()
        assert not server.running

    asyncio.run(scenario())



def test_the_ca_can_only_vouch_for_local_names_and_the_listener_stays_off_away_from_home(tmp_path, monkeypatch):  # M43
    import asyncio
    import subprocess

    from octo.phone_api import PhoneServer
    monkeypatch.setattr(PH, "DIR", tmp_path / "phone")
    certs = PH.ensure_certs(names=["sams-mac.local"], ips=["192.168.1.20"])
    text = subprocess.run([PH.OPENSSL, "x509", "-in", str(certs["ca"]), "-noout", "-text", "-enddate"],
                          capture_output=True, text=True).stdout
    assert "Name Constraints: critical" in text and "DNS:local" in text and "192.168.0.0" in text
    monkeypatch.setattr(PH, "router", lambda: "aa:bb:cc:dd:ee:ff")
    PH.set_enabled(True)  # records the home router
    assert PH.at_home()
    monkeypatch.setattr(PH, "router", lambda: "11:22:33:44:55:66")  # a café
    monkeypatch.setattr(PH, "tailscale_ips", lambda: [])  # and no Tailscale
    server = PhoneServer(FastAPI(), port=0)
    asyncio.run(server.start())
    assert not server.running and "home network" in server.error


# ---- the app and Tailscale (8 Oct, docs/phone-plan.md §4) ----

def test_anything_but_the_macs_own_socket_gets_the_phones_rules(clients):
    """Deny by default: a proxy on the Mac's socket (`tailscale serve` to 127.0.0.1:8765), or any other
    socket, is a phone and needs pairing; the Mac's own requests are unaffected."""
    mac, _ = clients
    app = mac.app
    assert mac.get("/api/octo/sessions").status_code == 200
    assert mac.get("/api/octo/sessions", headers={"X-Forwarded-For": "100.64.1.2"}).status_code == 401
    assert mac.get("/api/octo/sessions", headers={"Tailscale-User-Login": "sam@example.com"}).status_code == 401
    assert TestClient(app, base_url="http://mac.tail1234.ts.net").get("/api/octo/sessions").status_code == 401
    assert TestClient(app, base_url="http://100.101.102.103:8765").get("/api/octo/sessions").status_code == 401
    assert TestClient(app, base_url="http://localhost:8765").get("/api/octo/sessions").status_code == 200


def test_the_app_pairs_by_qr_once_and_keeps_its_token(clients, monkeypatch, tmp_path):
    mac, phone = clients
    monkeypatch.setattr(PH, "hostnames", lambda: ["sams-mac.local"])
    monkeypatch.setattr(PH, "addresses", lambda: ["192.168.1.20"])
    monkeypatch.setattr(PH, "tailscale_ips", lambda: ["100.101.102.103"])
    monkeypatch.setattr(PH, "mac_name", lambda: "Sam's MacBook")
    monkeypatch.setattr(PH, "router", lambda: "aa:bb:cc:dd:ee:ff")

    async def no_listener():  # the real one is tested above
        return None
    monkeypatch.setattr(mac.app.state.phone_server, "start", no_listener)
    assert phone.post("/api/octo/phone/qr", json={}).status_code == 401  # a phone can't make one (not even paired)
    r = mac.post("/api/octo/phone/qr", json={}).json()
    assert PH.enabled()  # showing the code switched phone access on
    from urllib.parse import parse_qs, urlparse
    link = urlparse(r["link"])
    q = {k: v[0] for k, v in parse_qs(link.query).items()}
    assert link.scheme == "octo" and link.netloc == "pair" and q["v"] == "1" and q["n"] == "Sam's MacBook"
    assert q["h"].split(",") == ["sams-mac.local", "192.168.1.20", "100.101.102.103"] and q["p"] == str(PH.PORT)
    assert q["ca"] == PH.ca_sha256() and len(q["ca"]) == 64 and r["svg"].startswith("<svg")
    # Scales into the page's box whole (8 Oct: with a fixed size and no viewBox the box cropped two corner marks).
    head = r["svg"][:r["svg"].index(">")]
    assert 'viewBox="0 0 ' in head and "width=" not in head and "height=" not in head
    assert "token" not in r["link"]  # nothing long-lived in the code
    got = phone.post("/api/pair", json={"secret": q["s"], "name": "iPhone", "app": True})
    assert got.status_code == 200 and got.json()["mac"] == "Sam's MacBook"
    token = got.json()["token"]
    assert phone.post("/api/pair", json={"secret": q["s"], "app": True}).status_code == 403  # once only
    # The app sends its token as the cookie on whichever address answers.
    fresh = TestClient(mac.app, base_url=f"https://testserver:{PH.PORT}", cookies={PH.COOKIE: token})
    hello = fresh.get("/api/octo/phone/hello").json()
    assert hello["device"] == "iPhone" and hello["tailscale"] == ["100.101.102.103"] and hello["ca_sha256"] == q["ca"]
    assert fresh.get("/api/octo/sessions").status_code == 200
    # Safari's pairing still answers with the cookie only.
    code = mac.post("/api/octo/phone/code", json={}).json()["code"]
    assert "token" not in phone.post("/api/pair", json={"code": code}).json()


def test_a_qr_secret_expires(monkeypatch):
    p = PH.Pairing()
    secret = p.new_secret()["secret"]
    assert len(secret) >= 20
    later = time.time() + PH.CODE_TTL_S + 1
    monkeypatch.setattr(PH.time, "time", lambda: later)
    assert not p.use_secret(secret)


def test_tailscale_is_a_100_64_address_on_a_tunnel():
    out = """lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 192.168.1.20 netmask 0xffffff00 broadcast 192.168.1.255
\tinet 100.70.1.2 netmask 0xffffff00
utun3: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1380
\tinet 10.8.0.2 --> 10.8.0.1 netmask 0xffffffff
utun4: flags=8051<UP,POINTOPOINT,RUNNING,MULTICAST> mtu 1280
\tinet 100.101.102.103 --> 100.101.102.103 netmask 0xffffffff
"""
    assert PH.tailscale_ips(out) == ["100.101.102.103"]  # not a carrier's 100.x on Wi-Fi, not another VPN
    assert PH.tailscale_ips("") == []


def test_the_ca_allows_tailscale_and_an_old_one_is_renewed_only_when_needed(tmp_path, monkeypatch):
    import subprocess
    monkeypatch.setattr(PH, "DIR", tmp_path / "phone")
    # A version-1 CA from before 8 Oct (no ca.json, no Tailscale range): kept while there's no Tailscale.
    PH.ensure_certs(names=["sams-mac.local"], ips=["192.168.1.20"])
    (PH.DIR / "ca.json").unlink()
    before = PH.ca_sha256()
    assert PH.ca_version() == 1
    PH.ensure_certs(names=["sams-mac.local"], ips=["192.168.1.20"])
    assert PH.ca_sha256() == before and PH._settings().get("ca_renewed_at") is None
    # Tailscale appears: a new CA that can vouch for it, said on the Phone page.
    certs = PH.ensure_certs(names=["sams-mac.local"], ips=["192.168.1.20", "100.101.102.103"])
    assert PH.ca_version() == 2 and PH.ca_sha256() != before and PH._settings().get("ca_renewed_at")
    text = subprocess.run([PH.OPENSSL, "x509", "-in", str(certs["ca"]), "-noout", "-text"], capture_output=True, text=True).stdout
    assert "100.64.0.0" in text and "DNS:local" in text
    leaf = subprocess.run([PH.OPENSSL, "x509", "-in", str(PH.DIR / "server.crt"), "-noout", "-text"], capture_output=True, text=True).stdout
    assert "IP Address:100.101.102.103" in leaf and "IP Address:192.168.1.20" in leaf
    # The chain carries the CA, and the fingerprint is the CA's DER's SHA-256 (as the app pins it).
    assert certs["cert"].read_text().count("BEGIN CERTIFICATE") == 2
    der = subprocess.run([PH.OPENSSL, "x509", "-in", str(certs["ca"]), "-outform", "DER"], capture_output=True).stdout
    import hashlib
    assert PH.ca_sha256() == hashlib.sha256(der).hexdigest()
    ok = subprocess.run([PH.OPENSSL, "verify", "-CAfile", str(certs["ca"]), str(PH.DIR / "server.crt")], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stderr  # name constraints allow both addresses


def test_a_paired_phone_hears_what_is_waiting_as_keys_never_names(clients, monkeypatch):
    """The app's own notifications (8 Oct): hello gives a paired phone the Mac notifier's keys, so the phone
    can tell what's new, in counts. The Mac's own hello doesn't need them; an unpaired phone gets nothing."""
    from octo import notify as NT
    mac, phone = clients
    monkeypatch.setattr(NT, "waiting", lambda conn, now=None: {"review:ses_b", "confirm:ses_a", "coach:note_1"})
    assert phone.get("/api/octo/phone/hello").status_code == 401
    code = mac.post("/api/octo/phone/code", json={}).json()["code"]
    assert phone.post("/api/pair", json={"code": code, "name": "iPhone"}).status_code == 200
    hello = phone.get("/api/octo/phone/hello").json()
    assert hello["waiting"] == ["coach:note_1", "confirm:ses_a", "review:ses_b"]
    assert "waiting" not in mac.get("/api/octo/phone/hello").json()


def test_the_last_tailscale_address_is_still_told_while_tailscale_is_off(tmp_path, monkeypatch):
    # 13 Oct: the app last reached the Mac at home with the Mac's Tailscale off, was told no Tailscale address,
    # dropped it, and away from home had nowhere to look.
    monkeypatch.setattr(PH, "DIR", tmp_path / "phone")
    (tmp_path / "phone").mkdir()
    monkeypatch.setattr(PH, "tailscale_ips", lambda: [])
    assert PH.tailscale_to_tell() == []  # never on: nothing to tell
    monkeypatch.setattr(PH, "tailscale_ips", lambda: ["100.70.1.2"])
    assert PH.tailscale_to_tell() == ["100.70.1.2"]
    monkeypatch.setattr(PH, "tailscale_ips", lambda: [])
    assert PH.tailscale_to_tell() == ["100.70.1.2"]  # off now: the last one
    monkeypatch.setattr(PH, "tailscale_ips", lambda: ["100.80.0.9"])
    assert PH.tailscale_to_tell() == ["100.80.0.9"]  # a new one replaces it
    PH.set_enabled(False)  # other settings keep it
    monkeypatch.setattr(PH, "tailscale_ips", lambda: [])
    assert PH.tailscale_to_tell() == ["100.80.0.9"]


# ---- away from home: Tailscale's setup, guided (19 Oct) ----

# The shape of `tailscale status --json` (1.102.4, 19 Oct), made-up names and keys.
def _status(state="Running", peers=None, ips=("100.101.102.103", "fd7a:115c:a1e0::1")):
    return json.dumps({"BackendState": state, "TailscaleIPs": list(ips),  # stopped, it still lists them (19 Oct)
                       "Self": {"HostName": "Sam's MacBook Air", "OS": "macOS"},
                       "CurrentTailnet": {"Name": "sam@example.com"},
                       "Peer": peers if peers is not None else {
                           "nodekey:a": {"HostName": "Sam's iPhone", "OS": "iOS", "Online": False,
                                         "LastSeen": "2026-10-14T16:18:24.123456789Z"},
                           "nodekey:b": {"HostName": "nas", "OS": "linux", "Online": True}}})


def test_tailscale_is_read_from_its_own_command_line():
    there = lambda path: path == PH.TAILSCALE_CLI[0]
    asked = []
    ts = PH.tailscale_status(ask=lambda cli: asked.append(cli) or (0, _status()), exists=there, ips=[])
    assert asked == [PH.TAILSCALE_CLI[0]] and ts["installed"] and ts["running"] and ts["state"] == "Running"
    assert ts["ips"] == ["100.101.102.103"] and ts["name"] == "Sam's MacBook Air" and ts["account"] == "sam@example.com"
    assert ts["phones"] == [{"name": "Sam's iPhone", "online": False, "last_seen": 1791994704.123456}]  # iOS only
    stopped = PH.tailscale_status(ask=lambda cli: (0, _status("Stopped")), exists=there, ips=[])
    assert stopped["state"] == "Stopped" and stopped["ips"] == []  # its addresses aren't on any interface
    # Not installed at all: nothing is asked.
    ts = PH.tailscale_status(ask=lambda cli: pytest.fail("asked"), exists=lambda p: False, ips=[])
    assert not ts["installed"] and not ts["running"]
    # Installed but not running (the command can't reach it), or answering nonsense.
    for answer in ((1, ""), (0, "not json")):
        ts = PH.tailscale_status(ask=lambda cli: answer, exists=there, ips=[])
        assert ts["installed"] and not ts["running"]
    # The command line can't be asked (another user's Tailscale, a sandbox) but the tunnel has an address: running.
    ts = PH.tailscale_status(ask=lambda cli: (1, ""), exists=there, ips=["100.101.102.103"])
    assert ts["running"] and ts["state"] == "Running" and ts["phones"] == []
    # Only the app (no command line found): installed, not running as far as anyone can tell.
    assert PH.tailscale_status(exists=lambda p: p == PH.TAILSCALE_APP, ips=[])["installed"]


def test_the_next_step_away_from_home_is_one_thing_in_order():
    ask = lambda state, peers=None: PH.tailscale_status(ask=lambda cli: (0, _status(state, peers)),
                                                       exists=lambda p: True, ips=[])
    phone = [{"id": "dev_1", "paired_at": 100.0, "away_at": None}]
    step = lambda ts, enabled=True, devices=phone, renewed=None: PH.away_step(ts, enabled, devices, renewed)["step"]
    assert step(PH.tailscale_status(exists=lambda p: False, ips=[])) == "install_mac"
    assert PH.away_step(PH.tailscale_status(exists=lambda p: False, ips=[]), True, [], None)["link"].startswith("https://tailscale.com/")
    assert step(PH.tailscale_status(ask=lambda cli: (1, ""), exists=lambda p: True, ips=[])) == "open_mac"
    assert step(ask("NeedsLogin")) == "sign_in"
    assert step(ask("NeedsMachineAuth")) == "approve"
    assert step(ask("Stopped")) == "turn_on"
    assert step(ask("Starting")) == "starting"
    nophone = ask("Running", peers={})
    assert step(nophone) == "install_phone" and "sam@example.com" in PH.away_step(nophone, True, phone, None)["do"]
    ready = ask("Running")
    assert step(ready, devices=[]) == "pair"
    assert step(ready, enabled=False) == "switch_on"
    assert step(ready, renewed=200.0) == "scan_again"  # paired before the CA was renewed
    assert step(ready, renewed=50.0) == "try_away"  # paired after: it already trusts the new one
    assert step(ready, devices=[{**phone[0], "away_at": 300.0}]) == "ready"
    # Only ifconfig spoke (the command line couldn't be asked): the iPhone isn't asked for, since it can't be seen.
    assert step(PH.tailscale_status(ask=lambda cli: (1, ""), exists=lambda p: True, ips=["100.101.102.103"])) == "try_away"


def test_a_phone_reaching_the_lab_over_tailscale_is_kept(clients, tmp_path, monkeypatch):
    mac, _ = clients
    monkeypatch.setattr(PH, "_away_written", {})
    token = PH.add_device(connect(tmp_path / "t.db"), "iPhone")
    home = TestClient(mac.app, base_url=f"https://192.168.1.20:{PH.PORT}", cookies={PH.COOKIE: token})
    away = TestClient(mac.app, base_url=f"https://100.101.102.103:{PH.PORT}", cookies={PH.COOKIE: token})
    assert home.get("/api/octo/sessions").status_code == 200
    assert mac.get("/api/octo/phone").json()["devices"][0]["away_at"] is None  # at home isn't away
    assert away.get("/api/octo/sessions").status_code == 200
    st = mac.get("/api/octo/phone").json()
    assert st["devices"][0]["away_at"] > time.time() - 5
    assert st["away"]["step"] in ("install_mac", "open_mac")  # this test's Tailscale: none asked (conftest)


def test_step_eight_is_done_once_reached_through_tailscale_and_says_when_that_way_is_off():
    running = PH.tailscale_status(ask=lambda cli: (0, _status("Running")), exists=lambda p: True, ips=[])
    stopped = PH.tailscale_status(ask=lambda cli: (0, _status("Stopped")), exists=lambda p: True, ips=[])
    phone = [{"id": "dev_1", "paired_at": 100.0, "away_at": None}]
    got = PH.away_setup(running, True, phone, None)
    assert not got["done"] and got["do"].startswith("Try it")
    reached = [{**phone[0], "away_at": 300.0}]
    assert PH.away_setup(running, True, reached, None) == {"done": True, "do": None, "link": None, "note": None,
                                                           "seen": 300.0}
    # Reached through Tailscale before, Tailscale turned off now: still done, and the note says what to do.
    off = PH.away_setup(stopped, True, reached, None)
    assert off["done"] and off["note"].startswith("Not reachable away from home right now. Turn Tailscale on")
    # The lab started at login can't ask Tailscale's command line; with no address it reads as not open.
    unasked = PH.tailscale_status(ask=lambda cli: (1, ""), exists=lambda p: True, ips=[])
    assert "Connect" in PH.away_setup(unasked, True, reached, None)["note"]
    assert "Allow my phone" in PH.away_setup(running, False, reached, None)["note"]
