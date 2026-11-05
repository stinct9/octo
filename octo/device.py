"""The recorder (L3): set it up, talk to it over home Wi-Fi, and pull its audio into the lab.

The device records 60 s chunks to its SD card (firmware/recorder/recorder.ino). `sync`:
  1. tells it the time (it has no clock battery; the manifest maps its uptime to real time)
  2. reads each boot's manifest: one signed JSON line per chunk with its SHA-256
  3. fetches new chunks, checks each against that hash, stores it safely, then deletes it on the device
  4. joins pulled chunks into recordings for the pipeline

Where recordings are cut: only after three chunks in a row with no speech. find_conversations
treats a 3-minute silence as the end of a conversation, so a cut there never splits one. Chunks with
no speech at all are deleted straight away: a day on a desk shouldn't sit on the Mac.

Security: requests are signed with a key only the Mac and the device have (data/device.json and the
gitignored firmware/recorder/secrets.h); every request uses a one-time nonce from the device, and the
device's answers are signed too. Plain HTTP on the home network: the audio itself isn't encrypted in
transit (see docs/conversation-lab-plan.md §3).
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import hmac
import http.client
import json
import os
import random
import re
import secrets
import subprocess
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from contextlib import contextmanager
from typing import Callable

from . import AUDIO, DATA, HOME, RATE, ROOT
from . import files as FL
from .db import row, rows, tx
from .marks import wall_clock  # noqa: F401  (here until 5 Nov; marks and fillers read it there, risk 8)

CONFIG = DATA / "device.json"
STATUS = DATA / "device_status.json"
SECRETS_H = ROOT / "firmware" / "recorder" / "secrets.h"
WIFI_SECRETS = ROOT / "firmware" / "bench" / "03_wifi" / "secrets.h"
RELEASE_FW = ROOT / "firmware" / "recorder" / "release"
# The filler cue (B20, rec-24): the owner's model, trained on their voice, kept with the lab's data and copied into
# the build (gitignored there); a recorder built without it has no filler cue.
FILLER_MODEL = DATA / "filler" / "octo_filler_model.h"
FILLER_H = ROOT / "firmware" / "recorder" / "octo_filler_model.h"  # a release's prebuilt firmware (scripts/release.sh)
CHUNKS = AUDIO / "device"
SILENT_RUN = 3     # chunks without speech that end a recording (= find_conversations' 3-minute gap)
MAX_CHUNKS = 240   # 4 hours: cut anyway, or a TV on all evening would never let a recording finish
MAX_HOLE_MS = 60_000  # a hole between chunks longer than this starts a new run instead of silence

Log = Callable[[str], None]


class DeviceError(RuntimeError):
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code  # the device's own error code, e.g. "no_chunk"


class Unreachable(DeviceError):
    """Not on the network: away from home, switched off, or out of range."""


# ---- setup --------------------------------------------------------------------------------

def _c_string(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def load_config() -> dict | None:
    try:
        return json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        return None


def update_config(change) -> dict:
    """Read data/device.json, apply `change(cfg)` and write it back, under the lock. A file that exists but
    doesn't read is never written over: it holds the recorder's key and enc_private, and without those the
    chunks still on the card can't be read (review 8 Oct, finding 2)."""
    try:
        return FL.update_json(CONFIG, change, {})
    except FL.Unreadable:
        raise DeviceError(f"{CONFIG} can't be read: not changing it (it holds the recorder's keys)") from None


def setup(new_key: bool = False) -> bool:
    """Write the recorder's secrets.h (Wi-Fi from the bench's 03_wifi/secrets.h, plus the signing
    key) and keep the key in data/device.json. Returns True if a new key was made, which means the
    recorder needs flashing again. Never prints either secret."""
    try:
        wifi = WIFI_SECRETS.read_text()
    except OSError:
        raise DeviceError(f"{WIFI_SECRETS.relative_to(ROOT)} not found: fill in the Wi-Fi details there first")
    ssid = re.search(r'#define\s+WIFI_SSID\s+"((?:[^"\\]|\\.)*)"', wifi)
    password = re.search(r'#define\s+WIFI_PASS\s+"((?:[^"\\]|\\.)*)"', wifi)
    if not ssid or not password or ssid.group(1) == "your-network":
        raise DeviceError(f"no Wi-Fi details in {WIFI_SECRETS.relative_to(ROOT)}")
    made = False

    def change(cfg: dict) -> None:
        nonlocal made
        made = new_key or not cfg.get("key")
        if made:
            cfg["key"] = secrets.token_hex(32)
        # The chunk-encryption key pair (M41): the private half stays here; the recorder gets the public half.
        if not cfg.get("enc_private"):
            from .devicecrypto import keypair
            cfg["enc_private"] = keypair()[0].hex()
            made = True
        cfg.setdefault("host", "octo-rec.local")
    cfg = update_config(change)
    from .devicecrypto import BASE, x25519
    pub = x25519(bytes.fromhex(cfg["enc_private"]), BASE)
    # The bench file holds C string literals already; copy them as they are.
    FL.write_private(SECRETS_H, "#pragma once\n// Written by `octo device setup`. Gitignored: never commit.\n"
                   f'#define WIFI_SSID "{ssid.group(1)}"\n#define WIFI_PASS "{password.group(1)}"\n'
                   f"#define OCTO_KEY {_c_string(cfg['key'])}\n"
                   "// The Mac's public key for chunk encryption; only the Mac has the private half.\n"
                   "#define OCTO_ENC_PUBKEY {" + ",".join(f"0x{b:02x}" for b in pub) + "}\n")
    return made


# ---- pairing and networks (rec-12, product plan 4.4) -------------------------------------------
#
# A recorder from rec-12 keeps its settings in its own flash: the key it shares with this Mac, this Mac's
# public key for encrypting chunks, and up to five Wi-Fi networks (firmware/recorder/octo_settings.h).
# Pairing writes the keys over USB, so it needs the recorder in hand; networks can then change over USB,
# over Wi-Fi (signed, like every request) or from the phone app over Bluetooth. A build with secrets.h
# (`octo device setup`, the owner's) still takes its key and network from there.

MAX_SSID, MIN_PASS, MAX_PASS = 32, 8, 63


def keys(new_key: bool = False, save: bool = True) -> tuple[str, str, dict]:
    """The signing key and this Mac's public key, as hex, and the config that holds them: made once and kept
    in data/device.json (the private half of the encryption key stays there; only this Mac can read the
    chunks). With save=False a new key isn't kept yet: pairing keeps it once the recorder has taken it."""
    from .devicecrypto import BASE, keypair, x25519

    def change(cfg: dict) -> None:
        if new_key or not cfg.get("key"):
            cfg["key"] = secrets.token_hex(32)
        if not cfg.get("enc_private"):
            cfg["enc_private"] = keypair()[0].hex()
        cfg.setdefault("host", "octo-rec.local")
    if save:
        cfg = update_config(change)
    else:
        cfg = load_config()
        if cfg is None and CONFIG.exists():
            raise DeviceError(f"{CONFIG} can't be read: not pairing (it holds the recorder's keys)")
        cfg = dict(cfg or {})
        change(cfg)
    return cfg["key"], x25519(bytes.fromhex(cfg["enc_private"]), BASE).hex(), cfg


def check_network(ssid: str, password: str) -> None:
    if not ssid or len(ssid.encode()) > MAX_SSID:
        raise DeviceError("a network name of 1 to 32 bytes")
    if password and not MIN_PASS <= len(password) <= MAX_PASS:
        raise DeviceError("a Wi-Fi password of 8 to 63 characters, or none for an open network")


def net_command(op: str, ssid: str = "", password: str = "") -> str:
    """The recorder's network command (octo_settings.h): names and passwords travel as hex."""
    if op == "list":
        return "list"
    if op == "add":
        check_network(ssid, password)
        return f"add {ssid.encode().hex()} {password.encode().hex()}"
    if op in ("del", "remove"):
        return f"del {ssid.encode().hex()}"
    raise DeviceError(f"unknown network command: {op}")


def _names(answer: dict) -> list[str]:
    return [bytes.fromhex(h).decode(errors="replace") for h in answer.get("networks_hex", [])]


def usb_signature(key: str | None, what: str) -> str:
    """rec-16 (review 8 Oct, C16): a re-pairing or an update over USB from the Mac the recorder knows carries
    HMAC(key, what) after its fields; without it the recorder asks someone holding it to allow it."""
    import hmac
    return " " + hmac.new(bytes.fromhex(key), what.encode(), hashlib.sha256).hexdigest() if key else ""


def _say_hold(log: Log) -> Callable[[dict], None]:
    def on_data(d: dict) -> None:
        if d.get("confirm") == "hold":
            log("the recorder asks first: hold its button for 2 seconds, within 30 seconds, to allow this")
    return on_data


def pair_usb(board, new_key: bool = False, log: Log = print) -> dict:
    """Pair the recorder on USB with this Mac: it gets the signing key and this Mac's public key. Its
    status after, as it reports it (paired, its networks)."""
    prompt = board.run(b"P", timeout=5)
    if prompt.get("send") != "pair":
        raise DeviceError("this recorder's firmware can't be paired from the Mac: it needs rec-12 or later (octo device flash)")
    # Signed with the key the recorder has now (rec-16 asks): ours, unless it was paired with another Mac.
    old = (load_config() or {}).get("key")
    # Kept only once the recorder has taken it: before 8 Oct a pairing that failed left this Mac with a key
    # the recorder didn't have, and every Wi-Fi call failed after (review 8 Oct, C15).
    key, pub, cfg = keys(new_key, save=False)
    line = f"{key} {pub}"
    if prompt.get("signed"):
        line += usb_signature(old, f"PAIR\n{line}")
    done = board.run(f"{line}\n".encode(), timeout=45, on_data=_say_hold(log))
    if done.get("error") == "not_confirmed":
        raise DeviceError("the recorder wasn't allowed to pair: nobody held its button (it was paired with another Mac)",
                          "not_confirmed")
    if not done.get("paired"):
        raise DeviceError(f"the recorder didn't take the pairing ({done.get('error') or 'no reason given'})")
    update_config(lambda c: c.update({k: cfg[k] for k in ("key", "enc_private", "host")}))
    learn_host(done)
    return done


def networks_usb(board, cmd: str) -> list[str]:
    prompt = board.run(b"N", timeout=5)
    if prompt.get("send") != "net":
        raise DeviceError("this recorder's firmware keeps one network: it needs rec-12 or later")
    done = board.run((cmd + "\n").encode(), timeout=10)
    if not done.get("ok"):
        raise DeviceError("the recorder refused that change")
    return _names(done)


def release_firmware(name: str, folder: Path | None = None) -> Path:
    """One of a release's prebuilt firmware files (open-source plan I5), checked against the release's SHA256SUMS
    first: a maker without the Arduino toolchain flashes these, and a damaged or swapped file must not reach a board."""
    folder = folder or RELEASE_FW
    try:
        sums = dict(reversed(line.split(maxsplit=1)) for line in (folder / "SHA256SUMS").read_text().splitlines() if line.strip())
        data = (folder / name).read_bytes()
    except (OSError, ValueError) as e:
        raise DeviceError(f"no prebuilt firmware here ({folder}): install arduino-cli to build it, or use a release of Octo") from e
    if hashlib.sha256(data).hexdigest() != sums.get(name, "").strip():
        raise DeviceError(f"{name} doesn't match the release's SHA256SUMS: download the release again")
    return folder / name


def install_firmware(port: str, image: Path, run=subprocess.run) -> None:
    """A board that has never run Octo (or won't start): the whole flash image at 0x0 with the chip's own loader,
    through esptool. Nothing of ours runs during it, so there's no screen and no going back; then pair it."""
    import sys
    r = run([sys.executable, "-m", "esptool", "--chip", "esp32s3", "--port", port, "write-flash", "0x0", str(image)],
            capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        tail = " ".join((r.stdout + r.stderr).strip().splitlines()[-2:])
        raise DeviceError(f"esptool couldn't write the firmware: {tail} (if it keeps failing: hold B, tap R, let go of B, "
                          "and try again)")


class OldFirmware(DeviceError):
    """A recorder from before rec-12: one network, built into its firmware."""


def firmware_number(fw: str | None) -> int:
    """12 for "rec-12"; 0 if it isn't a recorder version."""
    m = re.match(r"rec-(\d+)", fw or "")
    return int(m.group(1)) if m else 0


def networks_wifi(dev: "Device", cmd: str) -> list[str]:
    # Before rec-12 there's no /networks: its "not found" isn't signed, which would read as "not our
    # recorder?" (8 Oct). Ask its version first, signed, and say what's going on.
    fw = dev.call("/status").get("fw")
    if firmware_number(fw) < 12:
        raise OldFirmware(f"this recorder runs {fw}: one network, built into its firmware. rec-12 keeps up to "
                          "five (octo device flash)")
    done = dev.call("/networks", {"cmd": cmd})
    if not done.get("ok"):
        raise DeviceError("the recorder refused that change")
    return _names(done)


def networks(cmd: str) -> list[str]:
    """List or change the recorder's networks: over Wi-Fi when it's reachable, else over USB."""
    try:
        return networks_wifi(Device.from_config(), cmd)
    except OldFirmware:
        raise
    except (DeviceError, OSError) as wifi_error:
        from bench.device import BoardError, connect
        try:
            with connect("recorder") as board:
                return networks_usb(board, cmd)
        except BoardError:
            raise DeviceError(f"the recorder isn't reachable on Wi-Fi ({wifi_error}) or on USB") from None


def developer_build() -> bool:
    """This Mac builds the recorder with secrets.h: its key and network come from there, not from pairing."""
    return SECRETS_H.exists()


# ---- talking to it ------------------------------------------------------------------------

def unit_host(st: dict | None) -> str | None:
    """The recorder's own name on the network (rec-16, review 8 Oct finding 12): "octo-a1b2.local", from its status's
    "device". None before rec-16, which called every recorder octo-rec."""
    dev = (st or {}).get("device") or ""
    return f"{dev}.local" if re.fullmatch(r"octo-[0-9a-f]{4}", dev) else None


def learn_host(st: dict | None) -> None:
    """Remember the name a recorder answers to once its status gives one (rec-16): after an update to it, the old
    shared name finds nothing."""
    host = unit_host(st)
    cfg = load_config()
    if host and cfg is not None and cfg.get("host") != host:
        try:
            update_config(lambda c: c.update(host=host))
        except DeviceError:
            pass


def resolve(host: str) -> str | None:
    """IPv4 address for a .local name. Homebrew's Python can't do mDNS lookups itself (getaddrinfo
    spends ~30 s and fails, while curl and /usr/bin/python3 succeed), so ask macOS's resolver."""
    try:
        out = subprocess.run(["dscacheutil", "-q", "host", "-a", "name", host], capture_output=True, text=True,
                             timeout=8).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"^ip_address: (\d+\.\d+\.\d+\.\d+)$", out, re.M)
    return m.group(1) if m else None


# The login item can't reach the recorder itself: macOS's Local Network privacy answers "No route to
# host" to Homebrew's Python started by launchd (5 Oct; octo/relay.py says why). Then requests go
# through the relay, a LaunchAgent under Apple's Python on 127.0.0.1 (`octo login-item install`).
RELAY = ("127.0.0.1", 8767)
# The relay's key (review 8 Oct, finding 17): beside its copy, written by `octo login-item install`.
RELAY_KEY = Path.home() / "Library" / "Application Support" / "octo" / "relay.key"


def relay_headers() -> dict:
    try:
        return {"X-Octo-Relay-Key": RELAY_KEY.read_text().strip()}
    except OSError:
        return {}  # the relay says to run octo login-item install


class RelayDown(Exception):
    """The relay isn't running (not installed, or the Mac just logged in)."""


# Tries per signed request. The recorder keeps one nonce per address, so every request from this Mac shares
# it: the lab's 30 s check, a sync, a command in a terminal. Two at once take each other's, and with one
# retry each a Wi-Fi update died at 78% when the lab's check came in (8 Oct). Retries wait a random moment.
NONCE_TRIES = 4


class Device:
    def __init__(self, host: str, key: str, timeout: float = 20.0, ip: str | None = None):
        self.host, self.key, self.timeout = host, bytes.fromhex(key), timeout
        self.nonce: str | None = None
        self.relayed = False  # this process is blocked from the recorder: every request through the relay
        self.ip = ip or (host if re.fullmatch(r"[\d.]+", host) else None)

    def _address(self) -> str:
        if not self.ip:
            for host in self._hosts():
                self.ip = resolve(host)
                if self.ip:
                    if host != self.host:  # it changed name (rec-16): remember the one it answers to
                        self.host = host
                        try:
                            update_config(lambda c: c.update(host=host))
                        except DeviceError:
                            pass
                    break
            if not self.ip:
                raise Unreachable(f"recorder not found on the network ({self.host}): away, off, or out of range")
            cfg = load_config()
            if cfg is not None and cfg.get("ip") != self.ip:  # never written after a read that failed
                try:
                    update_config(lambda c: c.update(ip=self.ip))
                except DeviceError:
                    pass
        return self.ip

    def _hosts(self) -> list[str]:
        """The name it's remembered by, then its own (rec-16) as its last status gives it, then the old shared one."""
        last = (last_status() or {}).get("status")
        return list(dict.fromkeys(h for h in (self.host, unit_host(last), "octo-rec.local") if h))

    @classmethod
    def from_config(cls) -> "Device":
        cfg = load_config()
        if not cfg:
            raise DeviceError("no recorder paired: plug it into this Mac and run `octo device pair`")
        return cls(cfg["host"], cfg["key"])  # the remembered IP is only a fallback: DHCP can move it

    def sign(self, text: str) -> str:
        return hmac.new(self.key, text.encode(), hashlib.sha256).hexdigest()

    def _http(self, method: str, path: str, data: bytes | None = None, headers: dict | None = None):
        if self.relayed:
            return self._via_relay(method, path, data, headers)
        req = urllib.request.Request(f"http://{self._address()}{path}", data=data, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()
        except http.client.HTTPException as e:  # e.g. cut short: the device's card read failed mid-file
            self.nonce = None
            raise DeviceError(f"{path}: transfer broke off ({type(e).__name__})") from e
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            if getattr(reason, "errno", None) == errno.EHOSTUNREACH:  # blocked, or really gone: try the relay
                try:
                    out = self._via_relay(method, path, data, headers)
                    self.relayed = True
                    return out
                except RelayDown:
                    self.ip = None
                    raise Unreachable(f"recorder not reachable at {self.host}: {reason} (if a terminal reaches it, "
                                      "macOS is blocking this process from the local network and the relay isn't "
                                      "running: octo login-item install)") from e
            self.ip = None  # look it up again next time
            raise Unreachable(f"recorder not reachable at {self.host}: {reason}") from e

    def _via_relay(self, method: str, path: str, data: bytes | None, headers: dict | None):
        req = urllib.request.Request(f"http://{RELAY[0]}:{RELAY[1]}{path}", data=data, method=method,
                                     headers={**(headers or {}), "X-Octo-To": self._address(), **relay_headers()})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout + 15) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            why = e.headers.get("X-Octo-Relay-Error")
            if not why:
                return e.code, e.headers, e.read()  # the recorder's own answer
            if "IncompleteRead" in why:
                self.nonce = None
                raise DeviceError(f"{path}: transfer broke off (IncompleteRead)") from e
            self.ip = None
            raise Unreachable(f"recorder not reachable at {self.host} (through the relay): {why}") from e
        except (urllib.error.URLError, OSError) as e:
            raise RelayDown() from e

    def hello(self) -> str:
        status, _, body = self._http("GET", "/hello")
        if status != 200:
            raise DeviceError(f"/hello answered {status}")
        self.nonce = json.loads(body)["nonce"]
        return self.nonce

    def call(self, path: str, body: dict | None = None, raw: bool = False):
        """A signed request. JSON answers must be signed by the device; raw ones (chunk audio) are
        checked by the caller against the manifest's hash."""
        text = json.dumps(body or {}, separators=(",", ":"))
        for attempt in range(NONCE_TRIES):
            if attempt:  # out of step with whoever took the nonce, or both sides keep taking each other's
                time.sleep(random.uniform(0.1, 0.6))
            nonce = self.nonce or self.hello()
            self.nonce = None
            status, headers, data = self._http("POST", path, text.encode(), {
                "Content-Type": "application/json", "X-Octo-Nonce": nonce,
                "X-Octo-Sig": self.sign(f"POST\n{path}\n{nonce}\n{text}")})
            if status == 401 and attempt < NONCE_TRIES - 1:
                continue  # expired, or another request from this Mac took it: a fresh one
            if status == 401:
                raise DeviceError(f"the recorder rejected our request {NONCE_TRIES} times: another device on the Wi-Fi may "
                                  "be asking it for nonces, or it isn't flashed with this Mac's key (octo device flash)")
            self.nonce = headers.get("X-Octo-Next")
            if raw and status == 200:
                return data
            answer = data.decode()
            if not hmac.compare_digest(headers.get("X-Octo-Sig") or "", self.sign(f"RESP\n{path}\n{nonce}\n{answer}")):
                raise DeviceError(f"{path}: answer not signed with our key -- not our recorder?")
            if status != 200:
                code = json.loads(answer).get("error", str(status))
                raise DeviceError(f"{path}: {code}", code)
            return answer if path == "/manifest" else json.loads(answer)
        raise AssertionError("unreachable")


def filler_model() -> bool:
    """The lab's filler model into the recorder's build, or out of it when the lab has none (never in git)."""
    if FILLER_MODEL.exists():
        FILLER_H.write_bytes(FILLER_MODEL.read_bytes())
        return True
    FILLER_H.unlink(missing_ok=True)
    return False


def set_filler(dev: "Device", on: bool, threshold: float | None = None) -> dict:
    """The filler cue on or off (B20), and its threshold if given; the recorder keeps both. Refused ("no_model")
    by firmware built without the owner's model."""
    body: dict = {"on": on}
    if threshold is not None:
        if not 0.5 <= threshold < 1.0:
            raise ValueError("threshold: 0.5 to 0.999")
        body["threshold"] = threshold
    return dev.call("/filler", body)


def send_filler_model(dev: "Device", blob: bytes, log: Log = print, wait_s: float = 60.0, wait=time.sleep) -> dict:
    """The filler cue's model as data (rec-27, B29; filler_loop.pack): in signed pieces as a firmware update's, into
    the recorder's slot not in use; it checks it, keeps the one before for Undo, and restarts to load it. Returns its
    status once it answers with the new one; raises if it didn't take it (a bad model goes back by itself)."""
    import base64
    st = dev.call("/status")
    if not st.get("filler_models"):
        raise DeviceError(f"the recorder's firmware ({st.get('fw')}) takes no models as data: update it to rec-27 or later")
    sha = hashlib.sha256(blob).hexdigest()
    try:
        dev.call("/filler/model/begin", {"size": len(blob), "sha256": sha})
    except DeviceError as e:
        if e.code == "recording":
            raise DeviceError("recording is on: switch it off first (hold its button 1 s)", "recording") from None
        raise
    for off in range(0, len(blob), UPDATE_PIECE):
        dev.call("/filler/model/data", {"offset": off, "data": base64.b64encode(blob[off:off + UPDATE_PIECE]).decode()})
    dev.call("/filler/model/finish", {})
    log("sent; the recorder is restarting with it")
    now = _back(dev, st.get("boot"), wait_s, wait)
    if now.get("filler_reverted") or now.get("filler_sha") != hashlib.sha256(blob[80:]).hexdigest()[:16]:
        raise DeviceError("the recorder didn't start with the new model and went back to the one before")
    return now


def undo_filler_model(dev: "Device", log: Log = print, wait_s: float = 60.0, wait=time.sleep) -> dict:
    """The model before back (rec-27): the recorder swaps its slots and restarts. Returns its status after."""
    boot = dev.call("/status").get("boot")
    dev.call("/filler/model/undo", {})
    log("the recorder is restarting with the model before")
    return _back(dev, boot, wait_s, wait)


def _back(dev: "Device", boot, wait_s: float, wait) -> dict:
    """The recorder's status once it answers from its next boot: it answers on the old one for the second before it
    restarts (23 Oct, on the chip: the first wait took that answer for the new one)."""
    ip = getattr(dev, "ip", None)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        wait(3)
        if ip and not dev.ip:
            dev.ip = ip
        try:
            now = dev.call("/status")
        except DeviceError:
            continue
        if now.get("boot") != boot:
            return now
    raise Unreachable("the recorder didn't come back within a minute")


def set_recording(dev: "Device", on: bool, wait=time.sleep) -> dict:
    """Switch recording on or off; returns the status to show. The recorder answers a few ms
    before its writer task starts capturing, so "on" first comes back as "starting" -- and the
    chip said "Recorder starting" until the next sync, 5 min later (2 Oct). Ask once more."""
    st = dev.call("/recording", {"on": on})
    if on and st.get("state") == "starting":
        wait(1.0)
        st = dev.call("/status")
    return st


def save_status(st: dict | None, error: str | None = None) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    prev = last_status() or {}
    out = {"checked_at": time.time(), "error": error,
           "status": st if st is not None else prev.get("status"),
           "seen_at": time.time() if st is not None else prev.get("seen_at")}
    FL.write_private(STATUS, json.dumps(out), 0o644)
    from .filler_loop import note_cue_state  # the cue's switch over time, for its live numbers (B22)
    note_cue_state(st)
    learn_host(st)  # a sync and a check finishing at once each write it whole


def last_status() -> dict | None:
    try:
        return json.loads(STATUS.read_text())
    except (OSError, ValueError):
        return None


# ---- manifests ----------------------------------------------------------------------------

def parse_manifest(text: str) -> dict:
    """Chunks the device has closed (or repaired after a power cut), with what's known about each.
    Lines cut off by a power cut are skipped."""
    info, clock, opens, chunks = {}, None, {}, {}
    for line in text.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if "boot" in d:
            info = d
        elif "clock_ms" in d:
            clock = (d["clock_ms"], d["at_ms"])  # the latest one is the best
        elif "open" in d:
            opens[d["open"]] = d
        elif "seq" in d:
            o = opens.get(d["seq"], {})
            chunks[d["seq"]] = {"seq": d["seq"], "seg": d.get("seg", o.get("seg")),
                                "start_ms": d.get("start_ms", o.get("start_ms")), "samples": d["samples"],
                                "sha256": d.get("sha256"), "dropped": d.get("dropped"), "repaired": d.get("repaired", False)}
    return {"info": info, "clock": clock, "chunks": [chunks[k] for k in sorted(chunks)]}


# ---- sync ---------------------------------------------------------------------------------

def _store_chunk(path: Path, pcm: bytes) -> None:
    """Write a chunk with a correct WAV header and make sure it's on disk before the device deletes it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    with open(tmp, "rb+") as f:
        # macOS: plain fsync only reaches the drive's cache; F_FULLFSYNC is what makes it survive a
        # power cut, and it must, because the card's copy is deleted next (review 4 Oct, M42).
        fcntl.fcntl(f.fileno(), getattr(fcntl, "F_FULLFSYNC", 51))
    os.replace(tmp, path)
    d = os.open(path.parent, os.O_RDONLY)  # and the rename itself
    try:
        os.fsync(d)
    finally:
        os.close(d)


def speech_seconds() -> Callable[[Path], float]:
    """Seconds of speech in a chunk, by the same detector and settings find_conversations uses."""
    from silero_vad import load_silero_vad

    from . import audio as A
    from .stages import STAGES, detect_speech, params_for

    model, params = load_silero_vad(), params_for(STAGES["find_conversations"])
    return lambda path: round(sum(b - a for a, b in detect_speech(A.read(path), params, model)), 1)


def lazy_speech() -> Callable[[Path], float]:
    """speech_seconds, loaded on the first chunk: a sync that finds the recorder away (most of
    them) no longer loads torch every 5 minutes (review 4 Oct, M42)."""
    fn: list[Callable[[Path], float]] = []
    return lambda path: (fn or fn.append(speech_seconds()) or fn)[0](path)


class Busy(DeviceError):
    """Another sync (the web app's, or one in a terminal) is already running."""


SYNC_WAIT_S = 30  # how long a sync waits for a status check to let go of the recorder


def _lock_path(conn) -> Path:
    return Path(conn.execute("PRAGMA database_list").fetchone()[2] + ".device.lock")


def _take(lock, wait_s: float) -> bool:
    deadline = time.time() + wait_s
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.time() >= deadline:
                return False
            time.sleep(0.25)


@contextmanager
def holding(conn, wait_s: float = 10.0):
    """The recorder to ourselves for a signed call or an update (review 8 Oct, finding 7). It keeps one nonce per
    address, shared by every process on this Mac, so a call racing the lab's sync or 30 s check takes the other's
    nonce; four tries narrowed that, this removes it. sync() waits for it, check() skips while it's held."""
    with open(_lock_path(conn), "w") as lock:
        if not _take(lock, wait_s):
            raise Busy("the recorder is busy with a sync or an update: try again in a minute")
        yield


def sync(conn, dev: Device, log: Log = print, speech: Callable[[Path], float] | None = None) -> dict:
    """Pull every new chunk, delete what's safely stored, then join chunks into recordings.
    One at a time: two would fetch the same chunks, and the device serves one client at a time.
    Waits a little for the lock: the web app's status check (check()) holds it for a moment."""
    with open(_lock_path(conn), "w") as lock:
        if not _take(lock, SYNC_WAIT_S):
            raise Busy("another sync with the recorder is running; it will pick this up")
        return _sync(conn, dev, log, speech)


def check(conn, dev: Device | None = None) -> dict | None:
    """Between syncs, ask the recorder how it is (the web app, every 30 s), so the header shows a button
    press or the recorder leaving within a minute, not at the next sync 5 min later (5 Oct).
    Only /status: nothing is pulled or written to the card. Skips while a sync has the recorder."""
    with open(_lock_path(conn), "w") as lock:
        if not _take(lock, 0):
            return None
        try:
            st = (dev or Device.from_config()).call("/status")
        except Unreachable as e:
            save_status(None, str(e))
            return None
        save_status(st)
        return st


def _sync(conn, dev: Device, log: Log, speech: Callable[[Path], float] | None) -> dict:
    try:
        st = dev.call("/status")
    except Unreachable as e:
        save_status(None, str(e))
        # Away from home the recorder can't say a stretch is over; audio already on the Mac shouldn't
        # wait for it to come back (review 4 Oct, M04: 40 chunks from 2 Oct waited two days).
        if made := assemble(conn, idle_segs(conn), log):
            log(f"{len(made)} recording(s) made from audio already on the Mac")
        raise
    dev.call("/clock", {"epoch_ms": int(time.time() * 1000)})
    save_status(st)
    pulled = deleted = bad = 0
    finished: set[tuple[int, int]] = set()
    for b in sorted(dev.call("/boots"), key=lambda b: b["boot"]):
        boot = b["boot"]
        known = row(conn, "SELECT * FROM device_boots WHERE boot=?", boot)
        if known and b.get("boot_id") and parse_manifest(known["manifest"])["info"].get("boot_id") not in (None, b["boot_id"]):
            log(f"boot {boot} on the card is a new boot that reused the number; the earlier one is kept apart")
            _retire_boot(conn, boot)
            known = None
        if not known:
            conn.execute("INSERT INTO device_boots (boot) VALUES (?)", (boot,))
            known = {"boot": boot, "manifest": "", "removed_at": None}
        text = known["manifest"]
        # One bad boot folder mustn't stop the others, the clean-up or the joining (review 4 Oct, M40):
        # a folder with no manifest (a power cut mid-delete, or right after boot) counts as empty.
        try:
            while page := dev.call("/manifest", {"boot": boot, "from": len(text.encode())}):
                text += page
        except Unreachable:
            raise
        except DeviceError as e:
            log(f"boot {boot}: {e}; skipped" + ("" if e.code == "no_manifest" else ", will retry"))
            continue
        conn.execute("UPDATE device_boots SET manifest=? WHERE boot=?", (text, boot))
        from .marks import from_manifest
        if n := from_manifest(conn, boot, text):  # B16: double taps (rec-26)
            log(f"boot {boot}: {n} moment(s) marked")
        m = parse_manifest(text)
        key = boot_key(m["info"])
        for c in m["chunks"]:
            have = row(conn, "SELECT * FROM device_chunks WHERE boot=? AND seq=?", boot, c["seq"])
            if c["samples"] == 0 and c["repaired"] and not have:
                # A card error as the chunk opened: the recorder dropped the empty file and went on in
                # the next one from the same moment. Recorded as missing, so the recording is joined
                # across it; skipped, it split one (8 Oct, 21:52: chunk 87 of boot 49).
                conn.execute(
                    "INSERT INTO device_chunks (boot, seq, seg, start_ms, samples, sha256, pulled_at, "
                    "deleted_on_device_at, recording_id) VALUES (?, ?, ?, ?, 0, '', ?, ?, 'missing')",
                    (boot, c["seq"], c["seg"], c["start_ms"], time.time(), time.time()))
                continue
            if not c["samples"] or not c["sha256"]:
                continue
            if not have:
                pcm, gone = None, False
                for _ in range(2):  # the card occasionally fails a read too (see plan §0, L3)
                    try:
                        data = dev.call("/chunk", {"boot": boot, "seq": c["seq"]}, raw=True)
                    except Unreachable:
                        raise
                    except DeviceError as e:
                        gone = e.code == "no_chunk"
                        if gone:
                            break
                        continue
                    pcm = data[44:44 + c["samples"] * 2]
                    if len(pcm) == c["samples"] * 2 and hashlib.sha256(pcm).hexdigest() == c["sha256"]:
                        if key:  # checked as stored, then decrypted (M41)
                            from .devicecrypto import crypt_at
                            pcm = crypt_at(key, boot, c["seq"], 44, pcm)
                        break
                    pcm = None
                if gone:  # listed, but no longer on the card (another copy of the lab pulled it, or a new card)
                    log(f"boot {boot} chunk {c['seq']}: no longer on the card; skipped")
                    conn.execute(
                        "INSERT INTO device_chunks (boot, seq, seg, start_ms, samples, sha256, pulled_at, "
                        "deleted_on_device_at, recording_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'missing')",
                        (boot, c["seq"], c["seg"], c["start_ms"], c["samples"], c["sha256"], time.time(), time.time()))
                    continue
                if pcm is None:
                    tries = _bad_try(conn, boot, c["seq"], c["seg"])
                    if tries >= MAX_BAD_TRIES:  # not re-downloaded forever: joined around as silence
                        conn.execute("DELETE FROM device_bad_chunks WHERE boot=? AND seq=?", (boot, c["seq"]))
                        log(f"boot {boot} chunk {c['seq']}: never arrived intact in {tries} tries; given up, "
                            f"left on the card, {c['samples'] / RATE:.0f} s of silence in its place")
                        conn.execute(
                            "INSERT INTO device_chunks (boot, seq, seg, start_ms, samples, sha256, pulled_at, "
                            "deleted_on_device_at, recording_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'missing')",
                            (boot, c["seq"], c["seg"], c["start_ms"], c["samples"], c["sha256"], time.time(), time.time()))
                        continue
                    log(f"boot {boot} chunk {c['seq']}: didn't arrive intact twice ({tries} of {MAX_BAD_TRIES}); "
                        "left on the device, will retry")
                    bad += 1
                    continue
                path = CHUNKS / f"b{boot:05d}" / f"s{c['seq']:05d}.wav"
                _store_chunk(path, pcm)
                conn.execute("DELETE FROM device_bad_chunks WHERE boot=? AND seq=?", (boot, c["seq"]))
                conn.execute(
                    "INSERT INTO device_chunks (boot, seq, seg, start_ms, samples, sha256, speech_s, path, pulled_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (boot, c["seq"], c["seg"], c["start_ms"], c["samples"], c["sha256"],
                     speech(path) if speech else None, str(path.relative_to(HOME)), time.time()))
                pulled += 1
                have = {"deleted_on_device_at": None}
            if not have["deleted_on_device_at"]:
                if not _deleted(dev, boot, c["seq"], log):
                    continue  # the card refused: it stays, and the next sync tries again
                conn.execute("UPDATE device_chunks SET deleted_on_device_at=? WHERE boot=? AND seq=?",
                             (time.time(), boot, c["seq"]))
                deleted += 1
        # A stretch of recording is over unless it's the one the device is still writing.
        recording_now = (boot == st["boot"] and st["state"] == "recording")
        finished |= {(boot, c["seg"]) for c in m["chunks"] if not (recording_now and c["seg"] == st["seg"])}
    # Finished boots: once no chunk files are left on the card, remove the directory.
    for b in dev.call("/boots"):
        if b["boot"] != st["boot"] and b["chunk_files"] == 0:
            if (dev.call("/delete", {"boot": b["boot"]}) or {}).get("deleted") is False:
                log(f"boot {b['boot']}: the card wouldn't remove its folder; will retry")
                continue
            conn.execute("UPDATE device_boots SET removed_at=? WHERE boot=?", (time.time(), b["boot"]))
    made = assemble(conn, finished, log)
    return {"pulled": pulled, "deleted_on_device": deleted, "bad": bad, "recordings": made, "status": st}


def boot_key(info: dict) -> bytes | None:
    """The chunk key of an encrypted boot (its first manifest line has "epk"), or None for a plain one."""
    if not info.get("epk"):
        return None
    cfg = load_config() or {}
    if not cfg.get("enc_private"):
        raise DeviceError("this boot is encrypted, but data/device.json has no enc_private key to read it")
    from .devicecrypto import boot_key as derive
    return derive(bytes.fromhex(cfg["enc_private"]), bytes.fromhex(info["epk"]))


def _retire_boot(conn, boot: int) -> None:
    """Move an earlier boot's rows out of the way of a new boot with the same number (a new board,
    or flash erased). Its chunks keep their files and recordings; only the number changes."""
    n = 1
    while row(conn, "SELECT 1 FROM device_boots WHERE boot=?", boot + 1_000_000 * n):
        n += 1
    conn.execute("UPDATE device_boots SET boot=? WHERE boot=?", (boot + 1_000_000 * n, boot))
    conn.execute("UPDATE device_chunks SET boot=? WHERE boot=?", (boot + 1_000_000 * n, boot))
    conn.execute("UPDATE marks SET boot=? WHERE boot=?", (boot + 1_000_000 * n, boot))  # B16
    conn.execute("DELETE FROM device_bad_chunks WHERE boot=?", (boot,))


MAX_BAD_TRIES = 6  # syncs in a row a chunk failed its checksum before it's given up


def _bad_try(conn, boot: int, seq: int, seg: int | None) -> int:
    conn.execute("INSERT INTO device_bad_chunks (boot, seq, seg, tries) VALUES (?, ?, ?, 1) "
                 "ON CONFLICT(boot, seq) DO UPDATE SET tries = tries + 1", (boot, seq, seg))
    return row(conn, "SELECT tries FROM device_bad_chunks WHERE boot=? AND seq=?", boot, seq)["tries"]


def _deleted(dev: Device, boot: int, seq: int, log: Log) -> bool:
    """Delete a stored chunk from the card; True once it's gone. The card can refuse (deleted:false,
    after a write error), and before 5 Oct that was recorded as done. The firmware also says false
    for a file that's already gone, so a refusal is checked by asking for the chunk."""
    if (dev.call("/delete", {"boot": boot, "seq": seq}) or {}).get("deleted") is not False:
        return True
    try:
        dev.call("/chunk", {"boot": boot, "seq": seq}, raw=True)
    except DeviceError as e:
        if e.code == "no_chunk":
            return True
        if isinstance(e, Unreachable):
            raise
    log(f"boot {boot} chunk {seq}: stored on the Mac, but the card wouldn't delete it; will retry")
    return False


def idle_segs(conn, now: float | None = None, idle_s: float = 3600) -> set[tuple[int, int]]:
    """Stretches whose newest chunk reached the Mac over an hour ago: treated as finished while the
    recorder is away. A later chunk of the same stretch just starts a new recording."""
    now = time.time() if now is None else now
    return {(r["boot"], r["seg"]) for r in rows(
        conn, "SELECT boot, seg, max(pulled_at) last FROM device_chunks WHERE recording_id IS NULL "
              "GROUP BY boot, seg HAVING last < ?", now - idle_s)}


# ---- updating the firmware over Wi-Fi ---------------------------------------------------------

UPDATE_PIECE = 8192  # bytes of firmware per signed request (base64 in JSON)


def firmware_version(source: Path | None = None) -> str:
    """The FW string the recorder's source declares, e.g. "rec-2"."""
    text = (source or SECRETS_H.parent / "recorder.ino").read_text()
    m = re.search(r'const char \*FW = "([^"]+)"', text)
    return m.group(1) if m else "unknown"


def update_over_wifi(dev: Device, image: bytes, log: Log = print, wait_s: float = 120.0) -> dict:
    """Send a compiled firmware image over the home Wi-Fi, in signed pieces, with its SHA-256 in
    the signed first request. The recorder writes it to its spare slot, switches only if the hash
    matches, restarts, and keeps it once this Mac reaches it with a signed request (here, the
    /status after the restart); otherwise it goes back to the firmware before. Recording must be off."""
    import base64
    st = dev.call("/status")
    before = st.get("slot")  # rec-13 and later say which slot runs: the new firmware goes in the other one
    if not str(st.get("fw", "")).startswith("rec-") or st.get("fw") == "rec-1":
        raise DeviceError("the recorder's firmware can't take updates over Wi-Fi: flash it once over USB")
    try:
        dev.call("/update/begin", {"size": len(image), "sha256": hashlib.sha256(image).hexdigest()})
    except DeviceError as e:
        if e.code == "recording":
            raise DeviceError(RECORDING_ON, "recording") from None
        raise
    for off in range(0, len(image), UPDATE_PIECE):
        dev.call("/update/data", {"offset": off, "data": base64.b64encode(image[off:off + UPDATE_PIECE]).decode()})
        if off // UPDATE_PIECE % 16 == 0:
            log(f"sent {off * 100 // len(image)}%")
    ip = getattr(dev, "ip", None)  # where it answered just now
    dev.call("/update/finish", {})
    log("sent 100%; the recorder is restarting on the new firmware")
    deadline = time.time() + wait_s
    while time.time() < deadline:
        time.sleep(5)
        # The same address while it restarts (DHCP keeps its lease): rec-16 comes back under its own name
        # (finding 12), which the old one can't find. The answer is signed, so it's still ours.
        if ip and not dev.ip:
            dev.ip = ip
        try:
            now = dev.call("/status")  # a signed request: the recorder keeps the new firmware
        except DeviceError:
            continue
        learn_host(now)
        # Back on the slot it started on: the new firmware didn't start and the bootloader went back, even
        # when the version is the same (re-flashing rec-N; review 8 Oct, C14).
        now["went_back"] = bool(before and now.get("slot") == before)
        # rec-16 keeps it only once it works (review 8 Oct, finding 13): give it half a minute, asking again.
        for _ in range(10):
            if not now.get("pending") or now["went_back"]:
                break
            time.sleep(3)
            if ip and not dev.ip:
                dev.ip = ip
            try:
                now = {**dev.call("/status"), "went_back": now["went_back"]}
            except Unreachable:
                pass
        return now
    raise Unreachable("the recorder didn't come back within 2 minutes; if it doesn't answer within 10, "
                      "it returns to the firmware it had before")


# ---- updating the firmware over USB (rec-13) --------------------------------------------------
#
# The same update as over Wi-Fi, through the recorder's own code: the spare slot, the SHA-256 checked,
# its screen showing how far and how it ended, and the firmware before kept to go back to. Until rec-13
# a USB flash went through the chip's own loader (esptool, `octo device flash --direct` now): nothing of
# ours ran during it, so the screen couldn't show anything, and a firmware that didn't start stayed.

USB_UPDATE_FW = 13     # the first firmware that updates itself over USB
BACK_ON_WIFI_S = 60.0  # after a USB update, how long the recorder has to rejoin Wi-Fi (if it was on it)
RECORDING_ON = "recording is on: switch it off first (octo device off, or hold its button for 2 s), then flash again"
UPDATE_REFUSED = {
    "recording": RECORDING_ON,
    "busy": "an update over Wi-Fi is already under way",
    "no_room": "the firmware doesn't fit the recorder's spare slot",
    "sha256": "the firmware arrived but didn't match its SHA-256, so nothing changed",
    "write_failed": "the recorder couldn't write its flash, so nothing changed",
    "finish_failed": "the recorder found the firmware invalid, so nothing changed",
    "not_confirmed": "the recorder wasn't allowed to update: nobody held its button (this Mac's key isn't its key)",
}


def _refused(code: str | None) -> DeviceError:
    return DeviceError(UPDATE_REFUSED.get(code or "", f"the recorder stopped the update ({code or 'no reason given'})"), code)


def _next_boot(board, boot: int, wait_s: float, sleep: Callable[[float], None]) -> dict:
    """The recorder's status from its next boot. A restart from its own code keeps USB up, so the port
    stays open; if it went away all the same it's opened again, which doesn't restart the board."""
    from bench.device import BoardError, PortLost
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        sleep(0.5)
        try:
            st = board.run(b"s", timeout=1.5)
        except PortLost:
            try:
                board.reopen(wait=max(1.0, deadline - time.monotonic()))
            except BoardError:
                pass
            continue
        except BoardError:  # no answer yet: still starting
            continue
        if st.get("boot") != boot:
            return st
    raise DeviceError(f"the recorder didn't answer on USB within {wait_s:.0f} s of restarting; it goes back to the "
                      "firmware before by itself unless this Mac reaches it within 10 minutes")


def update_over_usb(board, image: bytes, log: Log = print, wait_s: float = 60.0, wifi_s: float = BACK_ON_WIFI_S,
                    sleep: Callable[[float], None] = time.sleep) -> dict:
    """Send a compiled firmware image to the recorder over USB, through its own update code (rec-13 on).

    It writes its spare slot, the screen showing how far, switches only if the SHA-256 matches and
    restarts on the new firmware, which is on trial until kept. This Mac keeps it (K) once it answers on
    USB and, if the recorder was on Wi-Fi before, once it's back on Wi-Fi; if it isn't within a minute,
    the Mac sends it back to the firmware before (X). One that doesn't start goes back by itself.
    Recording must be off. Returns the recorder's status on the firmware it keeps."""
    import base64
    before = board.run(b"s", timeout=10)
    if firmware_number(before.get("fw")) < USB_UPDATE_FW:
        raise OldFirmware(f"this recorder runs {before.get('fw')}, which can't update itself over USB (rec-13 can)")
    prompt = board.run(b"U", timeout=5)
    if prompt.get("send") != "update":
        raise DeviceError("the recorder didn't take the update command")
    head = f"{len(image)} {hashlib.sha256(image).hexdigest()}"
    if prompt.get("signed"):  # rec-16: from this Mac, signed; otherwise its button has to allow it
        head += usb_signature((load_config() or {}).get("key"), f"UPDATE\n{head}")
    ok = board.run(f"{head}\n".encode(), timeout=45, on_data=_say_hold(log))
    if not ok.get("accepted"):
        raise _refused(ok.get("error"))
    piece, off, resent, said = int(ok["piece"]), 0, 0, 0
    while off < len(image):
        part = image[off:off + piece]
        ans = board.run(f"{off} {base64.b64encode(part).decode()}\n".encode(), timeout=15)
        if ans.get("error") in ("data", "offset") and resent < 3:  # carry on from what it has
            resent += 1
            off = int(ans.get("done", off))
            continue
        if ans.get("error") or "done" not in ans:
            raise _refused(ans.get("error"))
        off = int(ans["done"])
        if off * 10 // len(image) > said:
            said = off * 10 // len(image)
            log(f"sent {said * 10}%")
    ans = board.run(b"end\n", timeout=30)
    if not ans.get("restarting"):
        raise _refused(ans.get("error"))
    log("the recorder is restarting on the new firmware")
    st = _next_boot(board, before["boot"], wait_s, sleep)
    if st.get("slot") == before.get("slot"):
        raise DeviceError(f"the new firmware didn't start properly, so the recorder went back to {st.get('fw')}",
                          "rolled_back")
    if st.get("pending") and before.get("wifi"):
        log("waiting for it to rejoin Wi-Fi before keeping it")
        deadline = time.monotonic() + wifi_s
        while st.get("pending") and not st.get("wifi") and time.monotonic() < deadline:
            sleep(2.0)
            st = board.run(b"s", timeout=10)
        if st.get("pending") and not st.get("wifi"):
            board.run(b"X", timeout=5)
            back = _next_boot(board, st["boot"], wait_s, sleep)
            raise DeviceError(f"{st.get('fw')} started but didn't rejoin Wi-Fi within {wifi_s:.0f} s, so the recorder "
                              f"went back to {back.get('fw')}", "not_back_on_wifi")
    learn_host(st)
    if st.get("pending"):  # (not if the lab's check over Wi-Fi reached it first: that keeps it too)
        st = board.run(b"K", timeout=10)
        if st.get("pending"):  # rec-16 keeps only a firmware that works (review 8 Oct, finding 13): send it back
            board.run(b"X", timeout=5)
            back = _next_boot(board, st["boot"], wait_s, sleep)
            raise DeviceError(f"{st.get('fw')} started, but its {_fault(st)}, so the recorder went back to {back.get('fw')}",
                              "not_working")
    return st


def _fault(st: dict) -> str:
    return {"mic_error": "microphone didn't start", "card_error": "card failed"}.get(st.get("state"), "checks failed")


# ---- chunks → recordings ------------------------------------------------------------------

def plan_cuts(chunks: list[dict], finished: bool) -> list[tuple[str, list[dict]]]:
    """Decide what to do with consecutive chunks of one stretch of recording.

    Returns ("recording", chunks) and ("silent", chunks) decisions, in order. Chunks not mentioned
    wait for more audio: a conversation may still be going on."""
    out: list[tuple[str, list[dict]]] = []
    cur: list[dict] = []
    quiet: list[dict] = []
    for c in chunks:
        if (c["speech_s"] or 0) > 0:
            cur += quiet + [c]
            quiet = []
        elif not cur:
            out.append(("silent", [c]))
        else:
            quiet.append(c)
            if len(quiet) >= SILENT_RUN:
                out += [("recording", cur), ("silent", quiet)]
                cur, quiet = [], []
        if len(cur) >= MAX_CHUNKS:
            out.append(("recording", cur))
            cur = []
    if finished:
        if cur:
            out.append(("recording", cur))
        if quiet:
            out.append(("silent", quiet))
    return out


def hole_ms(prev: dict, c: dict) -> int:
    """Time between the end of one chunk and the start of the next that has no audio: the device
    lost a little after a card error, or dropped samples when the card stalled."""
    if prev["start_ms"] is None or c["start_ms"] is None:
        return 0
    return max(0, round(c["start_ms"] - prev["start_ms"] - prev["samples"] * 1000 / RATE))


def _runs(chunks: list[dict], missing: dict[tuple[int, int], int] | None = None) -> list[list[dict]]:
    """Split into stretches of consecutive chunks (same boot and seg, no long hole). A seq that's
    missing for good (gone from the card, or never arrived intact: `missing`, boot/seq → samples) is
    bridged: the hole it leaves becomes silence, as for a card error. Any other missing seq splits."""
    missing = missing or {}
    out: list[list[dict]] = []
    for c in sorted(chunks, key=lambda c: (c["boot"], c["seq"])):
        p = out[-1][-1] if out else None
        between = range(p["seq"] + 1, c["seq"]) if p else range(0)
        lost_ms = sum(missing.get((c["boot"], s), 0) for s in between) * 1000 // RATE
        if (p and (p["boot"], p["seg"]) == (c["boot"], c["seg"])
                and all((c["boot"], s) in missing for s in between)
                and hole_ms(p, c) - lost_ms <= MAX_HOLE_MS):
            out[-1].append(c)
        else:
            out.append([c])
    return out


def assemble(conn, finished: set[tuple[int, int]], log: Log = print) -> list[str]:
    from . import audio as A
    from .pipeline import add_recording

    waiting = rows(conn, "SELECT * FROM device_chunks WHERE recording_id IS NULL AND speech_s IS NOT NULL")
    missing = {(r["boot"], r["seq"]): r["samples"] or 0 for r in rows(
        conn, "SELECT boot, seq, samples FROM device_chunks WHERE recording_id='missing'")}
    runs = _runs(waiting, missing)
    retrying = {(r["boot"], r["seg"]) for r in rows(conn, "SELECT boot, seg FROM device_bad_chunks")}
    made = []
    for i, run in enumerate(runs):
        # A run is complete if its stretch has ended and nothing after it in the stretch is missing.
        nxt = runs[i + 1][0] if i + 1 < len(runs) else None
        same_seg_later = nxt and (nxt["boot"], nxt["seg"]) == (run[0]["boot"], run[0]["seg"])
        # Nor while a chunk of its stretch is still being retried: it may yet fill the gap.
        done = (run[0]["boot"], run[0]["seg"]) in finished - retrying and not same_seg_later
        for kind, cs in plan_cuts(run, done):
            if kind == "silent":
                with tx(conn):
                    for c in cs:
                        conn.execute("UPDATE device_chunks SET recording_id='silent', path=NULL WHERE boot=? AND seq=?",
                                     (c["boot"], c["seq"]))
                for c in cs:
                    (HOME / c["path"]).unlink(missing_ok=True)
                continue
            boot = cs[0]["boot"]
            clock = parse_manifest((row(conn, "SELECT manifest FROM device_boots WHERE boot=?", boot) or {}).get("manifest", ""))["clock"]
            joined = CHUNKS / f"join_b{boot:05d}_s{cs[0]['seq']:05d}.wav"
            with wave.open(str(joined), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                for j, c in enumerate(cs):
                    if j and (gap := hole_ms(cs[j - 1], c)) > 20:
                        w.writeframes(b"\0\0" * (gap * RATE // 1000))  # keep the timeline true
                    with wave.open(str(HOME / c["path"])) as r:
                        w.writeframes(r.readframes(r.getnframes()))
            with tx(conn):
                rec = add_recording(conn, joined, "device", started_at=wall_clock(clock, cs[0]["start_ms"]),
                                    original=f"recorder boot {boot}, chunks {cs[0]['seq']}–{cs[-1]['seq']}")
                for c in cs:
                    conn.execute("UPDATE device_chunks SET recording_id=?, path=NULL WHERE boot=? AND seq=?",
                                 (rec, c["boot"], c["seq"]))
            joined.unlink(missing_ok=True)
            for c in cs:
                (HOME / c["path"]).unlink(missing_ok=True)
            log(f"recording {rec}: {A.duration(HOME / row(conn, 'SELECT path FROM recordings WHERE id=?', rec)['path']) / 60:.0f} min "
                f"from boot {boot}" + ("" if clock else " (time unknown: the device lost power before reaching home Wi-Fi)"))
            made.append(rec)
    return made
