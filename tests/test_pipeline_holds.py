"""Holds that wait for the Mac or the owner instead of failing step after step (architecture review 5 Nov): heavy steps
while macOS says memory is critical or the disk is nearly full (risk 6), every step on a connection whose key was
refused, and the daily backup on a nearly full disk."""

import json
import os
import time

import pytest

from octo import backup as BK
from octo import pipeline as P
from octo import privacy as PR
from octo import providers as PV
from octo import sessions as SV
from octo.db import connect
from octo.pipeline import short_of as reads_the_mac  # before conftest's mac_has_room replaces it

SID = "ses_20261001_aaaaaaaa"


def _lab(tmp_path):
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO recordings (id, path, source, started_at, duration_s, created_at) VALUES ('r', 'a', 'phone', 1790863200, 900, 0)")
    conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, status, created_at) "
                 "VALUES (?, 'r', 0, 600, 60, 'active', 0)", (SID,))
    return conn


def _queue(conn, rid, stage, params="{}", at=1.0):
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, params, queued_at) "
                 "VALUES (?, ?, ?, ?, 'queued', ?, ?)", (rid, stage, SID, rid, params, at))


def test_heavy_steps_wait_while_memory_is_critical_and_the_page_says_why(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    monkeypatch.setattr(P, "short_of", reads_the_mac)  # through the pressure level and free space, set here
    monkeypatch.setattr(P, "pressure_level", lambda: 4)
    monkeypatch.setattr(PR, "free_gb", lambda: 100.0)
    monkeypatch.setattr("octo.models.missing_for", lambda conn, stage: [])
    _queue(conn, "tr", "transcribe")
    _queue(conn, "id", "identify", at=2.0)
    now = time.time()
    assert P.next_run(conn, now)["id"] == "id"  # the light step goes; the speech model waits
    held = P.heavy_held(conn, now)
    assert held["why"] == "the Mac is short of memory" and held["until"] == pytest.approx(now + P.HEAVY_HOLD_S)
    assert P.next_due(conn, now) == pytest.approx(held["until"])  # the server looks again then
    todo = SV.todo(conn, SID)
    assert todo["kind"] == "processing" and todo["detail"].startswith("waiting: the Mac is short of memory · tries again after")
    monkeypatch.setattr(P, "pressure_level", lambda: 1)
    assert P.next_run(conn, now + 60)["id"] == "id"  # held for its few minutes, not looked at on every pass
    assert P.next_run(conn, now + P.HEAVY_HOLD_S + 1)["id"] == "tr"  # then it goes
    monkeypatch.setattr(PR, "free_gb", lambda: 1.5)
    assert P.short_of() == "only 1.5 GB free on the disk"


def test_a_refused_key_holds_its_steps_once_until_models_and_keys_change(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    PV.add("anthropic", label="Mine", api_key="sk-test")
    reader = {"kind": "anthropic", "connection": "mine", "model": "claude-sonnet-5-5"}
    params = json.dumps({"model": "claude-sonnet-5-5", "reader": reader})
    now = time.time()
    err = "ProviderError: Anthropic didn't accept the API key: check it under Settings → Models and keys"
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, params, error, queued_at, finished_at) "
                 "VALUES ('f', 'insight', ?, 'h', 'failed', ?, ?, 0, ?)", (SID, params, err, now + 5))
    for i in range(2):
        conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, status, created_at) "
                     "VALUES (?, 'r', ?, 600, 60, 'active', 0)", (f"ses_x{i}", 700 + i))
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, params, queued_at) "
                     "VALUES (?, 'insight', ?, ?, 'queued', ?, 1)", (f"q{i}", f"ses_x{i}", f"q{i}", params))
    assert P.connection_refused(conn, "mine") == now + 5
    assert P.next_run(conn, now) is None  # the others wait: not each failing three times
    assert P.due_at(conn, "insight", SID, "h") < float("inf")  # not counted: looked at again in 5 min
    assert "The key was refused" in SV.todo(conn, SID)["hint"]
    assert "the key for this model was refused" in SV.todo(conn, "ses_x0")["detail"]
    os.utime(PV.CONFIG, (now + 10, now + 10))  # the owner pasted a new key
    assert P.connection_refused(conn, "mine") is None and P.next_run(conn, now)["id"] in ("q0", "q1")


def test_the_daily_backup_waits_on_a_nearly_full_disk(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    monkeypatch.setattr(PR, "free_gb", lambda: 1.2)
    with pytest.raises(RuntimeError, match="held: 1.2 GB free"):
        BK.daily(conn, tmp_path / "backups")
    assert not (tmp_path / "backups").exists()
    monkeypatch.setattr(PR, "free_gb", lambda: 50.0)
    assert BK.daily(conn, tmp_path / "backups")


def test_mlx_is_capped_in_the_transcription_worker(tmp_path, monkeypatch):
    """Risk 6: MLX's cache grew unbounded in the worker (footprint 12.0 GB on a 2.8 min file, 10.0 GB capped)."""
    from octo import asr as AS
    from octo import worker as W
    from octo.stages import Stage
    mem, cache = AS.cap_mlx()
    import mlx.core as mx
    assert mx.set_memory_limit(mem) == mem and mx.set_cache_limit(cache) == cache  # what it set
    assert cache == AS.MLX_CACHE_BYTES and 0 < mem < int(os.popen("sysctl -n hw.memsize").read())
    conn = _lab(tmp_path)
    capped = []
    monkeypatch.setattr(AS, "cap_mlx", lambda: capped.append(1))
    monkeypatch.setattr(W, "connect", lambda: connect(tmp_path / "t.db"))
    monkeypatch.setattr(W, "inputs_for", lambda conn, run: {})
    monkeypatch.setattr(W, "STAGES", {s: Stage(s, 1, "session", None, {}, lambda i, p: {}) for s in ("transcribe", "identify")})
    for stage in ("transcribe", "identify"):
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, params, queued_at) "
                     "VALUES (?, ?, ?, 'h', 'queued', '{}', 0)", (stage, stage, SID))
        assert W.main(stage) == 0
    assert capped == [1]  # transcribe only: the others don't load MLX
