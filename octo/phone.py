"""Phone access (UI plan U4; the app since 8 Oct, docs/phone-plan.md §4): HTTPS for paired phones only,
off by default.

- This Mac's own certificate authority (data/phone/ca.*), made with the built-in openssl, signs
  the server's certificate. The Octo app pins it by its SHA-256 (from the QR code), so nothing is
  installed on the phone; Safari still needs ca.crt installed and trusted. The CA is NOT added to
  the Mac's keychain.
- The server certificate names <LocalHostName>.local, the Mac's Wi-Fi addresses and its Tailscale
  address; it's remade when those change (the CA stays, so phones keep trusting it). It's served
  with the CA behind it, so the app finds the CA it pinned in the handshake.
- Pairing: the QR code carries a one-time secret (2 minutes); Safari's page takes an 8-digit code,
  valid once for 2 minutes; 5 wrong tries from one address shut that address out until it expires, and 100 in all
  void it (review 10 Nov, L10: before, 5 from anyone on the Wi-Fi voided the owner's code). The phone gets a random device token as a
  Secure, HttpOnly, SameSite=Strict cookie (the app also in the answer, to keep in the Keychain); only
  its hash is stored. Unpairing deletes it.
- Where it listens: the home Wi-Fi address while the Mac is at home, and its Tailscale address
  (100.64.0.0/10) whenever Tailscale is up (8 Oct: away from home through Tailscale, end-to-end
  encrypted; Tailscale's relays carry only ciphertext). Nothing goes through a server of ours.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path

from . import DATA
from .db import new_id, row, rows

PORT = 8766
DIR = DATA / "phone"
CODE_TTL_S = 120
CODE_TRIES = 5  # wrong tries from one address
CODE_TOTAL_TRIES = 100  # from everyone: about one chance in a million that a guess gets in
COOKIE = "octo_device"
OPENSSL = "/usr/bin/openssl"


# ---- settings ------------------------------------------------------------------------------

def _settings_path() -> Path:
    return DIR / "settings.json"


def enabled() -> bool:
    try:
        return bool(json.loads(_settings_path().read_text()).get("enabled"))
    except (OSError, ValueError):
        return False


def set_enabled(on: bool) -> None:
    """Switching on records the home network (the router's hardware address): the Wi-Fi listener only
    ever runs there (review 4 Oct, M43). Tailscale's address doesn't depend on it."""
    found = router() if on else None  # asked outside the lock: it may ask the relay
    _update(lambda cur: {**cur, "enabled": on, "home": found or cur.get("home")})


def _settings() -> dict:
    try:
        return json.loads(_settings_path().read_text())
    except (OSError, ValueError):
        return {}


def router() -> str | None:
    """The default gateway's hardware address: the same at home whatever the Mac's own IP is, and
    different on any other network. macOS hides the ARP table from Homebrew's Python ("no entry",
    5 Oct: the same Local Network privacy as octo/relay.py), so ask the relay when it isn't there."""
    return _router_here() or _router_from_relay()


def _router_from_relay() -> str | None:
    import urllib.request
    from .device import RELAY, relay_headers
    try:
        req = urllib.request.Request(f"http://{RELAY[0]}:{RELAY[1]}/_router", headers=relay_headers())
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.loads(r.read()).get("mac")
    except (OSError, ValueError):
        return None


def _router_here() -> str | None:
    try:
        r = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True, timeout=5).stdout
        gw = next((ln.split(":", 1)[1].strip() for ln in r.splitlines() if ln.strip().startswith("gateway:")), None)
        if not gw:
            return None
        a = subprocess.run(["arp", "-n", gw], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r" at ([0-9a-f:]{11,17}) ", a)
        return m.group(1) if m else None
    except (OSError, subprocess.SubprocessError):
        return None


def at_home() -> bool:
    """On the network phone mode was switched on in. One switched on before 5 Oct records it now."""
    home = _settings().get("home")
    if not home:
        set_enabled(enabled())
        home = _settings().get("home")
    return bool(home) and router() == home


# ---- where the phone finds the Mac -----------------------------------------------------------

def hostnames() -> list[str]:
    try:
        local = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True).stdout.strip()
    except OSError:
        local = ""
    return [f"{local.lower()}.local"] if local else []


def addresses() -> list[str]:
    out = []
    for iface in ("en0", "en1"):
        try:
            ip = subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True, text=True).stdout.strip()
        except OSError:
            ip = ""
        if ip:
            out.append(ip)
    return out


TAILNET = ipaddress.ip_network("100.64.0.0/10")


def tailscale_ips(ifconfig: str | None = None) -> list[str]:
    """The Mac's Tailscale address: an IPv4 in 100.64.0.0/10 on a tunnel (utun) interface. Tailscale
    keeps it for as long as the Mac stays in the tailnet. Empty when Tailscale isn't running."""
    if ifconfig is None:
        try:
            ifconfig = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return []
    out, iface = [], ""
    for line in ifconfig.splitlines():
        if line and not line[0].isspace():
            iface = line.split(":", 1)[0]
        m = re.match(r"\s+inet (\d+\.\d+\.\d+\.\d+)\b", line)
        if m and iface.startswith("utun"):
            try:
                if ipaddress.ip_address(m.group(1)) in TAILNET:
                    out.append(m.group(1))
            except ValueError:
                pass
    return out


def tailscale_to_tell() -> list[str]:
    """The Tailscale address to give the phone: the one now, remembered; while Tailscale is off, the one it
    had last. The app keeps the addresses it's told, so a phone that last reached the Mac at home while the
    Mac's Tailscale was off had forgotten it and couldn't find the Mac away (13 Oct). It stays the same
    while the Mac stays in the tailnet; a stale one is only an address that doesn't answer."""
    now = tailscale_ips()
    if now:
        if _settings().get("tailscale_last") != now:
            _update(lambda cur: {**cur, "tailscale_last": now})
        return now
    last = _settings().get("tailscale_last")
    return [ip for ip in last if isinstance(ip, str)] if isinstance(last, list) else []


# Tailscale's own command line, asked read-only (`status --json`, ~40 ms, 19 Oct): the App Store and
# standalone apps carry it inside, Homebrew's sits on its own. No API key, no account of ours.
TAILSCALE_CLI = ("/Applications/Tailscale.app/Contents/MacOS/Tailscale", "/opt/homebrew/bin/tailscale",
                 "/usr/local/bin/tailscale")
TAILSCALE_APP = "/Applications/Tailscale.app"


def _ask_tailscale(cli: str) -> tuple[int, str]:
    try:
        # The app's binary with no TERM (as launchd starts the lab) doesn't act as a command line and hangs to the
        # timeout: every Setup, This Mac and Phone page waited 5 s (5 Nov).
        r = subprocess.run([cli, "status", "--json"], capture_output=True, text=True, timeout=5,
                           env={**os.environ, "TERM": os.environ.get("TERM") or "dumb"})
        return r.returncode, r.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _when(iso: str | None) -> float | None:
    """Go's RFC 3339 time ("2026-10-14T16:18:24.1Z", up to 9 decimals) as epoch seconds; its zero time is None."""
    if not iso or iso.startswith("0001-"):
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(re.sub(r"(\.\d{1,6})\d*", r"\1", iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def tailscale_status(ask=None, exists=os.path.exists, ips=None) -> dict:
    """What Tailscale on this Mac says about itself, read-only: installed, running, signed in, its address, and the
    iPhones (and iPads) in the same tailnet. `ips` is ifconfig's answer, trusted when the command line can't be asked."""
    cli = next((c for c in TAILSCALE_CLI if exists(c)), None)
    ips = tailscale_ips() if ips is None else ips
    out = {"installed": bool(cli) or exists(TAILSCALE_APP), "running": False, "state": None, "ips": ips,
           "name": None, "account": None, "phones": [], "asked": False}
    code, text = (ask or _ask_tailscale)(cli) if cli else (1, "")
    try:
        st = json.loads(text) if code == 0 else None
    except ValueError:
        st = None
    if not isinstance(st, dict):  # not running (or not asked): an address on a tunnel still means it is
        return {**out, "running": bool(ips), "state": "Running" if ips else None}
    me = st.get("Self") or {}
    state = st.get("BackendState")
    # Stopped, it still lists the addresses it was given though no interface carries them (19 Oct): not reachable.
    v4 = [i for i in st.get("TailscaleIPs") or [] if "." in i] if state == "Running" else []
    out.update(asked=True, running=True, state=state, ips=v4, name=me.get("HostName") or None,
               account=(st.get("CurrentTailnet") or {}).get("Name") or None)
    out["phones"] = [{"name": p.get("HostName") or "iPhone", "online": bool(p.get("Online")),
                      "last_seen": _when(p.get("LastSeen"))}
                     for p in (st.get("Peer") or {}).values() if p.get("OS") == "iOS"]
    return out


TAILSCALE_GET = {"mac": "https://tailscale.com/download/mac", "iphone": "https://tailscale.com/download/ios"}


def away_step(ts: dict, enabled: bool, devices: list[dict], ca_renewed_at: float | None) -> dict:
    """The next single thing to do so the app reaches this Mac away from home, in plain words: Tailscale on this
    Mac, signed in and on, then on the iPhone with the same account, then the app paired and trusting this CA."""
    def step(key: str, do: str, link: str | None = None) -> dict:
        return {"step": key, "do": do, "link": link}
    state, acct = ts.get("state"), ts.get("account")
    same = f" ({acct})" if acct else ""
    if not ts.get("installed"):
        return step("install_mac", "Install Tailscale on this Mac from tailscale.com, open it and sign in. It's free "
                    "for personal use; sign in with Google, Apple, Microsoft or GitHub.", TAILSCALE_GET["mac"])
    if not ts.get("running"):
        # Also what a launchd-started lab sees with the app open but disconnected (4 Nov): its CLI isn't answered then
        return step("open_mac", "Open Tailscale on this Mac (it's in Applications) and turn it on: click its icon in the "
                    "menu bar, then Connect.")
    if state in ("NeedsLogin", "NoState", "InUseOtherUser"):
        return step("sign_in", "Sign in to Tailscale on this Mac: click its icon in the menu bar, then Log in. Use the "
                    "account you'll use on your iPhone.")
    if state == "NeedsMachineAuth":
        return step("approve", "Approve this Mac in Tailscale's admin console (Machines): your tailnet asks for each "
                    "new device to be approved.", "https://login.tailscale.com/admin/machines")
    if state == "Stopped":
        return step("turn_on", "Turn Tailscale on: click its icon in the menu bar, then Connect.")
    if not ts.get("ips"):
        return step("starting", "Tailscale is starting on this Mac. Check again in a moment.")
    if not ts.get("phones") and ts.get("asked"):  # not asked: only ifconfig spoke, so the iPhone can't be seen
        return step("install_phone", f"Install Tailscale on your iPhone from the App Store and sign in with the same "
                    f"account as this Mac{same}.", TAILSCALE_GET["iphone"])
    if not devices:
        return step("pair", "Connect the Octo app with the code on the Phone page: it learns this Mac's Tailscale address "
                    "from it.")
    if not enabled:
        return step("switch_on", 'Switch on "Allow my phone" at the top of the Phone page, so the lab listens on Tailscale.')
    if ca_renewed_at and all(d["paired_at"] < ca_renewed_at for d in devices):
        return step("scan_again", "Scan the code on the Phone page again in the Octo app: this Mac's certificate was "
                    "renewed so it can vouch for Tailscale, and the app still trusts the old one.")
    seen = max((d.get("away_at") or 0 for d in devices), default=0)
    if not seen:
        return step("try_away", "Try it: on your iPhone, turn Wi-Fi off, check Tailscale is on, and open Lab in the "
                    "Octo app. This page shows when it got through.")
    return step("ready", "Ready: the Octo app reaches this Mac away from home, while the Mac is awake.")


def away_setup(ts: dict, enabled: bool, devices: list[dict], ca_renewed_at: float | None) -> dict:
    """Set up Octo's step 8, for the Setup page, home's card and `octo doctor` alike (4 Nov): done once a paired
    phone has reached the lab through Tailscale, and it stays done; while that way is off or broken now, `note` says
    what to do rather than the tick going. Not yet done: `do` is the next single thing."""
    nxt = away_step(ts, enabled, devices, ca_renewed_at)
    seen = max((d.get("away_at") or 0 for d in devices), default=0)
    if not seen:
        return {"done": False, "do": nxt["do"], "link": nxt["link"], "note": None, "seen": None}
    return {"done": True, "do": None, "link": nxt["link"] if nxt["step"] != "ready" else None, "seen": seen,
            "note": None if nxt["step"] == "ready" else f"Not reachable away from home right now. {nxt['do']}"}


_away_written: dict[str, float] = {}


def seen_away(device_id: str) -> None:
    """A paired phone reached the lab over Tailscale: kept per phone, at most once a minute (not a write per request)."""
    now = time.time()
    if _away_written.get(device_id, 0) > now - 60:
        return
    _away_written[device_id] = now
    _update(lambda cur: {**cur, "away_seen": {**(cur.get("away_seen") or {}), device_id: now}})


def mac_name() -> str:
    """The Mac's name as its owner sees it ("Sam's MacBook Air"), for the app."""
    try:
        name = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True).stdout.strip()
    except OSError:
        name = ""
    return name or "Your Mac"


# ---- certificates --------------------------------------------------------------------------

def _run(*args: str) -> None:
    r = subprocess.run([OPENSSL, *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"openssl {args[0]} failed: {r.stderr.strip()}")


# The CA's version: 2 allows Tailscale's 100.64.0.0/10 too (8 Oct). A version-1 CA (no Tailscale) is kept
# until the Mac has a Tailscale address; then a new one is made, and a phone that trusted the old one in
# Safari installs the new one (the app pins whatever the QR code says).
CA_VERSION = 2


def ca_version() -> int:
    try:
        return int(json.loads((DIR / "ca.json").read_text()).get("version", 1))
    except (OSError, ValueError):
        return 1 if (DIR / "ca.crt").exists() else 0


def ensure_certs(names: list[str] | None = None, ips: list[str] | None = None) -> dict:
    """Make the CA once, and the server certificate whenever the names change or it's near expiry."""
    names = hostnames() if names is None else names
    ips = (addresses() + tailscale_ips()) if ips is None else ips
    DIR.mkdir(parents=True, exist_ok=True)
    ca_key, ca_crt = DIR / "ca.key", DIR / "ca.crt"
    needs_tailnet = any(ipaddress.ip_address(i) in TAILNET for i in ips)
    renewed = False
    if not ca_crt.exists() or (needs_tailnet and ca_version() < 2):
        renewed = ca_crt.exists()
        cnf = DIR / "ca.cnf"
        # Name constraints (M43): once trusted, this CA can vouch only for .local names, localhost,
        # private addresses and Tailscale's, never for a real website; and it lasts 2 years, not 10.
        cnf.write_text("[req]\ndistinguished_name=dn\n[dn]\n[v3_ca]\nbasicConstraints=critical,CA:TRUE\n"
                       "keyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n"
                       "nameConstraints=critical,permitted;DNS:local,permitted;DNS:localhost,"
                       "permitted;IP:10.0.0.0/255.0.0.0,permitted;IP:172.16.0.0/255.240.0.0,"
                       "permitted;IP:192.168.0.0/255.255.0.0,permitted;IP:127.0.0.0/255.0.0.0,"
                       "permitted;IP:100.64.0.0/255.192.0.0\n")
        _run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(ca_key), "-out", str(ca_crt),
             "-days", "730", "-subj", "/CN=Octo on this Mac", "-config", str(cnf), "-extensions", "v3_ca")
        ca_key.chmod(0o600)
        (DIR / "ca.json").write_text(json.dumps({"version": CA_VERSION, "made": time.time()}))
        (DIR / "server.json").unlink(missing_ok=True)  # a new CA: a new server certificate too
    if renewed:
        _set("ca_renewed_at", time.time())
    if ca_version() < 2:  # a version-1 CA can't vouch for a Tailscale address: leave those out
        ips = [i for i in ips if ipaddress.ip_address(i) not in TAILNET]
    sans = sorted(names) + sorted(ips)
    stamp = DIR / "server.json"
    key, crt = DIR / "server.key", DIR / "server.crt"
    current = json.loads(stamp.read_text()) if stamp.exists() else {}
    # iOS accepts at most 825 days for certificates from a user-installed CA; 397 is well inside.
    if current.get("sans") != sans or current.get("expires", 0) < time.time() + 30 * 86400 or not crt.exists():
        ext = DIR / "server.ext"
        alt = ",".join([f"DNS:{n}" for n in sorted(names)] + [f"IP:{i}" for i in sorted(ips)] + ["DNS:localhost"])
        ext.write_text(f"basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
                       f"extendedKeyUsage=serverAuth\nsubjectAltName={alt}\n")
        csr = DIR / "server.csr"
        _run("req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(csr),
             "-subj", f"/CN={(names or ips or ['localhost'])[0]}")
        _run("x509", "-req", "-in", str(csr), "-CA", str(ca_crt), "-CAkey", str(ca_key), "-CAcreateserial",
             "-CAserial", str(DIR / "ca.srl"),  # by default OpenSSL cuts the CA's path at its first dot: octo/lab.srl
             "-out", str(crt), "-days", "397", "-sha256", "-extfile", str(ext))
        key.chmod(0o600)
        csr.unlink(missing_ok=True)
        stamp.write_text(json.dumps({"sans": sans, "expires": time.time() + 397 * 86400}))
    # The server certificate with the CA behind it: Safari ignores the extra one; the app finds the CA it
    # pinned (by SHA-256) in the handshake.
    chain = DIR / "server-chain.crt"
    chain.write_text(crt.read_text() + ca_crt.read_text())
    return {"ca": ca_crt, "key": key, "cert": chain, "names": names, "ips": ips}


def ca_sha256() -> str | None:
    """The SHA-256 of the CA certificate (DER), as the app pins it."""
    try:
        pem = (DIR / "ca.crt").read_text()
    except OSError:
        return None
    m = re.search(r"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", pem, re.S)
    return hashlib.sha256(base64.b64decode("".join(m.group(1).split()))).hexdigest() if m else None


def _set(key: str, value) -> None:
    _update(lambda cur: {**cur, key: value})


def _update(change) -> None:
    """Whole and under the lock (octo/files.py): showing the code and switching access on at once can't lose
    either. A damaged settings file is started afresh: it holds no keys, only whether access is on."""
    from . import files as FL
    try:
        FL.update_json(_settings_path(), change, {}, indent=None)
    except FL.Unreadable:
        FL.write_private(_settings_path(), json.dumps(change({})))


def pair_link(secret: str, hosts: list[str], port: int | None = None) -> str:
    """What the QR code says: everything the app needs to find this Mac, check it's this Mac (the CA's
    SHA-256) and pair once (the secret). Nothing long-lived: no token, no key, no Wi-Fi password."""
    from urllib.parse import urlencode, quote
    q = {"v": "1", "n": mac_name(), "h": ",".join(hosts), "p": str(port or PORT), "ca": ca_sha256() or "", "s": secret}
    return "octo://pair?" + urlencode(q, quote_via=quote)


# ---- pairing -------------------------------------------------------------------------------

class Pairing:
    """One code (Safari) and one secret (the app's QR code) at a time, in memory: a restart voids them."""

    def __init__(self) -> None:
        self.code: str | None = None
        self.expires = 0.0
        self.tries: dict[str, int] = {}  # wrong tries per address
        self.secret: str | None = None
        self.secret_expires = 0.0

    def new_secret(self) -> dict:
        self.secret = secrets.token_urlsafe(16)  # 128 bits: nobody types it
        self.secret_expires = time.time() + CODE_TTL_S
        return {"secret": self.secret, "expires_in": CODE_TTL_S}

    def use_secret(self, secret: str) -> bool:
        ok = bool(self.secret) and time.time() <= self.secret_expires and secrets.compare_digest(secret.strip(), self.secret or "")
        if ok or (self.secret and time.time() > self.secret_expires):
            self.secret = None  # once only
        return ok

    def new_code(self) -> dict:
        self.code = f"{secrets.randbelow(10 ** 8):08d}"
        self.expires = time.time() + CODE_TTL_S
        self.tries = {}
        return {"code": self.code, "expires_in": CODE_TTL_S}

    def use(self, code: str, who: str = "") -> bool:
        """`who`: the caller's address. Its own wrong tries shut it out, not everyone else."""
        if not self.code or time.time() > self.expires:
            self.code = None
            return False
        if self.tries.get(who, 0) >= CODE_TRIES:
            return False
        if secrets.compare_digest(code.strip(), self.code):
            self.code = None  # once only
            return True
        self.tries[who] = self.tries.get(who, 0) + 1
        if sum(self.tries.values()) >= CODE_TOTAL_TRIES:
            self.code = None
        return False


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def add_device(conn, name: str) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO devices (id, token_hash, name, paired_at) VALUES (?, ?, ?, ?)",
                 (new_id("dev"), _hash(token), name[:60] or "Phone", time.time()))
    return token


def device_for(conn, token: str | None) -> dict | None:
    if not token:
        return None
    d = row(conn, "SELECT * FROM devices WHERE token_hash=?", _hash(token))
    if d and (d["last_used_at"] or 0) < time.time() - 60:  # not a database write on every request (audit 14 Oct)
        conn.execute("UPDATE devices SET last_used_at=? WHERE id=?", (time.time(), d["id"]))
    return d


def devices(conn) -> list[dict]:
    away = _settings().get("away_seen") or {}
    return [{"id": d["id"], "name": d["name"], "paired_at": d["paired_at"], "last_used_at": d["last_used_at"],
             "away_at": away.get(d["id"])} for d in rows(conn, "SELECT * FROM devices ORDER BY paired_at")]


def unpair(conn, device_id: str) -> bool:
    return conn.execute("DELETE FROM devices WHERE id=?", (device_id,)).rowcount > 0
