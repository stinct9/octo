"""Other people's voiceprints only with their agreement recorded, in a new lab (product plan 4.6). The owner's
lab keeps their 30 Sep decision: every labelled speaker's voiceprint (D1)."""

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import labconfig as LC
from octo import voice as V
from octo.api import make_router
from octo.db import connect, row


def lab(tmp_path, name="t.db"):
    conn = connect(tmp_path / name)
    me = V.ensure_person(conn, "Asha", is_self=True)
    other = V.ensure_person(conn, "Ravi")
    return conn, me, other


def test_jays_lab_keeps_every_labelled_voice(tmp_path, monkeypatch):
    monkeypatch.delitem(LC.TEST_OVERRIDES, "voiceprints_need_consent")
    conn = connect(tmp_path / "old.db")
    conn.execute("DELETE FROM app_config")  # as it was before 7 Oct, with reviews: the owner's
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, queued_at) VALUES ('r', 'insight', 's', 'h', 'done', 0)")
    conn.execute("PRAGMA user_version = 0")  # and from before the migrations were versioned
    conn.close()
    conn = connect(tmp_path / "old.db")
    assert not V.needs_consent(conn)
    assert V.may_keep_print(conn, V.ensure_person(conn, "Kiran")["id"])


def test_a_new_lab_keeps_someone_elses_voice_only_with_their_agreement(tmp_path, monkeypatch):
    monkeypatch.delitem(LC.TEST_OVERRIDES, "voiceprints_need_consent")
    conn, me, other = lab(tmp_path)
    assert V.needs_consent(conn)
    assert V.may_keep_print(conn, me["id"])  # your own voice: always
    assert not V.may_keep_print(conn, other["id"])
    c = V.record_consent(conn, other["id"], "in writing", "signed the form")
    assert c["how"] == "in writing" and V.may_keep_print(conn, other["id"])
    V.add_voiceprint(conn, other["id"], np.ones(4, dtype=np.float32), 30.0, "label:s1:B")
    assert V.withdraw_consent(conn, other["id"]) == 1
    assert not V.may_keep_print(conn, other["id"])
    assert row(conn, "SELECT count(*) n FROM voiceprints WHERE person_id=?", other["id"])["n"] == 0
    assert row(conn, "SELECT name FROM people WHERE id=?", other["id"])["name"] == "Ravi"  # their name stays


def test_the_people_page_records_and_withdraws_agreement(tmp_path, monkeypatch):
    monkeypatch.delitem(LC.TEST_OVERRIDES, "voiceprints_need_consent")
    conn, me, other = lab(tmp_path)
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    c = TestClient(app, base_url="http://127.0.0.1:8765", headers={"Origin": "http://127.0.0.1:8765"})
    people = {p["name"]: p for p in c.get("/api/octo/people").json()}
    assert people["Ravi"]["needs_consent"] and people["Ravi"]["consent"] is None
    assert not people["Asha"]["needs_consent"]  # the owner
    assert c.post(f"/api/octo/people/{other['id']}/consent", json={"how": "asked in person"}).json()["consent"]["how"] == "asked in person"
    assert {p["name"]: p for p in c.get("/api/octo/people").json()}["Ravi"]["consent"]["how"] == "asked in person"
    V.add_voiceprint(conn, other["id"], np.ones(4, dtype=np.float32), 30.0, "label:s1:B")
    assert c.post(f"/api/octo/people/{other['id']}/consent/withdraw", json={}).json() == {"voiceprints_deleted": 1}
    assert c.post("/api/octo/people/nobody/consent", json={}).status_code == 404


def test_a_merge_keeps_the_agreement_with_the_voiceprints_and_deletions_are_overwritten(tmp_path, monkeypatch):
    """Review 8 Oct, finding 14: renaming someone to a name already in People combined their prints but not their
    agreement, so the page asked for one already given. And a withdrawn print stayed in SQLite's free pages."""
    monkeypatch.delitem(LC.TEST_OVERRIDES, "voiceprints_need_consent")
    conn = connect(tmp_path / "m.db")
    asha = V.ensure_person(conn, "Asha")
    V.record_consent(conn, asha["id"], "asked in person")
    V.ensure_person(conn, "Asha S")
    done, left = V.rename(conn, "Asha", "Asha S")
    assert done == "merged" and left["consent"] and V.may_keep_print(conn, left["id"])
    assert conn.execute("PRAGMA secure_delete").fetchone()[0] == 1
