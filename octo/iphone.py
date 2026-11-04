"""`octo phone install`: build the Octo iPhone app with this Mac's Xcode and put it on an iPhone, in one command
(beta, 30 Oct: each tester builds it with their own Apple ID, so nobody else's account is needed).

A free Apple ID signs an install for 7 days: run it again within the week. Each Apple ID team needs its own bundle
ID (Apple keeps one per team), so the app is `app.octo.recorder.<team>` unless --bundle-id says otherwise; the first
lab's own install is `app.octo.recorder` (the first lab's team registered it first).

Steps: Xcode found → the team signed in to Xcode → the iPhone (by cable or on this Wi-Fi, unlocked, Developer Mode on)
→ the pages built and copied in (Capacitor) → xcodebuild signs it, registering the phone with the team if needed →
devicectl installs it → when the install stops opening.
"""

from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import DATA, ROOT

MOBILE = ROOT / "mobile"
XCODE = Path("/Applications/Xcode.app/Contents/Developer")
BUILT = Path.home() / "Library/Caches/octo-xcbuild"
PROFILES = Path.home() / "Library/Developer/Xcode/UserData/Provisioning Profiles"
OLD_PROFILES = Path.home() / "Library/Caches/octo-old-profiles"
INSTALLED = DATA / "iphone.json"  # the last install: phone, team, app ID, until when it opens
XCODE_STORE = "macappstore://apps.apple.com/app/xcode/id497799835"


class PhoneError(Exception):
    pass


def teams(xcode_defaults: dict) -> list[dict]:
    """The teams of the Apple IDs signed in to Xcode (Xcode → Settings → Accounts), from its preferences."""
    out = {}
    for account in (xcode_defaults.get("IDEProvisioningTeamByIdentifier") or {}).values():
        for t in account:
            out[t["teamID"]] = {"id": t["teamID"], "name": t.get("teamName", ""), "free": bool(t.get("isFreeProvisioningTeam"))}
    return list(out.values())


def pick_team(found: list[dict], wanted: str | None) -> dict:
    if wanted:
        t = next((t for t in found if t["id"] == wanted), None)
        if not t:
            raise PhoneError(f"Xcode isn't signed in to team {wanted}: Xcode → Settings → Accounts")
        return t
    if not found:
        raise PhoneError("Xcode isn't signed in to an Apple ID: open Xcode → Settings → Accounts, press +, sign in with "
                         "your Apple ID (a free one works), then run this again")
    if len(found) > 1:
        raise PhoneError("Xcode knows several teams; choose one with --team:\n"
                         + "\n".join(f"  {t['id']}  {t['name']}" for t in found))
    return found[0]


def bundle_id(team: str) -> str:
    return f"app.octo.recorder.{team.lower()}"


def iphones(devicectl: dict) -> list[dict]:
    """The iPhones this Mac knows, from `devicectl list devices --json-output`."""
    out = []
    for d in devicectl.get("result", {}).get("devices", []):
        hw, props, conn = d.get("hardwareProperties", {}), d.get("deviceProperties", {}), d.get("connectionProperties", {})
        if hw.get("platform") != "iOS":
            continue
        out.append({"id": d["identifier"], "udid": hw.get("udid"), "name": props.get("name", ""),
                    "paired": conn.get("pairingState") == "paired",
                    "reachable": conn.get("tunnelState") == "connected" or conn.get("transportType") == "wired",
                    "developer_mode": props.get("developerModeStatus") == "enabled"})
    return out


def pick_iphone(found: list[dict], wanted: str | None) -> dict:
    if wanted:
        found = [d for d in found if wanted in (d["id"], d["udid"], d["name"])]
        if not found:
            raise PhoneError(f"No iPhone called {wanted}: `xcrun devicectl list devices` lists them")
    elif len(found) > 1:
        found = [d for d in found if d["reachable"]] or found
        if len(found) > 1:
            raise PhoneError("Several iPhones; choose one with --device:\n" + "\n".join(f"  {d['name']}" for d in found))
    if not found:
        raise PhoneError("No iPhone found. Plug it into this Mac with a cable, unlock it and tap Trust (the first time), "
                         "then run this again")
    d = found[0]
    if not d["paired"]:
        raise PhoneError(f"{d['name']} isn't paired with this Mac: plug it in by cable, unlock it and tap Trust")
    if not d["developer_mode"]:
        raise PhoneError(f"Turn on Developer Mode on {d['name']}: Settings → Privacy & Security → Developer Mode (it "
                         "shows once the phone has been plugged into a Mac with Xcode), restart, confirm; then run this again")
    return d


def _run(cmd: list[str], cwd: Path | None = None, env: dict | None = None, quiet: bool = False) -> str:
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=quiet, text=True)
    if r.returncode != 0:
        tail = "\n".join(((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-15:]) if quiet else ""
        raise PhoneError(f"{' '.join(cmd[:3])} failed (exit {r.returncode})" + (f":\n{tail}" if tail else ""))
    return r.stdout or ""


def profile_of(path: Path) -> dict:
    """A provisioning profile's plist (it's signed CMS; `security cms -D` reads it)."""
    return plistlib.loads(subprocess.run(["security", "cms", "-D", "-i", str(path)], capture_output=True, check=True).stdout)


def set_aside_profiles(app_id: str) -> int:
    """Move this app's cached profiles aside, so xcodebuild makes a new one with a full 7 days: it reuses a cached one
    until it lapses (13 Oct)."""
    n = 0
    for p in PROFILES.glob("*.mobileprovision") if PROFILES.is_dir() else []:
        try:
            got = profile_of(p).get("Entitlements", {}).get("application-identifier", "")
        except (subprocess.CalledProcessError, plistlib.InvalidFileException):
            continue
        if got.split(".", 1)[-1] == app_id:
            OLD_PROFILES.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), OLD_PROFILES / p.name)
            n += 1
    return n


def install(device: str | None = None, team: str | None = None, bundle: str | None = None, say=print) -> dict:
    if not (XCODE / "usr/bin/xcodebuild").exists():
        raise PhoneError("Xcode isn't installed: get it from the App Store (free, about 12 GB), open it once and let it "
                         "install its iOS parts, sign in (Xcode → Settings → Accounts), then run this again")
    env = {**os.environ, "DEVELOPER_DIR": str(XCODE)}
    node = ROOT / ".node/bin"
    if node.is_dir():
        env["PATH"] = f"{node}:{env.get('PATH', '')}"
    if not shutil.which("npm", path=env["PATH"]):  # a release install has its pages built, so no Node yet
        _run([str(ROOT / "scripts/get_node.sh")], quiet=True)
        env["PATH"] = f"{node}:{env.get('PATH', '')}"

    xcode_prefs = plistlib.loads(_run(["defaults", "export", "com.apple.dt.Xcode", "-"], quiet=True).encode())
    t = pick_team(teams(xcode_prefs), team)
    app_id = bundle or bundle_id(t["id"])
    with tempfile.TemporaryDirectory() as tmp:
        _run(["xcrun", "devicectl", "list", "devices", "--quiet", "--json-output", f"{tmp}/d.json"], env=env, quiet=True)
        phone = pick_iphone(iphones(json.loads(Path(f"{tmp}/d.json").read_text())), device)
    say(f"Building Octo ({app_id}) for {phone['name']}, signed by {t['name']}")

    _run(["npm", "ci", "--no-audit", "--no-fund", "--silent"], cwd=MOBILE, env=env)
    _run(["npm", "run", "build", "--silent"], cwd=MOBILE, env=env, quiet=True)
    _run(["npx", "cap", "sync", "ios"], cwd=MOBILE, env=env, quiet=True)
    if set_aside_profiles(app_id):
        say("Set the last install's signing aside, for a full 7 days")
    say("Signing and building (a few minutes the first time)…")
    _run(["xcodebuild", "-project", "ios/App/App.xcodeproj", "-scheme", "App", "-configuration", "Debug",
          "-destination", f"id={phone['udid']}", "-derivedDataPath", str(BUILT),
          "-allowProvisioningUpdates", "-allowProvisioningDeviceRegistration",
          f"DEVELOPMENT_TEAM={t['id']}", f"PRODUCT_BUNDLE_IDENTIFIER={app_id}", "build"], cwd=MOBILE, env=env, quiet=True)
    app = BUILT / "Build/Products/Debug-iphoneos/App.app"
    expires = profile_of(app / "embedded.mobileprovision").get("ExpirationDate")
    say(f"Installing on {phone['name']}…")
    try:
        _run(["xcrun", "devicectl", "device", "install", "app", "--device", phone["id"], str(app)], env=env, quiet=True)
    except PhoneError as e:  # 4016 or a reset connection: the phone locked, or left the Wi-Fi (a retry often works)
        raise PhoneError(f"{e}\nKeep {phone['name']} unlocked, on this Mac's Wi-Fi or its cable, and run this again") from e
    got = {"phone": phone["name"], "device": phone["id"], "app_id": app_id, "team": t,
           "expires": expires.timestamp() if expires else None, "at": time.time()}
    INSTALLED.write_text(json.dumps(got))
    return got


def installed() -> dict | None:
    try:
        return json.loads(INSTALLED.read_text())
    except (OSError, ValueError):
        return None


# ---- the Setup page's iPhone step (beta, 31 Oct): each part checked before the next is shown -----------------------

def _env() -> dict:
    return {**os.environ, "DEVELOPER_DIR": str(XCODE)}


def _ok(cmd: list[str], timeout: float = 30) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_env())
    except (OSError, subprocess.SubprocessError):
        return None


def _phones() -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        r = _ok(["xcrun", "devicectl", "list", "devices", "--quiet", "--json-output", f"{tmp}/d.json"])
        try:
            return iphones(json.loads(Path(f"{tmp}/d.json").read_text())) if r and r.returncode == 0 else []
        except (OSError, ValueError):
            return []


def _teams() -> list[dict]:
    r = _ok(["defaults", "export", "com.apple.dt.Xcode", "-"])
    try:
        return teams(plistlib.loads(r.stdout.encode())) if r and r.returncode == 0 else []
    except (ValueError, plistlib.InvalidFileException):
        return []


STEPS = {  # in order; what to do, in plain words, for someone who has never used Xcode
    "xcode": ("Get Xcode", "Xcode is Apple's free app for making iPhone apps; Octo uses it to put its app on your "
              "phone, signed with your own Apple ID. Open the App Store, get Xcode (about 4 GB, it can take a while), "
              "then come back here."),
    "first_launch": ("Open Xcode once", "Open Xcode from Applications. Click Agree; when it asks which platforms you "
                     "want, keep iOS ticked and click Download (a few GB). When it's done you can quit Xcode."),
    "ios": ("Get Xcode's iOS part", "Open Xcode, then Xcode menu → Settings… → Components, and click Get next to iOS. "
            "Quit Xcode when it's done."),
    "apple_id": ("Sign in to Xcode", "Open Xcode, then Xcode menu → Settings… → Accounts. Click + at the bottom left, "
                 "choose Apple ID, and sign in with your Apple ID (the one on your iPhone is fine). It costs nothing."),
    "phone": ("Plug in your iPhone", "Connect your iPhone to this Mac with its charging cable and unlock it. If it asks "
              "“Trust This Computer?”, tap Trust and enter your iPhone's passcode."),
    "developer_mode": ("Turn on Developer Mode", "On your iPhone: Settings → Privacy & Security → scroll to the bottom → "
                       "Developer Mode → turn it on. The iPhone restarts; when it's back, tap Turn On and enter your "
                       "passcode. (It lets your own Mac put apps on your phone; you can turn it off after the beta.)"),
    "install": ("Put Octo on your iPhone", "Keep the iPhone plugged in and unlocked, then press the button. It takes a "
                "minute or two the first time."),
    "trust": ("Trust it on your iPhone", "On your iPhone: Settings → General → VPN & Device Management → under "
              "Developer App, tap your Apple ID → Trust → Trust. Then press Check again."),
}


def steps(trusted: bool | None = None) -> list[dict]:
    """The iPhone step's parts in order, each {id, title, do, done}, checked on this Mac until the first one not done
    (the rest aren't asked: the phone can't be seen before Xcode is there)."""
    have, seen = installed(), []
    phones = lambda: seen or seen.extend(_phones()) or seen  # noqa: E731  asked once (about a second)
    checks = [
        ("xcode", lambda: (XCODE / "usr/bin/xcodebuild").exists()),
        ("first_launch", lambda: (r := _ok(["xcodebuild", "-checkFirstLaunchStatus"])) is not None and r.returncode == 0),
        ("ios", lambda: (r := _ok(["xcodebuild", "-showsdks"])) is not None and "iphoneos" in r.stdout),
        ("apple_id", lambda: bool(_teams())),
        ("phone", lambda: any(p["paired"] for p in phones())),
        ("developer_mode", lambda: any(p["paired"] and p["developer_mode"] for p in phones())),
        ("install", lambda: bool(have and (have.get("expires") or 0) > time.time())),
        ("trust", lambda: bool(trusted if trusted is not None else (have or {}).get("trusted"))),
    ]
    out, blocked = [], False
    for key, check in checks:
        done = False if blocked else bool(check())
        title, do = STEPS[key]
        out.append({"id": key, "title": title, "do": do, "done": done})
        blocked = blocked or not done
    return out


def open_on_phone() -> tuple[bool, str]:
    """Open Octo on the iPhone: the one way to tell from the Mac that the phone trusts its developer. (True, "") when it
    opened; otherwise what devicectl said."""
    have = installed()
    if not have:
        return False, "not installed yet"
    r = _ok(["xcrun", "devicectl", "device", "process", "launch", "--device", have["device"], have["app_id"]], 60)
    text = ((r.stdout + r.stderr) if r else "devicectl didn't run").strip()
    ok = bool(r and r.returncode == 0)
    INSTALLED.write_text(json.dumps({**have, "trusted": ok}))
    if ok:
        return True, ""
    if "trust" in text.lower() or "profile" in text.lower():
        return False, "your iPhone doesn't trust it yet"
    return False, "the iPhone didn't answer: keep it unlocked and plugged in (or on this Mac's Wi-Fi)"


class Job:
    """One install at a time, in a thread, for the Setup page and the weekly renewal."""

    def __init__(self) -> None:
        self.running, self.lines, self.error, self.done_at = False, [], None, None
        self.lock = threading.Lock()

    def start(self, **kw) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running, self.lines, self.error = True, [], None
        threading.Thread(target=self._run, kwargs=kw, daemon=True, name="octo-iphone").start()
        return True

    def _run(self, **kw) -> None:
        try:
            install(say=self.lines.append, **kw)
            ok, why = open_on_phone()
            self.lines.append("Octo opened on your iPhone." if ok else f"Installed; {why}.")
        except PhoneError as e:
            self.error = str(e)
        except Exception as e:  # noqa: BLE001  shown on the page rather than lost in a thread
            self.error = f"{type(e).__name__}: {e}"
        finally:
            self.running, self.done_at = False, time.time()

    def state(self) -> dict:
        return {"running": self.running, "lines": self.lines[-8:], "error": self.error, "done_at": self.done_at}


JOB = Job()
RENEW_WITHIN_S = 2 * 86400  # a free Apple ID's install opens for 7 days: renewed by itself once 2 are left


def renew_due(have: dict | None, now: float) -> bool:
    return bool(have and have.get("expires") and have["expires"] - now < RENEW_WITHIN_S
                and now - (have.get("tried_at") or 0) > 6 * 3600)


def renew(job: Job, now: float | None = None) -> bool:
    """The weekly re-signing, so a free Apple ID's install never stops opening (31 Oct: the tester does it alone).
    Hourly from the server; when 2 days are left it installs again on the same phone, if the phone is around (plugged
    in, or on this Mac's Wi-Fi). Nothing else changes on the phone."""
    have, now = installed(), now or time.time()
    if (have and have.get("expires") and have["expires"] - now < 86400 and job.error and not job.running
            and now - (have.get("reminded_at") or 0) > 86400):  # renewing failed and a day is left: say so, once a day
        from . import notify as N
        N.post("Octo on your iPhone stops opening within a day: plug the iPhone into this Mac, or bring it onto the "
               "same Wi-Fi, so Octo can renew it", page="/setup")
        have = {**have, "reminded_at": now}
        INSTALLED.write_text(json.dumps(have))
    if not renew_due(have, now):
        return False
    INSTALLED.write_text(json.dumps({**have, "tried_at": now}))
    return job.start(device=have["device"], team=have["team"]["id"], bundle=have["app_id"])


def _day(t: float) -> str:
    return time.strftime("%-d %b", time.localtime(t))


def setup_step(have: dict | None, devices: list[dict], now: float | None = None) -> dict:
    """Set up Octo's step 6, for the Setup page, home's card and `octo doctor` alike (Jay, 4 Nov): done once there's
    evidence the app is on the phone, however it got there: this lab installed it and it was opened, or a phone is
    paired (only the app pairs: its QR code; the Safari code has had no button since phone v2), so an install from
    Xcode by hand counts. Evidence that went stale stays ticked and says so in `note`."""
    now = now or time.time()
    lab = bool(have and have.get("trusted"))
    used = max((d.get("last_used_at") or d.get("paired_at") or 0 for d in devices), default=0)
    if not lab and not devices:
        return {"done": False, "note": None}
    expires = (have or {}).get("expires") or 0
    if (lab and expires > now) or used > now - 7 * 86400:  # a free Apple ID's install opens for 7 days
        return {"done": True, "note": None}
    if lab and expires and expires >= used:
        return {"done": True, "note": f"It stopped opening on {_day(expires)}: plug the iPhone into this Mac, or bring it "
                                      "onto the same Wi-Fi, and Octo installs it again."}
    return {"done": True, "note": f"The app hasn't reached this lab since {_day(used)}. If it no longer opens on your "
                                  "iPhone, put it on again here."}
