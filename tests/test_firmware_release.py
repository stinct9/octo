"""Flashing without the Arduino toolchain (open-source plan I5): a release's prebuilt firmware, checked against its
SHA256SUMS, and a first install on a board that has never run Octo. No board, no esptool run: both are replaced."""

import hashlib
from argparse import Namespace
from types import SimpleNamespace

import pytest

import bench.device as BD
from octo import cli
from octo import device as D


def release(tmp_path, files: dict[str, bytes], sums: dict[str, bytes] | None = None):
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
    (tmp_path / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(d).hexdigest()}  {n}\n" for n, d in (sums or files).items()))
    return tmp_path


def test_a_release_file_is_used_only_when_its_checksum_matches(tmp_path):
    folder = release(tmp_path, {"recorder.ino.bin": b"app"})
    assert D.release_firmware("recorder.ino.bin", folder).read_bytes() == b"app"
    (folder / "recorder.ino.bin").write_bytes(b"changed")
    with pytest.raises(D.DeviceError, match="doesn't match"):
        D.release_firmware("recorder.ino.bin", folder)
    with pytest.raises(D.DeviceError, match="arduino-cli"):
        D.release_firmware("recorder.ino.bin", tmp_path / "nothing")


def test_a_new_board_gets_the_whole_image_at_zero(tmp_path):
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout="Hash of data verified.", stderr="")
    D.install_firmware("/dev/cu.usbmodem101", tmp_path / "whole.bin", run=run)
    assert seen["cmd"][-3:] == ["write-flash", "0x0", str(tmp_path / "whole.bin")] and "esp32s3" in seen["cmd"]
    with pytest.raises(D.DeviceError, match="hold B, tap R"):
        D.install_firmware("p", tmp_path / "w.bin", run=lambda cmd, **kw: SimpleNamespace(returncode=2, stdout="", stderr="A fatal error\nFailed to connect"))


def test_flash_on_an_unpaired_board(monkeypatch, tmp_path, capsys):
    folder = release(tmp_path, {"recorder.ino.bin": b"app", "recorder.ino.merged.bin": b"whole"})
    monkeypatch.setattr(D, "RELEASE_FW", folder)
    monkeypatch.setattr(D, "load_config", lambda: None)
    monkeypatch.setattr("shutil.which", lambda name: None)  # no Arduino toolchain on this Mac
    installed = []
    monkeypatch.setattr(D, "install_firmware", lambda port, image: installed.append((port, image.read_bytes())))
    args = Namespace(usb=False, direct=False)
    monkeypatch.setattr(BD, "find_port", lambda: None)
    assert cli._flash(D, args) == 1 and "plug it in" in capsys.readouterr().out and not installed
    monkeypatch.setattr(BD, "find_port", lambda: "/dev/cu.usbmodem101")
    assert cli._flash(D, args) == 0
    assert installed == [("/dev/cu.usbmodem101", b"whole")] and "octo device pair" in capsys.readouterr().out
