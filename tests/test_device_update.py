"""Updating the recorder's firmware over USB through its own update code (octo/device.py, rec-13). A fake
recorder answers as recorder.ino does: s, U and its lines, K and X, and a restart onto the other slot."""

import base64
import hashlib

import pytest

from bench import device as B
from octo import device as D


class FakeRecorder:
    def __init__(self, fw="rec-13", wifi=True, want_on=False, starts=True, rejoins=True, garbles=0, loses_port=False,
                 kept_by_lab=False):
        self.fw, self.boot, self.slot, self.pending, self.wifi, self.want_on = fw, 7, "app0", False, wifi, want_on
        self.starts, self.rejoins, self.garbles, self.loses_port, self.kept_by_lab = starts, rejoins, garbles, loses_port, kept_by_lab
        self.sent, self.stage, self.image, self.size, self.sha = [], None, bytearray(), 0, None
        self.restart, self.polls, self.reopened = None, 0, 0

    def status(self) -> dict:
        return {"fw": self.fw, "boot": self.boot, "slot": self.slot, "pending": self.pending, "wifi": self.wifi,
                "want_on": self.want_on}

    def reopen(self, wait: float = 8.0) -> None:
        self.reopened += 1

    def _boot(self) -> None:
        """What the restart it asked for did: the new firmware on the other slot, on trial, off Wi-Fi at
        first; or (it didn't start) back on the slot it had."""
        to = self.restart
        self.restart, self.boot, self.polls = None, self.boot + 1, 0
        if to == "back":
            self.slot, self.fw, self.pending, self.wifi = self.old
            return
        if not self.starts:
            self.boot += 1  # it crashed, and the bootloader went back
            return
        self.old = (self.slot, self.fw, False, self.wifi)
        self.slot, self.fw = "app1" if self.slot == "app0" else "app0", bytes(self.image[:6]).decode()
        self.pending, self.wifi = not self.kept_by_lab, self.kept_by_lab

    def run(self, cmd: bytes, timeout: float, **_) -> dict:
        self.sent.append(cmd)
        if cmd == b"s":
            if self.restart:
                if self.loses_port and not self.reopened:
                    raise B.PortLost("lost the USB connection")
                self._boot()
            self.polls += 1
            if self.pending and self.rejoins and self.polls > 2:
                self.wifi = True
            return self.status()
        if cmd == b"U":
            if self.fw < "rec-13":
                raise B.BoardError("no result from the board within 5 s")  # older firmware ignores U
            self.stage = "head"
            return {"send": "update"}
        if cmd == b"K":
            was, self.pending = self.pending, False
            return {"kept": was, **self.status()}
        if cmd == b"X":
            self.restart = "back"
            return {"going_back": True}
        line = cmd.decode().rstrip("\n")
        if self.stage == "head":
            size, sha = line.split()
            if self.want_on:
                return {"error": "recording", "done": 0}
            self.stage, self.size, self.sha = "pieces", int(size), sha
            return {"accepted": True, "piece": 2048}
        if line == "end":
            ok = len(self.image) == self.size and hashlib.sha256(self.image).hexdigest() == self.sha
            self.stage, self.restart = None, ok and "new"
            return {"restarting": True} if ok else {"error": "sha256", "done": len(self.image)}
        off, data = line.split()
        if self.garbles:
            self.garbles -= 1
            return {"error": "data", "done": len(self.image)}
        if int(off) != len(self.image):
            return {"error": "offset", "done": len(self.image)}
        self.image += base64.b64decode(data)
        return {"done": len(self.image)}


IMAGE = b"rec-14" + bytes(range(256)) * 20  # 5126 bytes: three pieces


def update(board, **kw):
    log = []
    st = D.update_over_usb(board, IMAGE, log=log.append, sleep=lambda _: None, **kw)
    return st, log


def test_it_goes_in_pieces_and_is_kept_once_back_on_wifi():
    board = FakeRecorder()
    st, log = update(board)
    assert bytes(board.image) == IMAGE
    assert board.sent[2] == f"{len(IMAGE)} {hashlib.sha256(IMAGE).hexdigest()}\n".encode()
    assert [c.split()[0] for c in board.sent[3:6]] == [b"0", b"2048", b"4096"]
    assert board.sent[6] == b"end\n" and board.sent[-1] == b"K" and b"X" not in board.sent
    assert st["fw"] == "rec-14" and st["slot"] == "app1" and st["kept"] and not st["pending"] and st["wifi"]
    assert "sent 100%" in log and "waiting for it to rejoin Wi-Fi before keeping it" in log


def test_off_wifi_it_is_kept_as_soon_as_it_answers():
    board = FakeRecorder(wifi=False, rejoins=False)
    st, log = update(board)
    assert st["kept"] and st["fw"] == "rec-14" and board.sent[-2:] == [b"s", b"K"]


def test_one_that_doesnt_start_is_reported_gone_back():
    board = FakeRecorder(starts=False)
    with pytest.raises(D.DeviceError, match="didn't start properly, so the recorder went back to rec-13") as e:
        update(board)
    assert e.value.code == "rolled_back" and b"K" not in board.sent


def test_one_that_doesnt_rejoin_wifi_is_sent_back():
    board = FakeRecorder(rejoins=False)
    with pytest.raises(D.DeviceError, match="rec-14 started but didn't rejoin Wi-Fi .* went back to rec-13") as e:
        update(board, wifi_s=0.05)
    assert e.value.code == "not_back_on_wifi" and b"X" in board.sent and b"K" not in board.sent
    assert board.slot == "app0" and board.fw == "rec-13"


def test_kept_by_the_labs_check_over_wifi_meanwhile():
    """A signed request over Wi-Fi keeps it too (the lab asks every 30 s): nothing left for K to do."""
    board = FakeRecorder(kept_by_lab=True)
    st, _ = update(board)
    assert st["fw"] == "rec-14" and not st["pending"] and b"K" not in board.sent


def test_recording_on_is_refused_and_says_how_to_switch_it_off():
    board = FakeRecorder(want_on=True)
    with pytest.raises(D.DeviceError, match="recording is on: switch it off first") as e:
        update(board)
    assert e.value.code == "recording" and len(board.sent) == 3  # s, U and the first line: no pieces


def test_an_older_firmware_is_named_before_anything_is_sent():
    board = FakeRecorder(fw="rec-12")
    with pytest.raises(D.OldFirmware, match="rec-12, which can't update itself over USB"):
        update(board)
    assert board.sent == [b"s"]


def test_a_garbled_piece_is_sent_again():
    board = FakeRecorder(garbles=2)
    st, _ = update(board)
    assert bytes(board.image) == IMAGE and st["kept"]
    assert [c.split()[0] for c in board.sent[3:8]] == [b"0", b"0", b"0", b"2048", b"4096"]


def test_the_port_is_opened_again_if_it_went_away_in_the_restart():
    board = FakeRecorder(loses_port=True)
    st, _ = update(board)
    assert board.reopened == 1 and st["kept"] and st["fw"] == "rec-14"


def test_the_port_is_opened_without_restarting_the_board(monkeypatch):
    """DTR and RTS high: held low, opening the port restarted the board (measured 8 Oct), and a restart
    while an update is on trial sends it back."""
    seen = {}

    class Port:
        def __init__(self):
            self.port = None

        def open(self):
            seen.update(dtr=self.dtr, rts=self.rts, exclusive=self.exclusive)

    monkeypatch.setattr(B, "find_port", lambda: "/dev/cu.usbmodem-test")
    monkeypatch.setattr(B.serial, "Serial", Port)
    B.open_port(wait=0)
    assert seen == {"dtr": True, "rts": True, "exclusive": True}


class SigningRecorder(FakeRecorder):
    """rec-16 (C16): the first line signed with the key it shares with its Mac, or someone holds its button."""

    def __init__(self, key, held=False, **kw):
        super().__init__(fw="rec-16", **kw)
        self.key, self.held, self.asked = key, held, False

    def run(self, cmd: bytes, timeout: float, on_data=lambda d: None, **_) -> dict:
        if cmd == b"U":
            r = super().run(cmd, timeout)
            return {**r, "signed": True}
        if self.stage == "head":
            import hmac
            parts = cmd.decode().rstrip("\n").split(" ")
            head, sig = " ".join(parts[:2]), (parts[2] if len(parts) > 2 else "")
            if sig != hmac.new(bytes.fromhex(self.key), f"UPDATE\n{head}".encode(), hashlib.sha256).hexdigest():
                self.asked = True
                on_data({"v": 1, "sketch": "recorder", "confirm": "hold"})
                if not self.held:
                    self.stage = None
                    return {"error": "not_confirmed", "done": 0}
            return super().run(f"{head}\n".encode(), timeout)
        return super().run(cmd, timeout)


@pytest.fixture
def our_key(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "CONFIG", tmp_path / "device.json")
    D.update_config(lambda c: c.update({"key": "22" * 32}))
    return "22" * 32


def test_an_update_from_this_mac_is_signed_and_needs_no_button(our_key):
    board = SigningRecorder(our_key)
    st, log = update(board)
    assert not board.asked and st["fw"] == "rec-14"


def test_an_update_the_recorder_cant_check_waits_for_its_button(our_key):
    board = SigningRecorder("33" * 32)  # paired with another Mac
    with pytest.raises(D.DeviceError, match="nobody held its button"):
        update(board)
    assert board.asked and board.fw == "rec-16"
    board = SigningRecorder("33" * 32, held=True)
    st, log = update(board)
    assert any("hold its button" in line for line in log) and st["fw"] == "rec-14"


def test_a_firmware_that_starts_but_doesnt_work_is_sent_back(our_key):
    """Review 8 Oct, finding 13: rec-16 keeps a new firmware only once its microphone started and its card works;
    over USB the Mac then sends it back at once rather than leaving it on trial."""
    class Broken(SigningRecorder):
        def run(self, cmd, timeout, **kw):
            if cmd == b"K" and self.pending and self.fw == "rec-14":  # the new one's microphone didn't start
                return {"kept": False, **self.status(), "state": "mic_error"}
            return super().run(cmd, timeout, **kw)
    board = Broken(our_key, wifi=False)
    with pytest.raises(D.DeviceError, match="microphone didn't start, so the recorder went back to rec-16"):
        update(board)
    assert board.fw == "rec-16" and b"X" in board.sent
