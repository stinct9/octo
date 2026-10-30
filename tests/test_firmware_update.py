"""The lines of a USB update (firmware/recorder/octo_update.h, rec-13), compiled on the Mac and fed the
same lines octo/device.py sends: the first line (size and SHA-256), then each piece as offset and base64."""

import base64
import hashlib
import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Reads one case a line: "b CAP BASE64", "h MAX LINE" or "p CAP LINE"; prints "ok ..." or "no".
HARNESS = r"""
#include <stdio.h>
#include "octo_update.h"
static char line[70000];
static uint8_t out[65536];
static void bytes(size_t n) { for (size_t i = 0; i < n; i++) printf("%02x", out[i]); }
int main(void) {
  while (fgets(line, sizeof line, stdin)) {
    size_t len = strlen(line);
    if (len && line[len - 1] == '\n') line[--len] = 0;
    unsigned cap = 0;
    int at = 0;
    if (len < 2 || sscanf(line + 2, "%u%n", &cap, &at) != 1) { printf("bad case\n"); continue; }
    const char *rest = line + 2 + at + (line[2 + at] == ' ');
    size_t rlen = strlen(rest);
    size_t n = 0;
    uint32_t v = 0;
    if (line[0] == 'b') {
      if (oc_b64_decode(rest, rlen, out, cap, &n)) { printf("ok "); bytes(n); printf("\n"); } else printf("no\n");
    } else if (line[0] == 'h') {
      if (oc_parse_update_head(rest, rlen, cap, &v, out)) { printf("ok %u ", (unsigned)v); bytes(32); printf("\n"); }
      else printf("no\n");
    } else if (line[0] == 'p') {
      if (oc_parse_piece(rest, rlen, &v, out, cap, &n)) { printf("ok %u ", (unsigned)v); bytes(n); printf("\n"); }
      else printf("no\n");
    }
  }
  return 0;
}
"""


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    if not shutil.which("cc"):
        pytest.skip("no C compiler")
    tmp = tmp_path_factory.mktemp("fwu")
    (tmp / "u.c").write_text(HARNESS)
    subprocess.run(["cc", "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-I", str(ROOT / "firmware" / "recorder"),
                    str(tmp / "u.c"), "-o", str(tmp / "u")], check=True)

    def go(cases: list[str]) -> list[str]:
        out = subprocess.run([str(tmp / "u")], input="\n".join(cases) + "\n", capture_output=True, text=True, check=True)
        return out.stdout.splitlines()
    return go


def test_base64_as_python_writes_it(run):
    rng = random.Random(13)
    blobs = [b"", b"f", b"fo", b"foo", b"foob", b"fooba", b"foobar"] + [rng.randbytes(n) for n in (1, 2, 3, 100, 2047, 2048)]
    got = run([f"b 4096 {base64.b64encode(b).decode()}" for b in blobs])
    assert got == ["ok " + b.hex() for b in blobs]


def test_base64_that_isnt_or_doesnt_fit_is_refused(run):
    assert run(["b 10 Zm9", "b 10 Zm9v!", "b 10 Zg==Zm9v", "b 10 Zm=v", "b 10 Zm9v Zm9v", "b 2 Zm9v", "b 3 Zm9v"]) == \
        ["no", "no", "no", "no", "no", "no", "ok 666f6f"]


def test_the_first_line(run):
    sha = hashlib.sha256(b"rec-13").hexdigest()
    assert run([f"h 16777216 1342785 {sha}", f"h 16777216 1342785 {sha.upper()}", f"h 1000 1342785 {sha}",
                f"h 16777216 0 {sha}", f"h 16777216 1342785 {sha[:-1]}", f"h 16777216 1342785  {sha}",
                f"h 16777216 x1 {sha}", f"h 16777216 99999999999 {sha}", "h 16777216 1342785"]) == \
        [f"ok 1342785 {sha}", f"ok 1342785 {sha}"] + ["no"] * 7


class Accepting:
    """Just enough of the recorder for octo/device.py to send a whole update; it keeps the lines."""

    def __init__(self):
        self.lines, self.boot, self.stage = [], 1, None

    def run(self, cmd: bytes, timeout: float, **_) -> dict:
        self.lines.append(cmd.decode())
        if cmd == b"s":
            return {"fw": "rec-13", "boot": self.boot, "slot": f"app{self.boot - 1}", "pending": False}
        if cmd == b"U":
            self.stage = "head"
            return {"send": "update"}
        if self.stage == "head":
            self.stage = "pieces"
            return {"accepted": True, "piece": 2048}
        if cmd == b"end\n":
            self.boot = 2
            return {"restarting": True}
        off, data = cmd.split()
        return {"done": int(off) + len(base64.b64decode(data))}


def test_the_lines_the_mac_sends(run):
    """What octo/device.py's update_over_usb sends, read by the recorder's own parser: the first line, then
    the pieces, which put back together are the image."""
    from octo import device as D
    image = random.Random(7).randbytes(5000)
    board = Accepting()
    D.update_over_usb(board, image, log=lambda _: None, sleep=lambda _: None)
    head, pieces = board.lines[2].rstrip("\n"), [ln.rstrip("\n") for ln in board.lines[3:-2]]
    assert run([f"h 16777216 {head}"]) == [f"ok 5000 {hashlib.sha256(image).hexdigest()}"]
    got = run([f"p 2048 {ln}" for ln in pieces])
    assert [g.split()[1] for g in got] == ["0", "2048", "4096"]
    assert bytes.fromhex("".join(g.split()[2] for g in got)) == image


def test_a_piece_that_isnt_one_is_refused(run):
    assert run(["p 2048 0 ", "p 2048 Zm9v", "p 2048 -1 Zm9v", "p 2048 0  Zm9v", "p 2 0 Zm9v", "p 2048 4294967296 Zm9v",
                "p 2048 4294967295 Zm9v"]) == ["no"] * 6 + ["ok 4294967295 666f6f"]


def test_a_piece_fits_the_recorders_usb_buffer():
    """recorder.ino sizes its line and the USB receive buffer from the piece; a static_assert there checks
    they fit. Here: the Mac never sends more than a piece (it takes the size from the recorder's answer)."""
    src = (ROOT / "firmware" / "recorder" / "recorder.ino").read_text()
    assert "const size_t USB_PIECE = 2048;" in src and "const size_t USB_RX_BYTES = 4096;" in src
    line = len(f"{2**32 - 1} ") + len(base64.b64encode(os.urandom(2048))) + 2  # with \r\n
    assert line <= 4096
