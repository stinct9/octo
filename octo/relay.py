"""The lab's way to the recorder when macOS won't let it through (5 Oct).

macOS's Local Network privacy blocks the login item: started by launchd, Homebrew's Python gets
"No route to host" from the recorder while it reaches the router and the internet, and macOS shows no
prompt to a background item (from a terminal it works: the terminal app holds the permission). Apple's
own /usr/bin/python3 is allowed when it's the program a LaunchAgent runs, though not when Homebrew's
Python starts it. So this relay runs as its own LaunchAgent (com.octo.relay, `octo login-item install`)
under Apple's Python, listens on 127.0.0.1 only, and forwards a request to the private IPv4 address in
its X-Octo-To header, port 80, passing the answer back unchanged. Requests and answers are signed end to
end (octo/device.py), so the relay can't forge either. Standard library only: it runs on Python 3.9.

GET /_router answers the home router's hardware address, for phone access (octo/phone.py): macOS hides
the ARP table from the lab's Python too ("no entry"), so phone access never thought it was home.

Only for the lab (review 8 Oct, finding 17): a door from this Mac to the home network is worth locking.
- It answers only requests addressed to 127.0.0.1 by number: a web page that points a name of its own at
  127.0.0.1 (DNS rebinding; Safari has no check for it) sends that name, and is refused.
- Every request carries the relay's key (X-Octo-Relay-Key), a 0600 file beside this copy that
  `octo login-item install` writes; other programs and other macOS users can't read it.
- It forwards only to the home network's private addresses: never this Mac's own (127/8), never link-local.
"""

import hmac
import http.client
import ipaddress
import json
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8767
DEVICE_PORT = 80
TIMEOUT = 30
KEY_FILE = Path(__file__).with_name("relay.key")
ALLOW_LOOPBACK = False  # tests only: their fake recorder listens on 127.0.0.1


def relay_key():
    try:
        return KEY_FILE.read_text().strip() or None
    except OSError:
        return None


def router_mac():
    """The default gateway's hardware address, as octo/phone.py router() would read it."""
    try:
        r = subprocess.run(["/sbin/route", "-n", "get", "default"], capture_output=True, text=True, timeout=5).stdout
        gw = next((ln.split(":", 1)[1].strip() for ln in r.splitlines() if ln.strip().startswith("gateway:")), None)
        if not gw:
            return None
        a = subprocess.run(["/usr/sbin/arp", "-n", gw], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r" at ([0-9a-f:]{11,17}) ", a)
        return m.group(1) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


class Relay(BaseHTTPRequestHandler):
    def _go(self):
        if self.headers.get("Host", "") != f"127.0.0.1:{self.server.server_address[1]}":
            return self._fail(403, "this relay answers only the lab on this Mac")
        key = relay_key()
        if not key:
            return self._fail(403, "the relay has no key: run octo login-item install")
        if not hmac.compare_digest(self.headers.get("X-Octo-Relay-Key", ""), key):
            return self._fail(403, "not the relay's key: run octo login-item install")
        if self.command == "GET" and self.path == "/_router" and "X-Octo-To" not in self.headers:
            b = json.dumps({"mac": router_mac()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return
        to = self.headers.get("X-Octo-To", "")
        try:
            ip = ipaddress.IPv4Address(to)
            ok = ip.is_private and not ip.is_link_local and (ALLOW_LOOPBACK or not ip.is_loopback)
        except ValueError:
            ok = False
        if not ok:
            return self._fail(400, "X-Octo-To must be a private IPv4 address on the home network")
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else None
        skip = ("host", "x-octo-to", "x-octo-relay-key", "connection", "content-length")
        headers = {k: v for k, v in self.headers.items() if k.lower() not in skip}
        try:
            c = http.client.HTTPConnection(to, DEVICE_PORT, timeout=TIMEOUT)
            c.request(self.command, self.path, body=body, headers=headers)
            r = c.getresponse()
            data = r.read()
            c.close()
        except (OSError, http.client.HTTPException) as e:
            return self._fail(502, f"{type(e).__name__}: {e}")
        self.send_response(r.status)
        for k, v in r.getheaders():
            if k.lower() not in ("transfer-encoding", "connection", "content-length", "server", "date"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _go

    def _fail(self, code, why):
        b = why.encode()
        self.send_response(code)
        self.send_header("X-Octo-Relay-Error", why[:200].replace("\n", " "))
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *args):
        pass  # nothing about each request in the log


def serve(port=PORT):
    ThreadingHTTPServer(("127.0.0.1", port), Relay).serve_forever()


if __name__ == "__main__":
    serve()
