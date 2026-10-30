"""API: run control, SSE streaming and replay, host guard, safe file access."""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import bench.api as api_mod
import bench.runner as runner_mod
from bench.runner import Outcome, Runner, Step
from bench.store import RunStore


class Fake(Step):
    def __init__(self, sid, fn, needs_user=False):
        self.id, self.title, self.fn, self.needs_user = sid, sid.title(), fn, needs_user

    def run(self, ctx):
        return self.fn(ctx)


def gate(ctx):
    ctx.wait_for_user("press ready")
    return Outcome("pass", "recorded")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_mod, "RUNS", tmp_path)
    monkeypatch.setattr(api_mod, "AUDIO", tmp_path / "audio")
    steps = {"a": Fake("a", lambda ctx: Outcome("pass", "ok")),
             "mic": Fake("mic", gate, needs_user=True),
             "slow": Fake("slow", lambda ctx: (time.sleep(0.5), Outcome("pass", "ok"))[1])}
    runner = Runner(store=RunStore(tmp_path))
    app = api_mod.create_app(runner, steps=steps, run_all=["a", "mic"], poll_device=False)
    with TestClient(app) as c:
        c.runner = runner
        yield c


def sse_events(client, headers=None):
    """Read the SSE stream until run.finished (once=true)."""
    out = []
    with client.stream("GET", "/api/events?once=true", headers=headers or {}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        block = {}
        for line in r.iter_lines():
            if line == "":
                if block.get("event") == "run":
                    out.append((block.get("id"), json.loads(block["data"])))
                block = {}
            elif not line.startswith(":") and ":" in line:
                k, _, v = line.partition(":")
                block[k] = v.strip()
    return out


def wait_state(client, state, timeout=3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.get("/api/status").json()["state"] == state:
            return
        time.sleep(0.02)
    raise AssertionError(f"never reached {state}")


def test_steps_listed_in_run_all_order(client):
    assert [s["id"] for s in client.get("/api/steps").json()] == ["a", "mic"]


def test_run_with_human_gate_over_http(client):
    r = client.post("/api/runs", json={"steps": ["a", "mic"]})
    assert r.status_code == 202
    run_id = r.json()["run_id"]
    wait_state(client, "waiting_for_user")
    assert client.post("/api/runs", json={"steps": ["a"]}).status_code == 409  # busy
    assert client.post("/api/runs/current/ready").status_code == 200
    events = sse_events(client)
    assert events[-1][1]["type"] == "run.finished" and events[-1][1]["data"]["status"] == "pass"
    assert all(e["run_id"] == run_id for _, e in events)
    assert events[0][0] == f"{run_id}:1"  # SSE id = run:seq
    assert client.post("/api/runs/current/ready").status_code == 409  # nothing waiting


def test_resume_from_last_event_id(client):
    client.post("/api/runs", json={"steps": ["a"]})
    full = sse_events(client)
    last_id = full[2][0]
    resumed = sse_events(client, headers={"Last-Event-ID": last_id})
    assert [e["seq"] for _, e in resumed] == [e["seq"] for _, e in full[3:]]
    # An ID from some other run gets the whole current run.
    assert len(sse_events(client, headers={"Last-Event-ID": "20000101-000000:5"})) == len(full)


def test_live_stream_receives_events_from_worker_thread(client):
    got = []

    def listen():
        got.extend(sse_events(client))
    t = threading.Thread(target=listen)
    client.post("/api/runs", json={"steps": ["slow"]})
    t.start()
    t.join(5)
    types = [e["type"] for _, e in got]
    assert "step.started" in types and types[-1] == "run.finished"


def test_cancel_over_http(client):
    client.post("/api/runs", json={"steps": ["mic"]})
    wait_state(client, "waiting_for_user")
    assert client.post("/api/runs/current/cancel").status_code == 200
    events = sse_events(client)
    assert events[-1][1]["data"]["status"] == "cancelled"
    assert client.post("/api/runs/current/cancel").status_code == 409


def test_unknown_step_rejected(client):
    assert client.post("/api/runs", json={"steps": ["nope"]}).status_code == 400


def test_foreign_host_header_rejected(client):
    """DNS rebinding: a page on evil.example resolving to 127.0.0.1 must not get in."""
    assert client.get("/api/status", headers={"Host": "evil.example"}).status_code == 400


def test_recording_only_by_run_id(client, tmp_path):
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "bench_20260930-120000.wav").write_bytes(b"RIFF")
    assert client.get("/api/runs/20260930-120000/recording").status_code == 200
    assert client.get("/api/runs/..%2F..%2Fetc%2Fpasswd/recording").status_code == 404
    assert client.get("/api/runs/20260930-999999/recording").status_code == 404


def test_history_endpoints(client):
    client.post("/api/runs", json={"steps": ["a"]})
    sse_events(client)
    runs = client.get("/api/runs").json()
    assert runs[0]["status"] == "pass" and runs[0]["steps"] == {"a": "pass"}
    stored = client.get(f"/api/runs/{runs[0]['run_id']}/events").json()
    assert stored[-1]["type"] == "run.finished"


def test_another_website_cannot_drive_the_bench():
    from bench.api import create_app

    c = TestClient(create_app(poll_device=False, phone_access=False))
    evil = {"origin": "https://evil.example"}
    assert c.post("/api/runs/current/cancel", headers=evil).status_code == 403
    assert c.post("/api/runs/current/ready", headers=evil).status_code == 403
    assert c.post("/api/runs", json={"steps": ["board"]}, headers=evil).status_code == 403
