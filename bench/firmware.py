"""Compile and upload bench sketches with arduino-cli."""

from __future__ import annotations

import functools
import hashlib
import json
import queue
import re
import subprocess
import threading
import time
from typing import Callable

from . import ROOT, SKETCHES
from .device import BoardError, BoardNotFound, Cancelled, find_port

# PSRAM is disabled by default for this board; without it the 8 MB is invisible.
FQBN = "esp32:esp32:XIAO_ESP32S3:PSRAM=opi"
LIBRARIES = SKETCHES / "libraries"
BUILD = ROOT / ".build"  # gitignored: 03_wifi binaries contain the Wi-Fi password

# esptool writes several flash regions (bootloader 0x0, partitions 0x8000, boot_app0 0xe000,
# app 0x10000), each with its own 0-100%. Only the app region is worth showing as progress.
_PROGRESS = re.compile(r"Writing at 0x([0-9a-fA-F]+).*?(\d+(?:\.\d+)?)%")
APP_OFFSET = 0x10000

# Upload failures worth a straight retry. Anything else (e.g. compile output missing) isn't.
_TRANSIENT = (
    "Invalid head of packet",
    "Failed to connect",
    "No serial data received",
    "aited out waiting for packet",
    "Lost connection",
    "returned no data",
    "No such file or directory",  # port renamed mid-upload by re-enumeration
    "Could not open",
)

LineFn = Callable[[str], None]

# The recorder (L3) lives outside the bench folder but builds and flashes the same way.
RECORDER = "recorder"


def sketch_dir(sketch: str):
    return ROOT / "firmware" / "recorder" if sketch == RECORDER else SKETCHES / sketch


def _run(args: list[str], on_line: LineFn, timeout: float, check: Callable[[], None] = lambda: None,
         on_progress: Callable[[float], None] | None = None) -> tuple[int, list[str]]:
    """Run a command, streaming output lines. Kills it on timeout or if check() raises."""
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors="replace", bufsize=1)
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        assert proc.stdout
        for line in proc.stdout:
            lines.put(line.rstrip())
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    out: list[str] = []
    deadline = time.time() + timeout
    try:
        while True:
            check()
            if time.time() > deadline:
                raise BoardError(f"{args[1]} timed out after {timeout:.0f} s")
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                break
            m = _PROGRESS.search(line)
            if m:
                if on_progress and int(m.group(1), 16) >= APP_OFFSET:
                    on_progress(float(m.group(2)))
                continue  # progress lines would flood the log
            if line:
                out.append(line)
                on_line(line)
    except (Cancelled, BoardError):
        proc.kill()
        proc.wait()
        raise
    return proc.wait(), out


@functools.cache
def _toolchain() -> str:
    """Installed cores and versions, e.g. "esp32:esp32@3.3.12": a core update must invalidate
    cached builds. Only these fields -- arduino-cli's full JSON lists releases in a different
    order on every call, which made every build look changed."""
    try:
        out = subprocess.run(["arduino-cli", "core", "list", "--format", "json"], capture_output=True,
                             text=True, timeout=30).stdout
        platforms = json.loads(out).get("platforms", [])
        return ",".join(sorted(f"{p['id']}@{p.get('installed_version')}" for p in platforms))
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        return "unknown"


def fingerprint(sketch: str) -> str:
    """Everything a build depends on: board options, toolchain, the sketch folder (including
    secrets.h) and the shared protocol library."""
    h = hashlib.sha256()
    h.update(FQBN.encode())
    h.update(_toolchain().encode())
    for root in (sketch_dir(sketch), LIBRARIES):
        for p in sorted(root.rglob("*")):
            if p.is_file() and "build" not in p.parts:
                h.update(f"{root.name}/{p.relative_to(root)}".encode())
                h.update(p.read_bytes())
    return h.hexdigest()


def compile_sketch(sketch: str, on_line: LineFn, check: Callable[[], None] = lambda: None) -> None:
    """Compile into .build/<sketch>, unless nothing it depends on has changed since the last
    successful build (~10 s saved per sketch). Safe to cancel. Needs no board."""
    out_dir = BUILD / sketch
    stamp = out_dir / ".fingerprint"
    fp = fingerprint(sketch)
    if stamp.exists() and stamp.read_text() == fp and (out_dir / f"{sketch}.ino.bin").exists():
        on_line("unchanged since the last build; using it")
        return
    stamp.unlink(missing_ok=True)
    code, _ = _run(
        ["arduino-cli", "compile", "-b", FQBN, "--libraries", str(LIBRARIES),
         "--build-path", str(out_dir / "work"), "--output-dir", str(out_dir), str(sketch_dir(sketch))],
        on_line, timeout=600, check=check)
    if code != 0:
        raise BoardError(f"compile of {sketch} failed", hint="See the compiler output above.")
    stamp.write_text(fp)


def upload(sketch: str, on_line: LineFn, on_progress: Callable[[float], None], retries: int = 3) -> str:
    """Flash the compiled sketch; returns the port used. Not cancellable: a half-written
    flash would leave the board without working firmware until the next upload."""
    for attempt in range(1, retries + 1):
        port = find_port()
        if not port:
            raise BoardNotFound("board not found on USB",
                                hint="Check the USB-C cable is a data cable and the board is plugged in.")
        code, out = _run(["arduino-cli", "upload", "-b", FQBN, "-p", port, "--input-dir", str(BUILD / sketch)],
                         on_line, timeout=180, on_progress=on_progress)
        if code == 0:
            return port
        text = "\n".join(out)
        if "busy" in text.lower() or "exclusively" in text.lower():
            raise BoardError("upload failed: port busy",
                             hint="Close the Arduino IDE serial monitor or any other serial program.")
        if not any(t in text for t in _TRANSIENT) or attempt == retries:
            raise BoardError(f"upload of {sketch} failed",
                             hint="If it keeps failing: hold BOOT, tap RESET, release BOOT, then retry.")
        on_line(f"upload attempt {attempt} hit a transient error; retrying")
        time.sleep(1.0)
    raise AssertionError("unreachable")
