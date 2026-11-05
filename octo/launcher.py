"""The Octo app in Applications (open-source plan F11; docs/mac-app-plan.md).

Since 19 Oct it's a small native Mac app (`macapp/Octo.swift`): the lab's pages in its own window, the octopus in the
menu bar with the lab's state, and the lab's banners posted as Octo. It's built here, on the owner's Mac, with
`swiftc` from Apple's command line tools and signed ad hoc, so it needs no Xcode project and no developer account,
and a locally built app carries no quarantine flag for Gatekeeper to check. It's only a shell: the lab is still the
server the com.octo.lab login item runs, and the app finds it (and its key) through that login item, so nothing
about the owner is written into the bundle.

Without swiftc it's the earlier one-line AppleScript app made by `osacompile` (it runs this copy's `octo open`).

It goes in /Applications, where Finder and Launchpad show it, when this user can write there without a password (an
admin) and no other user's Octo is there; otherwise in ~/Applications, which Finder doesn't show by default (19 Oct:
"i don't see the octo app").
"""

from __future__ import annotations

import hashlib
import os
import plistlib
import shutil
import signal
import subprocess
import time
from pathlib import Path

from . import ROOT, bin_path

SYSTEM = Path("/Applications") / "Octo.app"
USER = Path.home() / "Applications" / "Octo.app"
SOURCE = ROOT / "macapp"
ICON = ROOT / "mobile" / "ios" / "App" / "App" / "Assets.xcassets" / "AppIcon.appiconset" / "icon-1024.png"
SOURCES = ("Octo.swift", "FillerCue.swift")  # FillerCue.swift (B21) is the phone's too: its Xcode project refers to it
TARGET = "arm64-apple-macos14.0"  # doctor.MIN_MACOS
LSREGISTER = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/"
              "lsregister")


def place() -> Path:
    """Where a new app goes."""
    theirs = SYSTEM.exists() and SYSTEM.stat().st_uid != os.getuid()
    return SYSTEM if os.access(SYSTEM.parent, os.W_OK) and not theirs else USER


def found() -> Path | None:
    """This user's app, wherever it was made."""
    return next((p for p in (SYSTEM, USER) if p.exists() and p.stat().st_uid == os.getuid()), None)


def octo_bin() -> Path:
    return bin_path("octo")


def script(octo: Path) -> str:
    quoted = str(octo).replace("\\", "\\\\").replace('"', '\\"')  # an AppleScript string literal
    return f'do shell script quoted form of "{quoted}" & " open"'


def swiftc() -> str | None:
    """Apple's Swift compiler from the command line tools (or Xcode), or None. Only asked when a developer folder is
    set: on a Mac without the tools, /usr/bin's stubs would open Apple's installer instead of answering."""
    try:
        folder = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True, timeout=10).stdout.strip()
        if not folder or not Path(folder).is_dir():
            return None
        out = subprocess.run(["xcrun", "--find", "swiftc"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    path = out.stdout.strip()
    return path if out.returncode == 0 and path and Path(path).exists() else None


def compile_command(out: Path) -> list[str]:
    # Through xcrun, which tells swiftc where macOS's SDK is: run bare, it can't find its standard library.
    return ["xcrun", "--sdk", "macosx", "swiftc", "-O", "-swift-version", "5", "-parse-as-library", "-target", TARGET,
            "-o", str(out), *(str(SOURCE / f) for f in SOURCES)]


def source_hash() -> str:
    """What the app is built from: macapp/, the icon and the compile command. The doctor compares it with the app's."""
    h = hashlib.sha256()
    for f in sorted(SOURCE.iterdir()):
        if f.is_file():
            h.update(f.name.encode() + b"\0" + f.read_bytes() + b"\0")
    h.update(ICON.read_bytes() if ICON.exists() else b"")
    h.update(" ".join(compile_command(Path("Octo"))[:-2 - len(SOURCES)]).encode())
    return h.hexdigest()


def info(app: Path | None = None) -> dict | None:
    """The app's Info.plist, or None (none, or not readable)."""
    app = app or found()
    try:
        return plistlib.loads((app / "Contents" / "Info.plist").read_bytes()) if app else None
    except (OSError, plistlib.InvalidFileException):
        return None


def native(app: Path | None = None) -> bool:
    return bool((info(app) or {}).get("OctoSource"))


def icon(out: Path, work: Path) -> None:
    """The phone's icon on Apple's macOS grid (an 824 px rounded square on 1024, with a soft shadow), packed by
    iconutil. The iOS art is full-bleed and square; macOS draws icons as they are (design review, 19 Oct)."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    art = Image.open(ICON).convert("RGBA").resize((824, 824), Image.LANCZOS)
    mask = Image.new("L", (824, 824), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 823, 823), radius=185, fill=255)
    art.putalpha(ImageChops.multiply(art.getchannel("A"), mask))
    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 90), (100, 112), mask)
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(14)))
    canvas.alpha_composite(art, (100, 100))
    iconset = work / "AppIcon.iconset"
    iconset.mkdir()
    for size in (16, 32, 128, 256, 512):
        canvas.resize((size, size), Image.LANCZOS).save(iconset / f"icon_{size}x{size}.png")
        canvas.resize((size * 2, size * 2), Image.LANCZOS).save(iconset / f"icon_{size}x{size}@2x.png")
    subprocess.run(["iconutil", "-c", "icns", "-o", str(out), str(iconset)], check=True, capture_output=True, timeout=60)


def build(parent: Path) -> Path:
    """The app, built in a folder beside where it goes (same disk, so it can be renamed into place)."""
    parent.mkdir(parents=True, exist_ok=True)
    new = parent / f".Octo-{os.getpid()}.app"
    shutil.rmtree(new, ignore_errors=True)
    try:
        (new / "Contents" / "MacOS").mkdir(parents=True)
        (new / "Contents" / "Resources").mkdir()
        subprocess.run(compile_command(new / "Contents" / "MacOS" / "Octo"), check=True, capture_output=True,
                       text=True, timeout=600)
        stamp = source_hash()
        plist = plistlib.loads((SOURCE / "Info.plist").read_bytes())
        # The About box's "Version 2026.10.19 (3d81d3d96399)": the day it was built here, and what it was built from
        plist.update(CFBundleShortVersionString=time.strftime("%Y.%m.%d"), CFBundleVersion=stamp[:12], OctoSource=stamp)
        (new / "Contents" / "Info.plist").write_bytes(plistlib.dumps(plist))
        work = parent / f".Octo-icon-{os.getpid()}"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir()
        try:
            if ICON.exists():  # a copy without the phone app builds an app with the generic icon
                icon(new / "Contents" / "Resources" / "AppIcon.icns", work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        subprocess.run(["codesign", "--force", "--sign", "-", str(new)], check=True, capture_output=True, timeout=60)
    except BaseException:
        shutil.rmtree(new, ignore_errors=True)
        raise
    return new


def running() -> list[int]:
    """This user's running Octo apps, built or not from this copy."""
    out = subprocess.run(["pgrep", "-U", str(os.getuid()), "-f", r"/Octo\.app/Contents/MacOS/Octo( |$)"],
                         capture_output=True, text=True)
    return [int(p) for p in out.stdout.split()]


def stop(pids: list[int], wait_s: float = 5) -> None:
    """SIGTERM, not an Apple Event: from a terminal or launchd, `tell app "Octo" to quit` asks for Automation."""
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    end = time.time() + wait_s
    while time.time() < end and any(_alive(p) for p in pids):
        time.sleep(0.1)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def swap(new: Path, app: Path) -> None:
    """Rename the old app aside, the new one in, then delete the old: a folder can't be replaced in one rename."""
    old = app.with_name(f".Octo-old-{os.getpid()}.app")
    if app.exists():
        os.rename(app, old)
    os.rename(new, app)
    shutil.rmtree(old, ignore_errors=True)


def install(app: Path | None = None, octo: Path | None = None, compiler=swiftc, say=print) -> Path:
    """The native app when swiftc is there, else the AppleScript one. A failed build keeps the app that's there (or
    falls back to AppleScript when there's none) and says why: it never fails install.sh or `octo deploy`."""
    app = app or place()
    for other in (SYSTEM, USER):  # one app, not one in each Applications folder
        if other != app and other.exists() and other.stat().st_uid == os.getuid():
            shutil.rmtree(other)
    if compiler():
        try:
            new = build(app.parent)
        except (OSError, subprocess.SubprocessError, ImportError) as e:
            detail = getattr(e, "stderr", None) or str(e)
            say(f"couldn't build the Octo app: {str(detail).strip().splitlines()[-1] if str(detail).strip() else e}")
            if app.exists():
                return app
        else:
            was = running()
            stop(was)
            swap(new, app)
            subprocess.run([LSREGISTER, "-f", str(app)], capture_output=True, timeout=30)
            subprocess.run(["mdimport", str(app)], capture_output=True, timeout=30)  # in Spotlight now
            if was:  # it was open: open the new one in the menu bar, without its window
                subprocess.run(["open", "-g", str(app), "--args", "--background"], capture_output=True, timeout=30)
            return app
    if app.exists():
        shutil.rmtree(app)
    app.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["osacompile", "-o", str(app), "-e", script(octo or octo_bin())],
                   check=True, capture_output=True, timeout=30)
    subprocess.run(["mdimport", str(app)], capture_output=True, timeout=30)
    return app


def remove(stop_running: bool = True) -> bool:
    if stop_running:  # not when the app itself asked (`octo uninstall --from-app`): it quits once this is done
        stop(running())
    gone = False
    while (app := found()):
        shutil.rmtree(app)
        gone = True
    return gone


def verified(app: Path) -> bool:
    """Its signature still matches its files (a swapped or edited app doesn't)."""
    return subprocess.run(["codesign", "--verify", "--strict", str(app)], capture_output=True, timeout=30).returncode == 0


def runs(app: Path | None = None) -> str | None:
    """The AppleScript app's script, read back with `osadecompile`; None if there's no such app or it can't be read."""
    app = app or found()
    main = app / "Contents" / "Resources" / "Scripts" / "main.scpt" if app else None
    if not main or not main.exists():
        return None
    try:
        out = subprocess.run(["osadecompile", str(main)], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None
