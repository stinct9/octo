"""Pairing a recorder with this Mac and its Wi-Fi networks (octo/device.py, rec-12, product plan 4.4).
A fake board answers like the firmware's USB commands (P and N); nothing touches a real port."""

import json
import time

import pytest

from octo import device as D


class FakeBoard:
    """Answers P and N as recorder.ino does: a prompt, then the result for the line that follows."""

    def __init__(self, fw="rec-12"):
        self.fw, self.sent, self.mode, self.nets = fw, [], None, []

    def run(self, cmd: bytes, timeout: float, **_) -> dict:
        self.sent.append(cmd)
        if self.fw < "rec-12" and cmd in (b"P", b"N"):
            return {"v": 1, "sketch": "recorder", "state": "off"}  # an older firmware: just its status
        if cmd == b"P":
            self.mode = "pair"
            return {"send": "pair"}
        if cmd == b"N":
            self.mode = "net"
            return {"send": "net"}
        line = cmd.decode().strip()
        if self.mode == "pair":
            key, pub = line.split(" ")
            ok = len(bytes.fromhex(key)) == 32 and len(bytes.fromhex(pub)) == 32
            return {"paired": ok, "fw": self.fw} if ok else {"paired": False, "error": "bad_pairing_line"}
        if line.startswith("add "):
            name = line.split(" ")[1]  # an open network has no password field
            self.nets.insert(0, bytes.fromhex(name).decode())
        elif line.startswith("del "):
            self.nets.remove(bytes.fromhex(line.split(" ")[1]).decode())
        return {"ok": True, "networks_hex": [n.encode().hex() for n in self.nets]}


@pytest.fixture(autouse=True)
def own_config(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "CONFIG", tmp_path / "device.json")
    monkeypatch.setattr(D, "SECRETS_H", tmp_path / "no-secrets.h")


def test_pairing_sends_the_key_and_this_macs_public_key_and_keeps_them():
    board = FakeBoard()
    st = D.pair_usb(board)
    assert st["paired"] and board.sent[0] == b"P"
    key, pub = board.sent[1].decode().split()
    cfg = json.loads(D.CONFIG.read_text())
    assert key == cfg["key"] and len(pub) == 64 and cfg["host"] == "octo-rec.local"
    assert oct(D.CONFIG.stat().st_mode & 0o777) == "0o600"
    from octo.devicecrypto import BASE, x25519
    assert x25519(bytes.fromhex(cfg["enc_private"]), BASE).hex() == pub  # only this Mac holds the private half
    again = FakeBoard()
    D.pair_usb(again)
    assert again.sent[1] == board.sent[1]  # the same keys next time: chunks already recorded stay readable


def test_a_new_key_is_kept_only_once_the_recorder_has_taken_it():  # review 8 Oct, C15
    D.pair_usb(FakeBoard())
    before = json.loads(D.CONFIG.read_text())["key"]

    class Refusing(FakeBoard):
        def run(self, cmd, timeout, **_):
            r = super().run(cmd, timeout)
            return {"paired": False, "error": "timeout"} if self.mode == "pair" and cmd != b"P" else r
    with pytest.raises(D.DeviceError, match="didn't take"):
        D.pair_usb(Refusing(), new_key=True)
    assert json.loads(D.CONFIG.read_text())["key"] == before  # still the key the recorder has
    board = FakeBoard()
    D.pair_usb(board, new_key=True)
    assert json.loads(D.CONFIG.read_text())["key"] == board.sent[1].decode().split()[0] != before


def test_an_older_firmware_is_told_to_update():
    with pytest.raises(D.DeviceError, match="rec-12"):
        D.pair_usb(FakeBoard(fw="rec-11"))
    with pytest.raises(D.DeviceError, match="rec-12"):
        D.networks_usb(FakeBoard(fw="rec-11"), "list")


def test_networks_travel_as_hex_and_are_checked_first():
    board = FakeBoard()
    assert D.networks_usb(board, D.net_command("add", "Sam's Wi-Fi 2.4", "p@ss word 9")) == ["Sam's Wi-Fi 2.4"]
    assert board.sent[-1] == ("add " + "Sam's Wi-Fi 2.4".encode().hex() + " " + "p@ss word 9".encode().hex() + "\n").encode()
    assert D.networks_usb(board, D.net_command("add", "Cafe", "")) == ["Cafe", "Sam's Wi-Fi 2.4"]  # open network
    assert D.networks_usb(board, D.net_command("remove", "Cafe")) == ["Sam's Wi-Fi 2.4"]
    for ssid, pw in (("", "password1"), ("x" * 33, "password1"), ("Home", "short")):
        with pytest.raises(D.DeviceError):
            D.net_command("add", ssid, pw)


class WifiDev:
    def __init__(self, fw="rec-12"):
        self.fw, self.calls = fw, []

    def call(self, path, body=None, raw=False):
        self.calls.append((path, body))
        if path == "/status":
            return {"fw": self.fw}
        return {"ok": True, "networks_hex": ["486f6d65"]}


def test_over_wifi_it_is_a_signed_call():
    dev = WifiDev()
    assert D.networks_wifi(dev, "list") == ["Home"] and dev.calls[-1] == ("/networks", {"cmd": "list"})


def test_an_older_recorder_on_wifi_is_named_not_suspected(monkeypatch):
    """rec-11 has no /networks; its unsigned "not found" once read as "not our recorder?" (8 Oct)."""
    dev = WifiDev(fw="rec-11")
    with pytest.raises(D.OldFirmware, match="rec-11: one network"):
        D.networks_wifi(dev, "list")
    assert dev.calls == [("/status", None)]  # never asked for /networks
    assert D.firmware_number("rec-9") < D.firmware_number("rec-12") and D.firmware_number(None) == 0
    monkeypatch.setattr(D.Device, "from_config", classmethod(lambda cls: WifiDev(fw="rec-11")))
    with pytest.raises(D.OldFirmware):  # and not a USB attempt after it
        D.networks("list")


def test_settings_answers_at_once_while_the_recorder_was_just_unreachable(tmp_path, monkeypatch):
    """Settings waited 16 s (lock, four tries) for a recorder the 30 s check had just failed to reach (5 Nov)."""
    from tests.test_fillers import _client
    monkeypatch.setattr(D, "load_config", lambda: {"key": "k", "host": "h"})
    monkeypatch.setattr(D.Device, "from_config", classmethod(lambda cls: WifiDev()))
    monkeypatch.setattr(D, "last_status", lambda: {"error": "No route to host", "checked_at": time.time() - 20})
    monkeypatch.setattr(D, "networks_wifi", lambda dev, cmd: pytest.fail("asked the recorder"))
    got = _client(tmp_path).get("/api/octo/settings/recorder").json()
    assert got["networks"] is None and got["error"] == "not reachable on Wi-Fi now (No route to host)"
    monkeypatch.setattr(D, "last_status", lambda: {"error": "No route to host", "checked_at": time.time() - 200})
    monkeypatch.setattr(D, "networks_wifi", lambda dev, cmd: ["Home"])  # an old failure: ask again
    assert _client(tmp_path).get("/api/octo/settings/recorder").json()["networks"] == ["Home"]


class SigningBoard(FakeBoard):
    """rec-16 (review 8 Oct, C16): a paired recorder takes a re-pairing only signed with the key it has, or once
    someone holds its button (`held`); a recorder with no key yet takes any."""

    def __init__(self, key=None, held=False):
        super().__init__(fw="rec-16")
        self.key, self.held, self.asked = key, held, False

    def run(self, cmd: bytes, timeout: float, on_data=lambda d: None, **_) -> dict:
        if cmd == b"P":
            self.sent.append(cmd)
            self.mode = "pair"
            return {"send": "pair", "signed": True}
        if self.mode == "pair":
            self.sent.append(cmd)
            import hashlib
            import hmac
            parts = cmd.decode().strip().split(" ")
            line, sig = " ".join(parts[:2]), (parts[2] if len(parts) > 2 else "")
            want = hmac.new(bytes.fromhex(self.key), f"PAIR\n{line}".encode(), hashlib.sha256).hexdigest() if self.key else None
            if self.key and sig != want:
                self.asked = True
                on_data({"v": 1, "sketch": "recorder", "confirm": "hold"})
                if not self.held:
                    return {"paired": False, "error": "not_confirmed"}
            self.key = parts[0]
            return {"paired": True, "fw": self.fw}
        return super().run(cmd, timeout)


def test_a_re_pairing_from_this_mac_is_signed_and_needs_no_button():
    D.pair_usb(SigningBoard())  # a new recorder: no key yet, nothing to sign with
    ours = json.loads(D.CONFIG.read_text())["key"]
    board = SigningBoard(key=ours)
    D.pair_usb(board, new_key=True, log=lambda _: None)
    assert not board.asked and board.key == json.loads(D.CONFIG.read_text())["key"] != ours


def test_a_recorder_paired_with_another_mac_asks_for_its_button():
    said = []
    with pytest.raises(D.DeviceError, match="nobody held its button"):
        D.pair_usb(SigningBoard(key="11" * 32), log=said.append)
    assert said and "hold its button" in said[0]
    assert not D.CONFIG.exists()  # nothing kept from a pairing that didn't happen
    board = SigningBoard(key="11" * 32, held=True)
    D.pair_usb(board, log=said.append)
    assert board.asked and board.key == json.loads(D.CONFIG.read_text())["key"]
