"""Talk to the XIAO ESP32S3 over its built-in USB serial port.

Hardware facts this module is built around:
- The board enumerates as USB 303A:1001, but its /dev/cu.usbmodem* name changes between
  plugs and re-enumerations, so it's found by USB ID every time.
- Uploading or resetting drops USB; the port disappears for ~1 s and comes back.
- DTR and RTS are held high, which the board ignores. Held low, as until 8 Oct, opening the port
  restarted it (measured: boot 38 -> 39, reset reason "usb"; with both high, the same boot before and
  after an open and a close). The board's USB serial (the chip's own, ARDUINO_USB_MODE=1) resets it
  when DTR is low and RTS high; macOS raises both on open and pyserial then lowered DTR first. Since
  rec-13 this matters: a restart while an update is on trial sends it back to the firmware before.
- Only one program should hold the port. Ports are opened exclusively, so the Arduino IDE
  monitor (or a second bench process) gives an immediate "port busy" instead of stolen bytes.
"""

from __future__ import annotations

import time
from typing import Callable

import serial
from serial.tools import list_ports

from .protocol import is_identity, is_result, parse

USB_ID = (0x303A, 0x1001)
BAUD = 115200
# ROM bootloader chatter printed on every reset.
BOOT_NOISE = ("ESP-ROM", "Build:", "rst:", "Saved PC", "SPIWP", "mode:", "load:", "entry ")


class Cancelled(Exception):
    pass


class BoardError(RuntimeError):
    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.hint = hint


class BoardNotFound(BoardError):
    pass


class PortBusy(BoardError):
    pass


class PortLost(BoardError):
    """The port went away under us: unplugged, or the board re-enumerated."""


def find_port() -> str | None:
    for p in list_ports.comports():
        if (p.vid, p.pid) == USB_ID:
            return p.device
    return None


def _is_busy(e: Exception) -> bool:
    text = str(e)
    return "exclusively lock" in text or "Resource busy" in text or "[Errno 16]" in text


def open_port(wait: float = 8.0, timeout: float = 0.2) -> serial.Serial:
    """Find the board and open it exclusively, waiting up to `wait` s for USB to come back."""
    deadline = time.time() + wait
    while True:
        port = find_port()
        if port:
            s = serial.Serial()
            s.port, s.baudrate, s.timeout = port, BAUD, timeout
            s.dtr = s.rts = True  # not low: that restarts the board (module docstring)
            s.exclusive = True
            try:
                s.open()
                return s
            except serial.SerialException as e:
                if _is_busy(e):
                    raise PortBusy(
                        f"{port} is held by another program",
                        hint="Close the Arduino IDE serial monitor, or stop the other bench process.",
                    ) from e
                # Otherwise the port vanished between listing and opening: still re-enumerating.
        if time.time() > deadline:
            raise BoardNotFound(
                "board not found on USB",
                hint="Check the USB-C cable is a data cable and the board is plugged in.",
            )
        time.sleep(0.25)


class Board:
    """An open connection to a bench sketch."""

    def __init__(self, s: serial.Serial):
        self.s = s
        self._buf = b""

    @property
    def port(self) -> str:
        return self.s.port

    def close(self) -> None:
        try:
            self.s.close()
        except serial.SerialException:
            pass

    def __enter__(self) -> Board:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def reopen(self, wait: float = 8.0) -> None:
        """Open the port again after it went away; opening it doesn't restart the board."""
        self.close()
        self.s = open_port(wait=wait)
        self._buf = b""

    def send(self, data: bytes) -> None:
        try:
            self.s.write(data)
            self.s.flush()
        except (serial.SerialException, OSError) as e:
            raise PortLost("lost the USB connection", hint="Was the board unplugged?") from e

    def _fill(self) -> bool:
        # What's waiting, else the first byte to arrive within the timeout. Reading a fixed 4096 sat out
        # the whole timeout on every short answer (0.2 s): too slow for a USB update's ~650 pieces.
        try:
            chunk = self.s.read(self.s.in_waiting or 1)
        except (serial.SerialException, OSError) as e:
            raise PortLost("lost the USB connection", hint="Was the board unplugged?") from e
        self._buf += chunk
        return bool(chunk)

    def readline(self, timeout: float) -> str | None:
        """Next non-noise text line, or None if nothing complete arrives within `timeout`."""
        deadline = time.time() + timeout
        while True:
            while b"\n" in self._buf:
                raw, self._buf = self._buf.split(b"\n", 1)
                text = raw.decode(errors="replace").rstrip("\r")
                if not text.startswith(BOOT_NOISE):
                    return text
            if time.time() > deadline:
                return None
            self._fill()

    def read_exact(self, n: int, timeout: float) -> bytes:
        deadline = time.time() + timeout
        while len(self._buf) < n:
            if time.time() > deadline:
                raise BoardError(f"short read: {len(self._buf)} of {n} bytes")
            self._fill()
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def drain(self, quiet: float = 0.3, limit: float = 15.0) -> None:
        """Discard input until the board has been silent for `quiet` seconds."""
        self._buf = b""
        deadline = time.time() + limit
        last = time.time()
        while time.time() < deadline and time.time() - last < quiet:
            if self._fill():
                last = time.time()
                self._buf = b""

    def identify(self, timeout: float = 2.0) -> str | None:
        self.send(b"i")
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.readline(deadline - time.time())
            data = parse(line) if line else None
            if data and is_identity(data):
                return data["sketch"]
        return None

    def run(
        self,
        cmd: bytes,
        timeout: float,
        on_line: Callable[[str], None] = lambda _: None,
        on_data: Callable[[dict], None] = lambda _: None,
        check: Callable[[], None] = lambda: None,
    ) -> dict:
        """Send a command and return its final `@@` result.

        Human-readable lines go to on_line; other `@@` lines (confirm prompts, levels) go to
        on_data. check() is called between reads and may raise Cancelled.
        """
        self.send(cmd)
        deadline = time.time() + timeout
        while time.time() < deadline:
            check()
            line = self.readline(0.2)
            if line is None:
                continue
            data = parse(line)
            if data is None:
                on_line(line)
            elif is_result(data):
                return data
            elif not is_identity(data):
                on_data(data)
        raise BoardError(f"no result from the board within {timeout:.0f} s")


def connect(expected: str, wait: float = 12.0) -> Board:
    """Open the board and confirm `expected` sketch is running (e.g. right after an upload)."""
    deadline = time.time() + wait
    last_seen = None
    while True:
        board = Board(open_port(wait=max(1.0, deadline - time.time())))
        try:
            board.drain(quiet=0.2, limit=2)
            last_seen = board.identify()
            if last_seen == expected:
                return board
        except BoardError:
            pass
        board.close()
        if time.time() > deadline:
            raise BoardError(
                f"expected sketch {expected}, board reports {last_seen or 'nothing'}",
                hint="The upload may not have taken. Try again; if it persists, hold BOOT, tap RESET.",
            )
        time.sleep(0.5)
