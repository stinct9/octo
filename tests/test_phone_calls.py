"""B25: a call recorded on the iPhone, shared from Notes to the Octo app, reaches the lab as a phone memo (B8's path)
and the pages say it was a phone call. Synthetic audio only."""

import shutil
import subprocess

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import octo.api as API
from octo import audio as A
from octo import pipeline as P
from octo.db import connect, rows
from tests.test_octo import lab  # noqa: F401 (the fixture)


@pytest.mark.skipif(not shutil.which("afconvert"), reason="macOS only")
def test_a_shared_call_recording_is_a_phone_memo_the_pages_call_a_phone_call(lab, tmp_path):
    tmp = P.HOME
    lab["mp"].setattr(API, "DATA", tmp / "data")
    app = FastAPI()
    app.include_router(API.make_router(lambda: connect(tmp / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    c = TestClient(app)

    def m4a(name, hz):  # AAC in an .m4a, two channels: whatever Notes gives, the lab keeps one
        src = tmp_path / f"{hz}.wav"
        t = np.arange(16000 * 20) / 16000
        x = (0.3 * np.sin(2 * np.pi * hz * t)).astype(np.float32)
        A.write_pcm(src, (np.stack([x, x], 1) * 32767).astype(np.int16))
        out = tmp_path / name
        subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", str(src), str(out)], check=True)
        return out.read_bytes()

    def up(name, body):  # as the Octo app sends it (mobile/ios/App/App/Memos.swift)
        return c.post(f"/api/octo/recordings?name={name}&source=phone&modified=1790000000000", content=body,
                      headers={"x-octo-upload": "1"})

    assert up("Call with Mum.m4a", m4a("call.m4a", 300)).status_code == 200
    assert up("New Recording 12.m4a", m4a("memo.m4a", 500)).status_code == 200  # Voice Memos, as before
    conn = lab["conn"]
    recs = rows(conn, "SELECT source, original, path FROM recordings ORDER BY original")
    assert [(r["source"], r["original"]) for r in recs] == [("phone", "Call with Mum.m4a"), ("phone", "New Recording 12.m4a")]
    assert all(A.channels(tmp / r["path"]) == 1 for r in recs)  # mixed down, as any memo
    lab["run"]()
    listed = c.get("/api/octo/sessions").json()
    by_source = sorted(s["source"] for s in listed)
    assert by_source == ["phone", "phone", "phone call", "phone call"]  # two conversations each (the fake finder)
    call = next(s for s in listed if s["source"] == "phone call")
    assert c.get(f"/api/octo/sessions/{call['id']}").json()["source"] == "phone call"
