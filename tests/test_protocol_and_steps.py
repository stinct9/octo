"""Parsers and verdicts against real serial output captured from the board (tests/fixtures/serial)."""

from pathlib import Path

import pytest

from bench.protocol import is_identity, is_result, parse
from bench.steps import board, sd, sd_wipe, wifi

FIXTURES = Path(__file__).parent / "fixtures" / "serial"


def result_from(sketch: str) -> dict:
    results = [d for line in (FIXTURES / f"{sketch}.txt").read_text().splitlines()
               if (d := parse(line)) and is_result(d)]
    assert results, f"no @@ result in {sketch} fixture"
    return results[-1]


def test_parse_classifies_lines():
    assert parse("Chip:  ESP32-S3") is None
    assert parse("@@ not json") is None
    assert parse('@@ {"v":2,"sketch":"x","a":1}') is None  # unknown protocol version
    ident = parse('@@ {"v":1,"sketch":"00_hello"}')
    assert is_identity(ident) and not is_result(ident)
    assert not is_result(parse('@@ {"v":1,"sketch":"02_mic","level":{"dbfs":-50}}'))
    assert not is_result(parse('@@ {"v":1,"sketch":"01b_sd_wipe","confirm":"WIPE","claimed_sectors":1}'))
    assert not is_result(parse('@@ {"v":1,"sketch":"00_hello","ready":true}'))
    assert is_result(parse('@@ {"v":1,"sketch":"01_sd","error":"x"}'))


@pytest.mark.parametrize("sketch", ["00_hello", "01_sd", "01b_sd_wipe", "02_mic", "03_wifi"])
def test_every_fixture_has_exactly_the_expected_sketch(sketch):
    assert result_from(sketch)["sketch"] == sketch


def test_board_real_output_passes():
    o = board.evaluate(result_from("00_hello"))
    assert o.status == "pass", o.summary
    assert {m.key: m.value for m in o.metrics}["psram_mb"] == 8


def test_board_without_psram_fails_with_build_hint():
    r = dict(result_from("00_hello"), psram_bytes=0)
    o = board.evaluate(r)
    assert o.status == "fail" and "PSRAM=opi" in o.hint


def test_sd_real_output_passes_with_headroom():
    o = sd.evaluate(result_from("01_sd"))
    assert o.status == "pass", o.summary
    m = {m.key: m.value for m in o.metrics}
    assert 12 < m["headroom"] < 16  # ~430 KB/s vs 31.25 KB/s
    assert m["mount_attempts"] == 1


def test_sd_retried_mount_warns():
    o = sd.evaluate(dict(result_from("01_sd"), mount_attempts=2))
    assert o.status == "warn" and "mount" in o.hint.lower()


def test_sd_slow_card_warns():
    o = sd.evaluate(dict(result_from("01_sd"), write_ms=30000))
    assert o.status == "warn"


def test_sd_no_card_fails_with_reseat_hint():
    o = sd.evaluate({"v": 1, "sketch": "01_sd", "boot": "usb", "mount_attempts": 3,
                     "phase": "mount", "error": "no_card_or_unreadable"})
    assert o.status == "fail" and "Reseat" in o.hint


def test_wifi_real_output_passes():
    o = wifi.evaluate(result_from("03_wifi"))
    assert o.status == "pass", o.summary


@pytest.mark.parametrize("rssi,status", [(-65, "pass"), (-75, "warn"), (-85, "fail")])
def test_wifi_thresholds(rssi, status):
    assert wifi.evaluate(dict(result_from("03_wifi"), rssi_mean=rssi)).status == status


def test_wifi_network_not_visible():
    o = wifi.evaluate({"v": 1, "sketch": "03_wifi", "networks_visible": 12, "own_visible": False})
    assert o.status == "fail" and "2.4 GHz" in o.hint


def test_wipe_abort_is_skipped_not_failed():
    o = sd_wipe.evaluate(result_from("01b_sd_wipe"))
    assert o.status == "skipped"


def test_build_fingerprint_tracks_sources(tmp_path, monkeypatch):
    import bench.firmware as fw
    sk = tmp_path / "99_x"
    lib = tmp_path / "libraries" / "P"
    sk.mkdir(parents=True)
    lib.mkdir(parents=True)
    (sk / "99_x.ino").write_text("void setup(){}")
    (lib / "P.h").write_text("#pragma once")
    monkeypatch.setattr(fw, "SKETCHES", tmp_path)
    monkeypatch.setattr(fw, "LIBRARIES", tmp_path / "libraries")
    monkeypatch.setattr(fw, "_toolchain", lambda: "esp32 3.3.12")
    a = fw.fingerprint("99_x")
    assert fw.fingerprint("99_x") == a
    (sk / "secrets.h").write_text('#define WIFI_PASS "x"')
    b = fw.fingerprint("99_x")
    assert b != a  # new/changed secrets.h forces a rebuild
    (lib / "P.h").write_text("#pragma once // v2")
    c = fw.fingerprint("99_x")
    assert c != b  # shared library change too
    (sk / "build").mkdir()
    (sk / "build" / "junk.bin").write_bytes(b"\0")
    assert fw.fingerprint("99_x") == c  # build output is not a source
