"""The Pipeline page: model choices per step (octo/settings.py)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import settings as ST
from octo.api import make_router
from octo.db import connect
from octo.pipeline import input_hash
from octo.stages import STAGES, params_for


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(ST, "_hf_cached", lambda repo: True)
    monkeypatch.setattr(ST, "_ollama_models", lambda: {"qwen3:8b", "gemma3:12b"})
    return connect(tmp_path / "t.db")


def test_with_nothing_chosen_every_step_runs_exactly_as_before(conn):
    """Deploying the page must not re-run anything: each default is the stage's own parameters."""
    for step in ST.STEPS:
        stage = STAGES[step.stage]
        assert params_for(stage, ST.overrides(conn, step.stage)) == params_for(stage)
        if step.choices:
            assert ST.selected(conn, step) is ST.default(step) is step.choices[0]


def test_a_choice_changes_the_steps_parameters_so_it_reruns(conn):
    stage = STAGES["speakers"]
    before = input_hash(stage, params_for(stage, ST.overrides(conn, "speakers")), "s1", ["t1"])
    ST.choose(conn, "speakers", "pyannote-community-1")
    after_params = params_for(stage, ST.overrides(conn, "speakers"))
    assert after_params["model"] == "pyannote/speaker-diarization-community-1"
    assert after_params["clustering_threshold"] is None  # community-1 keeps its own clustering
    assert input_hash(stage, after_params, "s1", ["t1"]) != before
    ST.choose(conn, "speakers", "pyannote-3.1")  # and back
    assert input_hash(stage, params_for(stage, ST.overrides(conn, "speakers")), "s1", ["t1"]) == before


def test_only_listed_choices_that_can_run_here(conn, monkeypatch):
    with pytest.raises(KeyError):
        ST.choose(conn, "numbers", "anything")  # no model to choose
    with pytest.raises(ValueError):
        ST.choose(conn, "transcribe", "openai/whisper-cloud")  # not in the registry
    monkeypatch.setattr(ST, "_ollama_models", lambda: {"qwen3:8b"})
    with pytest.raises(ValueError, match="ollama pull gemma3:12b"):
        ST.choose(conn, "insight", "gemma3-12b")
    monkeypatch.setattr(ST, "_hf_cached", lambda repo: repo != "pyannote/speaker-diarization-community-1")
    with pytest.raises(ValueError, match="not downloaded"):
        ST.choose(conn, "speakers", "pyannote-community-1")
    assert ST.overrides(conn, "insight") == {"model": "claude-opus-5-5", "effort": "high"}  # nothing changed


def test_the_page_through_the_api(conn, tmp_path):
    kicks = []
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"),
                                   processor=type("P", (), {"kick": lambda self: kicks.append(1)})()))
    c = TestClient(app)
    steps = c.get("/api/octo/pipeline").json()
    assert [s["stage"] for s in steps] == list(STAGES)  # every step, in pipeline order
    tr = next(s for s in steps if s["stage"] == "transcribe")
    assert tr["selected"] == "qwen3-asr-1.7b" and {c["id"] for c in tr["choices"]} == {"qwen3-asr-1.7b", "whisper-large-v3"}
    assert next(s for s in steps if s["stage"] == "numbers")["fixed"]
    later = c.get("/api/octo/pipeline/transcribe/impact").json()["later"]
    assert later[0] == "Who said what" and "Separate speakers" not in later  # pyannote needs only the audio
    assert c.get("/api/octo/pipeline/speakers/impact").json()["later"][0] == "Who said what"
    assert c.post("/api/octo/pipeline/transcribe", json={"choice": "nope"}).status_code == 422
    assert c.post("/api/octo/pipeline/numbers", json={"choice": "x"}).status_code == 404
    assert c.post("/api/octo/pipeline/transcribe", json={"choice": "whisper-large-v3"},
                  headers={"origin": "https://evil.example"}).status_code == 403
    assert kicks == []
    r = c.post("/api/octo/pipeline/transcribe", json={"choice": "whisper-large-v3"})
    assert r.status_code == 200 and r.json()["selected"] == "whisper-large-v3" and kicks == [1]
    assert c.post("/api/octo/pipeline/transcribe", json={"choice": "whisper-large-v3"}).status_code == 200
    assert kicks == [1]  # the same choice again changes nothing


def test_choosing_a_local_model_starts_ollama_and_the_login_item_follows_the_choice(conn, tmp_path, monkeypatch):
    """Tech review 5 Nov, risk 6: Ollama's login item only while a step runs a local model."""
    import octo.db as DB
    from octo import login_item as LI
    monkeypatch.setattr(DB, "connect", lambda *a, **k: connect(tmp_path / "t.db"))
    monkeypatch.setattr(LI, "AGENTS", tmp_path / "agents")
    monkeypatch.setattr(LI, "RELAY_COPY", tmp_path / "relay" / "relay.py")
    monkeypatch.setattr("octo.device.RELAY_KEY", tmp_path / "relay" / "relay.key")
    monkeypatch.setattr(LI, "has_ollama", lambda: True)
    monkeypatch.setattr(LI.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr("octo.mackey.key", lambda path: "k")
    plists = lambda: sorted(p.name for p in (tmp_path / "agents").glob("*.plist"))
    assert not LI.wants_ollama()  # Claude Code reviews, as in the first lab
    LI.install()
    assert plists() == ["com.octo.lab.plist", "com.octo.relay.plist"]
    # Ollama stopped: the page still offers the local model, and choosing it starts Ollama
    up = []
    monkeypatch.setattr(ST, "_ollama_models", lambda: {"qwen3:8b"} if up else None)
    page = next(s for s in ST.describe(conn) if s["stage"] == "insight")
    assert next(c for c in page["choices"] if c["id"] == "qwen3-8b")["unavailable"] is None
    monkeypatch.setattr(LI, "start_ollama", lambda: up.append(1) or True)
    ST.choose(conn, "insight", "qwen3-8b")
    assert up and LI.wants_ollama()
    LI.install()
    assert plists() == ["com.octo.lab.plist", "com.octo.ollama.plist", "com.octo.relay.plist"]
    conn.execute("DELETE FROM settings")  # back to Claude Code
    LI.install()  # no local model left: the next install lets it go
    assert plists() == ["com.octo.lab.plist", "com.octo.relay.plist"]
    monkeypatch.setattr(LI, "has_ollama", lambda: False)
    assert "ollama.com" in ST.unavailable(ST.BY_STAGE["insight"].choices[1], lambda: None, startable=True)
