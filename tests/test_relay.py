"""The relay to the recorder (octo/relay.py) and the lab's fallback to it (octo/device.py), with a fake
recorder on 127.0.0.1."""

import errno
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from octo import device as D
from octo import relay as R


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeRecorder(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        code = 409 if self.path == "/busy" else 200
        out = b'{"echo":' + (body or b"null") + b',"nonce":"' + self.headers.get("X-Octo-Nonce", "").encode() + b'"}'
        self.send_response(code)
        self.send_header("X-Octo-Next", "n2")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def relay(monkeypatch, tmp_path):
    rec = ThreadingHTTPServer(("127.0.0.1", 0), FakeRecorder)
    rel = ThreadingHTTPServer(("127.0.0.1", 0), R.Relay)
    for srv in (rec, rel):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(R, "DEVICE_PORT", rec.server_address[1])
    monkeypatch.setattr(D, "RELAY", ("127.0.0.1", rel.server_address[1]))
    (tmp_path / "relay.key").write_text("k" * 43)
    monkeypatch.setattr(R, "KEY_FILE", tmp_path / "relay.key")
    monkeypatch.setattr(D, "RELAY_KEY", tmp_path / "relay.key")
    monkeypatch.setattr(R, "ALLOW_LOOPBACK", True)  # the fake recorder is on this Mac
    yield
    rec.shutdown()
    rel.shutdown()


def blocked(monkeypatch):
    """Direct connections fail as macOS's Local Network privacy fails them; the relay's still work."""
    real = urllib.request.urlopen

    def urlopen(req, timeout=None):
        if f":{D.RELAY[1]}/" not in req.full_url:
            raise urllib.error.URLError(OSError(errno.EHOSTUNREACH, "No route to host"))
        return real(req, timeout=timeout)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


def test_blocked_requests_go_through_the_relay_unchanged(relay, monkeypatch):
    blocked(monkeypatch)
    dev = D.Device("octo-rec.local", "00" * 32, ip="127.0.0.1")
    status, headers, body = dev._http("POST", "/status", b'{"a":1}', {"X-Octo-Nonce": "n1"})
    assert status == 200 and body == b'{"echo":{"a":1},"nonce":"n1"}' and headers.get("X-Octo-Next") == "n2"
    assert dev.relayed  # the rest of this sync goes straight through the relay
    status, _, _ = dev._http("POST", "/busy", b"{}")
    assert status == 409  # the recorder's own errors come back as they are


def test_relay_down_says_what_to_do(monkeypatch):
    blocked(monkeypatch)
    monkeypatch.setattr(D, "RELAY", ("127.0.0.1", free_port()))
    with pytest.raises(D.Unreachable, match="octo login-item install"):
        D.Device("octo-rec.local", "00" * 32, ip="127.0.0.1")._http("POST", "/status", b"{}")


def ask_relay(to: str, headers: dict | None = None, host: str | None = None) -> int:
    url = f"http://{host or '127.0.0.1'}:{D.RELAY[1]}/status"
    req = urllib.request.Request(url, data=b"{}", method="POST", headers={"X-Octo-To": to, **D.relay_headers(), **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def test_relay_only_forwards_to_private_addresses(relay, monkeypatch):
    assert ask_relay("1.1.1.1") == 400
    assert ask_relay("169.254.1.1") == 400  # link-local
    monkeypatch.setattr(R, "ALLOW_LOOPBACK", False)
    assert ask_relay("127.0.0.1") == 400  # this Mac's own services: never through the relay


def test_relay_answers_only_the_lab(relay):
    """Review 8 Oct, finding 17: its key, and a Host of 127.0.0.1 by number (a rebinding page sends its own name)."""
    assert ask_relay("127.0.0.1") == 200
    assert ask_relay("127.0.0.1", {"X-Octo-Relay-Key": "wrong"}) == 403
    assert ask_relay("127.0.0.1", host="localhost") == 403
    req = urllib.request.Request(f"http://127.0.0.1:{D.RELAY[1]}/_router")  # no key: not even the router's address
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 403 and "login-item install" in e.value.headers["X-Octo-Relay-Error"]


def test_a_relay_with_no_key_says_how_to_fix_it(relay, monkeypatch, tmp_path):
    blocked(monkeypatch)
    monkeypatch.setattr(R, "KEY_FILE", tmp_path / "missing.key")
    with pytest.raises(D.Unreachable, match="octo login-item install"):
        D.Device("octo-rec.local", "00" * 32, ip="127.0.0.1")._http("POST", "/status", b"{}")


def test_recorder_gone_through_the_relay_is_unreachable(relay, monkeypatch):
    blocked(monkeypatch)
    monkeypatch.setattr(R, "DEVICE_PORT", free_port())  # nothing listening: as if the recorder were away
    with pytest.raises(D.Unreachable, match="through the relay"):
        D.Device("octo-rec.local", "00" * 32, ip="127.0.0.1")._http("POST", "/status", b"{}")


def test_relay_tells_phone_access_the_router(relay, monkeypatch):
    from octo import phone as PH
    monkeypatch.setattr(R, "router_mac", lambda: "3c:84:6a:00:00:01")
    monkeypatch.setattr(PH, "_router_here", lambda: None)  # what Homebrew's Python sees under launchd
    assert PH.router() == "3c:84:6a:00:00:01"
