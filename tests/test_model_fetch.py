"""The speech models from the setup page (open-source plan I3, I4): the background fetcher and the Hugging Face
sign-in, with the network and the cache replaced."""

import json
import time
from types import SimpleNamespace

from octo import models as MD
from octo.models import missing_for as REAL_MISSING_FOR  # before conftest's models_downloaded replaces it


def test_the_fetcher_gets_whats_missing_and_then_runs_done(monkeypatch):
    have = {"a/one"}
    monkeypatch.setattr(MD, "required", lambda conn: ["a/one", "b/two", "pyannote/segmentation-3.0"])
    monkeypatch.setattr(MD, "cached", lambda repo: repo in have)
    fetched, done = [], []

    def fetch(repo):
        fetched.append(repo)
        if repo in MD.GATED:
            return {"ok": False, "gated": True}
        have.add(repo)
        return {"ok": True}

    f = MD.Fetcher(lambda: None, fetch=fetch, done=lambda: done.append(1))
    assert f.start()
    while f.running:
        time.sleep(0.01)
    assert fetched == ["b/two", "pyannote/segmentation-3.0"] and done == []  # one still waits for access
    states = {m["repo"]: m["state"] for m in f.report()["models"]}
    assert states == {"a/one": "ready", "b/two": "ready", "pyannote/segmentation-3.0": "needs_access"}
    have.add("pyannote/segmentation-3.0")  # signed in, fetched
    assert not f.start()  # nothing missing: nothing to do
    have.discard("pyannote/segmentation-3.0")
    f.fetch = lambda repo: (have.add(repo), {"ok": True})[1]
    assert f.start()
    while f.running:
        time.sleep(0.01)
    assert done == [1]


def test_signing_in_sends_the_token_in_the_environment_never_the_arguments():
    seen = {}

    def run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return SimpleNamespace(stdout="Logged in.\n" + json.dumps({"name": "asha", "access": {r: "ok" for r in MD.GATED}}))

    got = MD.account("hf_secret_token", run=run)
    assert got["name"] == "asha" and set(got["access"]) == set(MD.GATED)
    assert "hf_secret_token" not in " ".join(seen["cmd"]) and seen["env"]["OCTO_HF_TOKEN"] == "hf_secret_token"
    assert seen["env"]["HF_HUB_OFFLINE"] == "0"
    assert MD.account(run=lambda cmd, **kw: SimpleNamespace(stdout=""))["error"] == "couldn't reach Hugging Face"


def test_the_sample_is_reviewed_as_the_owner_and_kept_outside_the_database(tmp_path, monkeypatch):
    from octo import owner as O
    from octo import providers as PV
    from octo import sample as SM
    from octo import settings as ST
    from octo.db import connect
    monkeypatch.setattr(SM, "RESULT", tmp_path / "sample.json")
    conn = connect(tmp_path / "t.db")
    monkeypatch.setattr(ST, "reader", lambda c: None)
    import pytest
    with pytest.raises(ValueError):
        SM.run(conn)
    monkeypatch.setattr(ST, "reader", lambda c: {"kind": "anthropic", "model": "m"})
    O.save(conn, {"name": "Asha"})
    seen = {}

    def review(d, names, me, params, rubric):
        from octo.stages import count
        count(d, {"min_overlap_s": 0.3})  # the numbers the real review makes from it (19 Oct: it lacked fields)
        seen.update(names=names, me=me, own=sum(t["end"] - t["start"] for t in d["turns"] if t["speaker"] in me))
        return {"skipped": None, "summary": "s", "memo": {"k": 1}}

    tables = lambda: sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"))
    counts = lambda: [conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables()]
    before = counts()
    got = SM.run(conn, review=review)
    assert seen["names"] == {"A": "Asha", "B": "Meera"} and seen["me"] == {"A"} and seen["own"] >= 20
    assert "memo" not in got["review"] and SM.last()["review"]["summary"] == "s"
    assert counts() == before  # nothing written to the lab's database


def test_steps_wait_for_their_models_instead_of_failing(tmp_path, monkeypatch):
    """Onboarding review 7 (28 Oct): a memo added while the 10 GB come down waits quietly, step by step."""
    from octo import pipeline as P
    from octo import settings as ST
    from octo.db import connect
    monkeypatch.setattr(MD, "missing_for", REAL_MISSING_FOR)
    have = set()
    monkeypatch.setattr(MD, "cached", lambda repo: repo in have)
    conn = connect(tmp_path / "t.db")
    for rid, stage in (("run_t", "transcribe"), ("run_i", "identify"), ("run_n", "numbers")):
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, queued_at) VALUES (?, ?, 'ses_x', 'h', 'queued', 0)",
                     (rid, stage))
    assert MD.missing_for(conn, "numbers") == [] and P.next_run(conn)["id"] == "run_n"  # needs no model: runs
    conn.execute("DELETE FROM stage_runs WHERE id='run_n'")
    assert P.next_run(conn) is None  # the rest wait, queued, not failed
    assert MD.missing_for(conn, "identify") == [MD.VOICEPRINT_FILE[0]]
    have.update(ST.selected(conn, ST.BY_STAGE["transcribe"]).hf)
    assert P.next_run(conn)["id"] == "run_t"
