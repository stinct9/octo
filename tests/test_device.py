"""The recorder's Mac side (octo/device.py): signing, manifests, pulling safely, and where recordings are cut."""

import hashlib
import json
import os
import stat
import wave

import numpy as np
import pytest

from octo import device as D
from octo import pipeline as P
from octo.db import connect, row, rows

RATE = 16000


def pcm(seconds: float, loud: bool) -> bytes:
    return (np.full(int(seconds * RATE), 1000 if loud else 0, np.int16)).tobytes()


class FakeRecorder:
    """The firmware's API semantics (recorder.ino), in memory."""

    def __init__(self, boot=2, state="recording", seg=1, page=200):
        self.boot, self.state, self.seg, self.page = boot, state, seg, page
        self.files: dict[tuple[int, int], bytes] = {}
        self.manifests: dict[int, str] = {}
        self.calls: list[tuple[str, dict]] = []

    def line(self, boot, d):
        self.manifests[boot] = self.manifests.get(boot, "") + json.dumps(d, separators=(",", ":")) + "\n"

    def chunk(self, boot, seq, seg, start_ms, data, corrupt=False, closed=True):
        self.line(boot, {"open": seq, "seg": seg, "start_ms": start_ms})
        self.files[(boot, seq)] = b"H" * 44 + (data[:-2] + b"xx" if corrupt else data)
        if closed:
            self.line(boot, {"seq": seq, "seg": seg, "start_ms": start_ms, "samples": len(data) // 2,
                             "sha256": hashlib.sha256(data).hexdigest(), "dropped": 0})

    def call(self, path, body=None, raw=False):
        body = body or {}
        self.calls.append((path, body))
        if path == "/status":
            return {"boot": self.boot, "state": self.state, "seg": self.seg}
        if path == "/clock":
            self.line(self.boot, {"clock_ms": body["epoch_ms"], "at_ms": 100_000})
            return {}
        if path == "/boots":
            boots = sorted({b for b, _ in self.files} | set(self.manifests))
            return [{"boot": b, "current": b == self.boot, "chunk_files": sum(1 for k in self.files if k[0] == b)} for b in boots]
        if path == "/manifest":
            if body["boot"] not in self.manifests:  # a folder with chunk files but no manifest: 404
                raise D.DeviceError("/manifest: no_manifest", "no_manifest")
            text = self.manifests.get(body["boot"], "")[body["from"]:][:self.page]
            return text[:text.rfind("\n") + 1]
        if path == "/chunk":
            if (body["boot"], body["seq"]) not in self.files:
                raise D.DeviceError("/chunk: no_chunk", "no_chunk")
            return self.files[(body["boot"], body["seq"])]
        if path == "/delete":
            if "seq" in body:
                return {"deleted": self.files.pop((body["boot"], body["seq"]), None) is not None}
            assert body["boot"] != self.boot
            self.manifests.pop(body["boot"], None)
            return {"deleted": True}
        raise AssertionError(path)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    for mod in (D, P):
        monkeypatch.setattr(mod, "HOME", tmp_path)
    monkeypatch.setattr(D, "CHUNKS", tmp_path / "audio" / "device")
    monkeypatch.setattr(P, "AUDIO", tmp_path / "audio" / "lab")
    monkeypatch.setattr(D, "STATUS", tmp_path / "device_status.json")
    return connect(tmp_path / "d.db")


def speech(path):  # stand-in for Silero: any non-zero sample is "speech"
    with wave.open(str(path)) as w:
        return 5.0 if np.frombuffer(w.readframes(w.getnframes()), np.int16).any() else 0.0


def test_pulls_only_what_matches_its_checksum_and_deletes_only_what_is_stored(lab):
    dev = FakeRecorder(boot=2)
    dev.chunk(1, 0, 1, 500, pcm(2, True))  # an earlier boot, finished
    dev.chunk(2, 0, 1, 500, pcm(2, True))
    dev.chunk(2, 1, 1, 2500, pcm(2, True), corrupt=True)
    dev.chunk(2, 2, 1, 4500, pcm(2, True), closed=False)  # still being written: never asked for
    r = D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert (r["pulled"], r["bad"]) == (2, 1)
    assert set(dev.files) == {(2, 1), (2, 2)}  # the bad one stays on the card for a retry
    assert ("/chunk", {"boot": 2, "seq": 2}) not in dev.calls
    assert 1 not in dev.manifests  # boot 1: everything pulled, so its directory went too
    got = row(lab, "SELECT * FROM device_chunks WHERE boot=2 AND seq=0")
    assert got["deleted_on_device_at"] and got["sha256"] == hashlib.sha256(pcm(2, True)).hexdigest()
    # A second sync doesn't pull again, and the manifest is read on from where it stopped.
    dev.calls.clear()
    D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert not [c for c in dev.calls if c[0] == "/chunk" and c[1]["seq"] == 0]
    assert all(c[1]["from"] > 0 for c in dev.calls if c[0] == "/manifest" and c[1]["boot"] == 2)


def test_a_stored_chunk_survives_a_failed_delete(lab):
    dev = FakeRecorder(boot=2)
    dev.chunk(2, 0, 1, 500, pcm(1, False))
    real = dev.call

    def flaky(path, body=None, raw=False):
        if path == "/delete":
            raise D.Unreachable("Wi-Fi dropped")
        return real(path, body, raw)

    dev.call = flaky
    with pytest.raises(D.Unreachable):
        D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert row(lab, "SELECT deleted_on_device_at FROM device_chunks")["deleted_on_device_at"] is None
    dev.call = real
    D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert not dev.files


def test_recordings_are_cut_only_at_three_silent_chunks_and_silence_is_deleted(lab):
    dev = FakeRecorder(boot=2, state="off")
    loud = [True, False, True, False, False, False, True]  # speech, short pause, speech, 3 min quiet, speech
    for i, l in enumerate(loud):
        dev.chunk(2, i, 1, 1000 + i * 2000, pcm(2, l))
    r = D.sync(lab, dev, log=lambda m: None, speech=speech)
    recs = rows(lab, "SELECT * FROM recordings ORDER BY original")
    assert len(r["recordings"]) == 2 and [round(x["duration_s"]) for x in recs] == [6, 2]
    assert [c["recording_id"] == "silent" for c in rows(lab, "SELECT * FROM device_chunks ORDER BY seq")] == \
        [False, False, False, True, True, True, False]
    assert not any((D.CHUNKS / "b00002").glob("*.wav"))  # chunk files gone once joined or judged silent
    # The device's clock line maps uptime to wall time: chunk 0 started 99 s before the clock was set.
    clock = D.parse_manifest(dev.manifests[2])["clock"]
    assert recs[0]["started_at"] == pytest.approx(clock[0] / 1000 - 99)


def test_a_conversation_still_going_waits(lab):
    dev = FakeRecorder(boot=2, state="recording", seg=1)
    for i in range(3):
        dev.chunk(2, i, 1, 1000 + i * 2000, pcm(2, True))
    D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert rows(lab, "SELECT * FROM recordings") == []
    dev.state = "off"
    D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert len(rows(lab, "SELECT * FROM recordings")) == 1


def test_a_hole_after_a_card_error_becomes_silence_so_the_timeline_stays_true(lab):
    dev = FakeRecorder(boot=2, state="off")
    dev.chunk(2, 0, 1, 1000, pcm(2, True))
    dev.chunk(2, 1, 1, 3800, pcm(2, True))  # 0.8 s lost between them
    D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert row(lab, "SELECT duration_s FROM recordings")["duration_s"] == pytest.approx(4.8)


def test_plan_cuts_and_runs():
    c = lambda seq, s, seg=1, start=None, boot=1: {"boot": boot, "seq": seq, "seg": seg, "speech_s": s,
                                                    "start_ms": start, "samples": 16000}
    kinds = lambda plan: [(k, [x["seq"] for x in cs]) for k, cs in plan]
    assert kinds(D.plan_cuts([c(0, 0), c(1, 3), c(2, 0)], finished=False)) == [("silent", [0])]  # the rest waits
    assert kinds(D.plan_cuts([c(0, 0), c(1, 3), c(2, 0)], finished=True)) == [("silent", [0]), ("recording", [1]), ("silent", [2])]
    runs = D._runs([c(0, 1), c(1, 1), c(3, 1), c(4, 1, seg=2), c(5, 1, seg=2, start=0), c(6, 1, seg=2, start=200_000)])
    assert [[x["seq"] for x in r] for r in runs] == [[0, 1], [3], [4, 5], [6]]


def test_parse_manifest_survives_a_line_cut_by_power_loss():
    text = ('{"boot":3,"fw":"rec-1"}\n{"open":0,"seg":1,"start_ms":640}\n{"clock_ms":5000,"at_ms":10}\n'
            '{"seq":0,"samples":100,"sha256":"ab","repaired":true}\n{"open":1,"seg":1,"sta')
    m = D.parse_manifest(text)
    assert m["info"]["boot"] == 3 and m["clock"] == (5000, 10)
    assert m["chunks"] == [{"seq": 0, "seg": 1, "start_ms": 640, "samples": 100, "sha256": "ab", "dropped": None, "repaired": True}]
    assert D.wall_clock(m["clock"], 1010) == 6.0 and D.wall_clock(None, 5) is None


def test_device_answers_must_be_signed_with_our_key(monkeypatch):
    dev = D.Device("192.168.0.9", "aa" * 32)
    sent = {}

    def http(method, path, data=None, headers=None):
        if path == "/hello":
            return 200, {}, b'{"nonce":"n1"}'
        sent.update(headers)
        body = b'{"state":"recording"}'
        return 200, {"X-Octo-Next": "n2", "X-Octo-Sig": dev.sign(f"RESP\n{path}\nn1\n{body.decode()}")}, body

    monkeypatch.setattr(dev, "_http", http)
    assert dev.call("/status") == {"state": "recording"}
    assert sent["X-Octo-Sig"] == dev.sign("POST\n/status\nn1\n{}") and dev.nonce == "n2"

    impostor = D.Device("192.168.0.9", "bb" * 32)
    monkeypatch.setattr(impostor, "_http", http)  # answers signed with a different key
    with pytest.raises(D.DeviceError, match="not signed"):
        impostor.call("/status")


def test_a_nonce_taken_by_another_request_from_this_mac_is_asked_for_again(monkeypatch):
    """The recorder keeps one nonce per address: the lab's 30 s check and a command in a terminal take each
    other's. One retry each wasn't enough (a Wi-Fi update died at 78%, 8 Oct): up to four, out of step."""
    dev = D.Device("192.168.0.9", "aa" * 32)
    waits, taken = [], {"n": 3}

    def http(method, path, data=None, headers=None):
        if path == "/hello":
            return 200, {}, b'{"nonce":"n1"}'
        if taken["n"]:
            taken["n"] -= 1
            return 401, {}, b'{"error":"unauthorized"}'
        body = b'{"done":8192}'
        return 200, {"X-Octo-Next": "n2", "X-Octo-Sig": dev.sign(f"RESP\n{path}\nn1\n{body.decode()}")}, body

    monkeypatch.setattr(dev, "_http", http)
    monkeypatch.setattr(D.time, "sleep", waits.append)
    assert dev.call("/update/data", {"offset": 0}) == {"done": 8192}
    assert len(waits) == 3 and all(0.1 <= w <= 0.6 for w in waits)
    taken["n"] = 4
    with pytest.raises(D.DeviceError, match="rejected our request 4 times"):
        dev.call("/status")


def test_setup_writes_private_secrets_and_keeps_the_key(tmp_path, monkeypatch):
    wifi = tmp_path / "wifi.h"
    wifi.write_text('#define WIFI_SSID "home"\n#define WIFI_PASS "pa\\"ss"\n')
    monkeypatch.setattr(D, "WIFI_SECRETS", wifi)
    monkeypatch.setattr(D, "SECRETS_H", tmp_path / "rec" / "secrets.h")
    monkeypatch.setattr(D, "CONFIG", tmp_path / "device.json")
    monkeypatch.setattr(D, "HOME", tmp_path)
    assert D.setup() is True
    key = D.load_config()["key"]
    text = D.SECRETS_H.read_text()
    assert f'OCTO_KEY "{key}"' in text and 'WIFI_PASS "pa\\"ss"' in text
    for f in (D.SECRETS_H, D.CONFIG):
        assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert D.setup() is False and D.load_config()["key"] == key
    assert D.setup(new_key=True) is True and D.load_config()["key"] != key


def test_device_endpoints(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from octo import api as API

    monkeypatch.setattr(D, "CONFIG", tmp_path / "device.json")
    monkeypatch.setattr(D, "STATUS", tmp_path / "device_status.json")
    connect(tmp_path / "t.db")
    app = FastAPI()
    app.include_router(API.make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    c = TestClient(app)
    assert c.get("/api/octo/device").json()["configured"] is False

    D.FL.write_private(D.CONFIG, json.dumps({"host": "octo-rec.local", "key": "aa" * 32}))
    D.save_status({"state": "recording", "want_on": True, "card_free": 32_000 * 3600 * 10, "battery_mv": -1, "uptime_ms": 7_200_000})
    got = c.get("/api/octo/device").json()
    assert got["configured"] and got["state"] == "recording" and got["card_hours_left"] == 10.0
    assert got["battery_mv"] is None and got["uptime_h"] == 2.0  # -1: no battery reading wired yet

    sent = []

    class Fake:
        def call(self, path, body=None, raw=False):
            sent.append((path, body))
            return {"state": "off", "want_on": False}

    monkeypatch.setattr(D.Device, "from_config", classmethod(lambda cls: Fake()))
    assert c.post("/api/octo/device/recording", json={"on": False}).json()["state"] == "off"
    assert sent == [("/recording", {"on": False})]
    # Another website open in the same browser can't switch it.
    assert c.post("/api/octo/device/recording", json={"on": True}, headers={"Origin": "https://evil.example"}).status_code == 403

    class Away:
        def call(self, *a, **k):
            raise D.Unreachable("recorder not found on the network")

    monkeypatch.setattr(D.Device, "from_config", classmethod(lambda cls: Away()))
    r = c.post("/api/octo/device/recording", json={"on": True})
    assert r.status_code == 503 and "not found" in r.json()["detail"]


def test_a_transfer_that_breaks_off_is_retried_then_left_for_next_time(lab):
    dev = FakeRecorder(boot=2, state="off")
    dev.chunk(2, 0, 1, 500, pcm(1, False))
    dev.chunk(2, 1, 1, 1500, pcm(1, False))
    real, broken = dev.call, {"left": 3}

    def flaky(path, body=None, raw=False):
        if path == "/chunk" and body["seq"] == 0 and broken["left"]:
            broken["left"] -= 1
            raise D.DeviceError("/chunk: transfer broke off (IncompleteRead)")
        return real(path, body, raw)

    dev.call = flaky
    r = D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert (r["pulled"], r["bad"]) == (1, 1) and (2, 0) in dev.files and (2, 1) not in dev.files
    r = D.sync(lab, dev, log=lambda m: None, speech=speech)  # third try fails, fourth works
    assert (r["pulled"], r["bad"]) == (1, 0) and not dev.files


def test_only_one_sync_at_a_time(lab, monkeypatch):
    import fcntl
    monkeypatch.setattr(D, "SYNC_WAIT_S", 0.3)
    path = lab.execute("PRAGMA database_list").fetchone()[2] + ".device.lock"
    with open(path, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with pytest.raises(D.Busy):
            D.sync(lab, FakeRecorder(), log=lambda m: None, speech=speech)


def test_a_chunk_gone_from_the_card_is_skipped_not_retried_forever(lab):
    dev = FakeRecorder(boot=2, state="off")
    dev.chunk(2, 0, 1, 500, pcm(1, False))
    dev.chunk(2, 1, 1, 1500, pcm(1, False))
    del dev.files[(2, 0)]
    real = dev.call

    def call(path, body=None, raw=False):
        if path == "/chunk" and (body["boot"], body["seq"]) not in dev.files:
            raise D.DeviceError("/chunk: no_chunk", "no_chunk")
        return real(path, body, raw)

    dev.call = call
    r = D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert (r["pulled"], r["bad"]) == (1, 0)
    assert row(lab, "SELECT recording_id FROM device_chunks WHERE seq=0")["recording_id"] == "missing"
    dev.calls.clear()
    D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert not [c for c in dev.calls if c[0] == "/chunk"]


def test_an_empty_chunk_after_a_card_error_does_not_split_the_recording(lab):
    """8 Oct, 21:52: a card error as chunk 87 opened; the recorder dropped it empty ("samples":0,
    "repaired") and went on in 88 from the same moment. Skipped, it split one conversation in two."""
    dev = FakeRecorder(boot=2, state="off")
    dev.chunk(2, 0, 1, 500, pcm(2, True))
    dev.line(2, {"open": 1, "seg": 1, "start_ms": 2500})
    dev.line(2, {"seq": 1, "samples": 0, "repaired": True})
    dev.chunk(2, 2, 1, 2500, pcm(2, True))
    D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert row(lab, "SELECT recording_id, samples FROM device_chunks WHERE seq=1") == {"recording_id": "missing", "samples": 0}
    recs = rows(lab, "SELECT duration_s FROM recordings")
    assert len(recs) == 1 and abs(recs[0]["duration_s"] - 4.0) < 0.1  # one recording, no silence added
    dev.calls.clear()
    D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert not [c for c in dev.calls if c[0] in ("/chunk", "/delete") and c[1].get("seq") == 1]


def test_switching_on_asks_again_once_capture_has_begun():
    """The recorder answers "starting" a few ms before capture begins; the chip shouldn't keep that."""
    from octo import device as D

    class Dev:
        def __init__(self):
            self.calls = []

        def call(self, path, body=None):
            self.calls.append(path)
            return {"state": "starting"} if path == "/recording" else {"state": "recording"}

    waits = []
    dev = Dev()
    assert D.set_recording(dev, True, wait=waits.append)["state"] == "recording"
    assert dev.calls == ["/recording", "/status"] and waits == [1.0]
    off = Dev()
    off.call = lambda path, body=None: off.calls.append(path) or {"state": "off"}
    assert D.set_recording(off, False, wait=waits.append)["state"] == "off" and off.calls == ["/recording"]


# ---- 4 Oct review, batch C --------------------------------------------------------------------

def test_away_from_home_audio_already_on_the_mac_is_joined_after_an_hour(lab):  # M04
    dev = FakeRecorder(boot=2, state="recording", seg=1)
    for i in range(3):
        dev.chunk(2, i, 1, 500 + i * 2000, pcm(2, True))
    D.sync(lab, dev, log=lambda m: None, speech=speech)  # still recording that stretch: it waits
    assert not rows(lab, "SELECT id FROM recordings")

    def away(path, body=None, raw=False):
        raise D.Unreachable("recorder not found on the network")
    dev.call = away
    with pytest.raises(D.Unreachable):
        D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert not rows(lab, "SELECT id FROM recordings")  # pulled just now: not yet
    lab.execute("UPDATE device_chunks SET pulled_at = pulled_at - 3700")
    with pytest.raises(D.Unreachable):
        D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert len(rows(lab, "SELECT id FROM recordings")) == 1


def test_a_boot_folder_without_a_manifest_doesnt_stop_the_others(lab):  # M40
    dev = FakeRecorder(boot=3, state="off")
    dev.files[(1, 0)] = b"H" * 44  # boot 1: a file but no manifest (power cut mid-delete)
    dev.chunk(2, 0, 1, 500, pcm(1, True))
    r = D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert r["pulled"] == 1 and (2, 0) not in dev.files


def test_a_refused_delete_is_retried_not_recorded_as_done(lab):  # M42
    dev = FakeRecorder(boot=2, state="off")
    dev.chunk(2, 0, 1, 500, pcm(1, True))
    real = dev.call
    refuse = {"on": True}

    def call(path, body=None, raw=False):
        if path == "/delete" and "seq" in body and refuse["on"]:
            return {"deleted": False}  # SD.remove failed: the file is still there
        return real(path, body, raw)

    dev.call = call
    D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert row(lab, "SELECT deleted_on_device_at FROM device_chunks")["deleted_on_device_at"] is None
    refuse["on"] = False
    D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert row(lab, "SELECT deleted_on_device_at FROM device_chunks")["deleted_on_device_at"] and not dev.files


def test_a_chunk_that_never_arrives_intact_is_given_up_and_bridged_with_silence(lab):  # M42
    dev = FakeRecorder(boot=2, state="off")
    dev.chunk(2, 0, 1, 500, pcm(2, True))
    dev.chunk(2, 1, 1, 2500, pcm(2, True), corrupt=True)
    dev.chunk(2, 2, 1, 4500, pcm(2, True))
    for _ in range(D.MAX_BAD_TRIES):
        D.sync(lab, dev, log=lambda m: None, speech=speech)
    assert row(lab, "SELECT recording_id FROM device_chunks WHERE seq=1")["recording_id"] == "missing"
    recs = rows(lab, "SELECT id, duration_s FROM recordings")
    assert len(recs) == 1 and abs(recs[0]["duration_s"] - 6.0) < 0.1  # 2 s, 2 s of silence, 2 s


def test_an_encrypted_boot_is_checked_as_stored_then_decrypted(lab, monkeypatch):  # M41
    from octo import devicecrypto as DC
    mac_sk, mac_pk = DC.keypair()
    monkeypatch.setattr(D, "load_config", lambda: {"enc_private": mac_sk.hex()})
    eph = bytes(range(1, 33))  # the recorder's throwaway key for this boot
    epk = DC.x25519(eph, DC.BASE)
    key = __import__("hashlib").sha256(b"octo-rec-v1" + DC.x25519(eph, mac_pk) + epk).digest()  # as the firmware does
    dev = FakeRecorder(boot=3, state="off")
    dev.line(3, {"boot": 3, "boot_id": "a1b2c3d4e5f60718", "enc": "x25519-chacha20", "epk": epk.hex()})
    audio = pcm(2, True)
    stored = DC.crypt_at(key, 3, 0, 44, audio)  # the card holds ciphertext; the manifest hashes it
    dev.chunk(3, 0, 1, 500, stored)
    assert D.sync(lab, dev, log=lambda m: None, speech=speech)["pulled"] == 1
    path = row(lab, "SELECT path FROM recordings")["path"]
    from octo import audio as A
    got = A.read_pcm(P.HOME / path)
    assert got.tobytes() == audio  # the recording is the plain audio


def test_a_reused_boot_number_is_kept_apart(lab):  # M42
    dev = FakeRecorder(boot=5, state="off")
    dev.line(4, {"boot": 4, "boot_id": "1111111111111111"})
    dev.chunk(4, 0, 1, 500, pcm(1, False))
    D.sync(lab, dev, log=lambda m: None, speech=speech)
    dev2 = FakeRecorder(boot=5, state="off")  # a new board (or erased flash) counting from 4 again
    dev2.line(4, {"boot": 4, "boot_id": "2222222222222222"})
    dev2.chunk(4, 0, 1, 500, pcm(1, True))
    real = dev2.call
    dev2.call = lambda path, body=None, raw=False: [{**b, "boot_id": "2222222222222222"} if b["boot"] == 4 else b
                                                  for b in real(path, body, raw)] if path == "/boots" else real(path, body, raw)
    D.sync(lab, dev2, log=lambda m: None, speech=speech)
    assert {r["boot"] for r in rows(lab, "SELECT boot FROM device_chunks")} == {4, 1_000_004}


class Updatable(FakeRecorder):
    """The update endpoints as the firmware has them (recorder.ino setupUpdates)."""

    def __init__(self, fw="rec-2", **kw):
        super().__init__(**kw)
        self.fw, self.image, self.want, self.restarted = fw, b"", None, 0

    def call(self, path, body=None, raw=False):
        import base64
        body = body or {}
        if path == "/status":
            return {**super().call(path, body), "fw": self.fw}
        if path == "/update/begin":
            self.want, self.image = body, b""
            return {"ready": True}
        if path == "/update/data":
            assert body["offset"] == len(self.image)  # in order, nothing skipped
            self.image += base64.b64decode(body["data"])
            return {"done": len(self.image)}
        if path == "/update/finish":
            if hashlib.sha256(self.image).hexdigest() != self.want["sha256"]:
                raise D.DeviceError("/update/finish: sha256", "sha256")
            self.restarted += 1
            return {"restarting": True}
        return super().call(path, body, raw)


def test_a_firmware_update_goes_over_wifi_in_signed_pieces(monkeypatch):
    monkeypatch.setattr(D.time, "sleep", lambda s: None)
    image = os.urandom(3 * D.UPDATE_PIECE + 100)
    dev = Updatable()
    st = D.update_over_wifi(dev, image, log=lambda m: None)
    assert dev.image == image and dev.restarted == 1 and st["fw"] == "rec-2"
    assert dev.want["size"] == len(image)
    with pytest.raises(D.DeviceError, match="over USB"):
        D.update_over_wifi(Updatable(fw="rec-1"), image, log=lambda m: None)  # the old firmware can't take it

    class Corrupting(Updatable):  # a piece changed on the way: the recorder keeps its firmware
        def call(self, path, body=None, raw=False):
            if path == "/update/data" and body["offset"] == 0:
                import base64
                data = base64.b64decode(body["data"])  # flip the first byte: setting it to b"x" left 1 image in 256 unchanged
                body = {**body, "data": base64.b64encode(bytes([data[0] ^ 0xFF]) + data[1:]).decode()}
            return super().call(path, body, raw)
    with pytest.raises(D.DeviceError, match="sha256"):
        D.update_over_wifi(Corrupting(), image, log=lambda m: None)


def test_a_wifi_update_that_went_back_is_not_called_a_success(monkeypatch):  # review 8 Oct, C14
    """Re-flashing the same version: only the slot tells a new firmware from the one the bootloader went back to."""
    monkeypatch.setattr(D.time, "sleep", lambda s: None)
    image = os.urandom(D.UPDATE_PIECE + 10)

    class Slots(Updatable):
        def __init__(self, back: bool):
            super().__init__()
            self.back, self.slot = back, "app0"

        def call(self, path, body=None, raw=False):
            r = super().call(path, body, raw)
            if path == "/update/finish" and not self.back:
                self.slot = "app1"
            return {**r, "slot": self.slot} if path == "/status" else r
    assert D.update_over_wifi(Slots(back=False), image, log=lambda m: None)["went_back"] is False
    assert D.update_over_wifi(Slots(back=True), image, log=lambda m: None)["went_back"] is True


def test_the_version_is_read_from_the_firmware_source():
    assert D.firmware_version().startswith("rec-")


class _StatusOnly:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def call(self, path, body=None, raw=False):
        self.calls.append(path)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_check_asks_for_status_only_and_keeps_it(lab):
    dev = _StatusOnly({"state": "off", "want_on": False})
    assert D.check(lab, dev)["state"] == "off"
    assert dev.calls == ["/status"]  # nothing pulled, no clock line written to the card
    assert D.last_status()["status"]["state"] == "off" and D.last_status()["error"] is None


def test_check_marks_the_recorder_unreachable(lab):
    D.save_status({"state": "recording"})
    assert D.check(lab, _StatusOnly(D.Unreachable("recorder not found"))) is None
    last = D.last_status()
    assert last["error"] == "recorder not found" and last["status"]["state"] == "recording"  # last known kept


def test_check_skips_while_a_sync_has_the_recorder(lab):
    import fcntl
    with open(D._lock_path(lab), "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        dev = _StatusOnly({"state": "off"})
        assert D.check(lab, dev) is None and dev.calls == []


def test_an_update_or_a_signed_call_has_the_recorder_to_itself(tmp_path, monkeypatch):
    """Review 8 Oct, finding 7: one nonce per address for the whole Mac, so while an update (or a network or
    recording change) holds the recorder, the lab's status check skips and anything else waits, then says busy."""
    from octo.db import connect as dbconnect
    conn = dbconnect(tmp_path / "lock.db")
    asked = []

    class Dev:
        def call(self, path, body=None, raw=False):
            asked.append(path)
            return {"state": "off"}
    monkeypatch.setattr(D, "save_status", lambda *a, **k: None)
    with D.holding(conn):
        assert D.check(conn, Dev()) is None and asked == []  # skipped, not queued behind it
        with pytest.raises(D.Busy, match="busy"):
            with D.holding(dbconnect(tmp_path / "lock.db"), wait_s=0.3):
                pass
    assert D.check(conn, Dev()) == {"state": "off"} and asked == ["/status"]


def test_a_wifi_update_that_starts_but_doesnt_work_isnt_called_updated(monkeypatch):  # review 8 Oct, finding 13
    """rec-16 keeps a new firmware only once its microphone and card work: until then /status says pending. The Mac
    asks again for half a minute; one that gets there is updated, one that doesn't is reported, not kept."""
    monkeypatch.setattr(D.time, "sleep", lambda s: None)
    image = os.urandom(D.UPDATE_PIECE + 10)

    class Trial(Updatable):
        def __init__(self, healthy_after: int | None):
            super().__init__()
            self.healthy_after, self.asked = healthy_after, 0

        def call(self, path, body=None, raw=False):
            r = super().call(path, body, raw)
            if path == "/status" and self.restarted:
                self.asked += 1
                ok = self.healthy_after is not None and self.asked > self.healthy_after
                return {**r, "pending": not ok, "state": "off" if ok else "mic_error"}
            return r
    assert D.update_over_wifi(Trial(healthy_after=2), image, log=lambda m: None)["pending"] is False
    st = D.update_over_wifi(Trial(healthy_after=None), image, log=lambda m: None)
    assert st["pending"] is True and D._fault(st) == "microphone didn't start"


def test_a_recorder_with_its_own_name_is_found_and_remembered(tmp_path, monkeypatch):
    """Review 8 Oct, finding 12: rec-16 answers to octo-a1b2.local, not the shared octo-rec.local. The Mac learns the
    name from its status ("device"), and finds it under that name once the old one finds nothing."""
    monkeypatch.setattr(D, "CONFIG", tmp_path / "device.json")
    monkeypatch.setattr(D, "STATUS", tmp_path / "status.json")
    D.update_config(lambda c: c.update(key="00" * 32, host="octo-rec.local"))
    assert D.unit_host({"device": "octo-rec"}) is None and D.unit_host({"device": "octo-a1b2"}) == "octo-a1b2.local"
    D.save_status({"device": "octo-rec", "fw": "rec-15"})
    assert D.load_config()["host"] == "octo-rec.local"  # rec-15: still the shared name
    D.save_status({"device": "octo-a1b2", "fw": "rec-16"})
    assert D.load_config()["host"] == "octo-a1b2.local"
    # A Mac that remembers the old name: it tries the recorder's own (from its last status), then the shared one.
    monkeypatch.setattr(D, "resolve", lambda h: {"octo-a1b2.local": "10.0.0.9"}.get(h))
    D.update_config(lambda c: c.update(host="octo-rec.local"))
    dev = D.Device("octo-rec.local", "00" * 32)
    assert dev._address() == "10.0.0.9" and dev.host == "octo-a1b2.local"
    assert D.load_config()["host"] == "octo-a1b2.local"


def test_the_filler_model_goes_into_the_build_only_from_the_lab(tmp_path, monkeypatch):
    """B20: the owner's model is copied into the recorder's build from the lab's data, and taken out of the build
    when the lab has none (it's never in git); the threshold sent to the recorder is checked first."""
    from octo import device as D
    lab, build = tmp_path / "data" / "filler" / "octo_filler_model.h", tmp_path / "fw" / "octo_filler_model.h"
    build.parent.mkdir()
    monkeypatch.setattr(D, "FILLER_MODEL", lab)
    monkeypatch.setattr(D, "FILLER_H", build)
    build.write_text("// a stale one")
    assert not D.filler_model() and not build.exists()
    lab.parent.mkdir(parents=True)
    lab.write_text("const float KWS_THRESHOLD = 0.96f;")
    assert D.filler_model() and build.read_text() == lab.read_text()

    sent = []

    class Dev:
        def call(self, path, body=None):
            sent.append((path, body))
            return {"filler_model": True, "filler_on": body["on"]}
    assert D.set_filler(Dev(), True, 0.9)["filler_on"]
    assert sent == [("/filler", {"on": True, "threshold": 0.9})]
    for bad in (0.2, 1.0):
        with pytest.raises(ValueError):
            D.set_filler(Dev(), True, bad)
