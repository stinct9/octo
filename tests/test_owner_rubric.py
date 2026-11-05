"""You and your principles (product plan 4.2): the owner's profile, where their principles live, the
page's routes, and a new lab against the owner's. The owner's real rubric is never written: every test points the
files somewhere of its own."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import labconfig as LC
from octo import models as MD
from octo import insight as I
from octo import owner as O
from octo import review_any as RA
from octo import rubric as RB
from octo.api import make_router
from octo.db import connect

JAYS = """# Rubric

## Decision (2026-09-30)

Conduct first.

## How the review applies it

Read the whole conversation first.

## Conduct principles

### On work

1. Do the work well, then let it rest.

### On people

2. Ignore insults. They pass on their own.

## Examples (optional)

Nothing yet.
"""


@pytest.fixture
def jays_file(tmp_path, monkeypatch):
    f = tmp_path / "pipeline-rubric.md"
    f.write_text(JAYS)
    monkeypatch.setattr(I, "RUBRIC", f)
    return f


@pytest.fixture
def new_lab_files(tmp_path, monkeypatch):
    """A buyer's install: the shipped rubric is only the template."""
    monkeypatch.setattr(I, "RUBRIC", tmp_path / "no-shipped-rubric.md")
    return tmp_path


def test_the_profile_keeps_known_fields_and_a_pronoun_it_knows(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert O.load(conn) == {}
    got = O.save(conn, {"name": " Asha ", "pronoun": "she", "about": "", "shoe_size": 7})
    assert got == {"name": "Asha", "pronoun": "she"} and O.load(conn) == got
    with pytest.raises(ValueError):
        O.save(conn, {"name": "X", "pronoun": "it"})
    f = O.forms({"pronoun": "they"})
    assert (f["They"], f["their"], f["hasnt"], f["theyre"]) == ("They", "their", "haven't", "they're")
    assert O.forms({})["they"] == "they"  # nobody said: "they"


def test_a_new_lab_starts_empty_and_its_principles_go_in_its_own_file(new_lab_files):
    s = RB.sections()
    assert s["principles"] == "" and s["parsed"] == [] and s["file"].endswith("rubric-template.md")
    out = RB.save("1. Listen before answering.\n\n### At home\n\n2. Keep promises small.", "")
    assert RB.OWN.exists() and not I.RUBRIC.exists()  # the shipped file is never created or touched
    assert [p["n"] for p in out["parsed"]] == [1, 2] and out["rules"] == ""
    assert I.principles(RB.text())[1]["text"] == "Keep promises small."
    RB.save("1. Listen before answering.", "Look at how I disagree.")
    assert I.review_rules(RB.text()) == "Look at how I disagree." and len(I.principles(RB.text())) == 1


def test_jays_file_is_copied_to_his_data_on_save_and_keeps_everything_else(jays_file):
    s = RB.sections()
    assert s["parsed"] == [{"n": 1, "title": "Do the work well, then let it rest."},
                           {"n": 2, "title": "Ignore insults. They pass on their own."}]
    assert "### On work" in s["principles"]  # his headings come with the section
    RB.save(s["principles"].replace("They pass on their own.", "They pass on their own, always."), s["rules"])
    assert jays_file.read_text() == JAYS  # the repo's file, byte for byte
    t = RB.OWN.read_text()
    assert RB.path() == RB.OWN and RB.sections()["file"] == str(RB.OWN)
    assert "## Decision (2026-09-30)\n\nConduct first." in t and "## Examples (optional)\n\nNothing yet." in t
    assert "### On people" in t and "They pass on their own, always." in t
    assert I.review_rules(t) == "Read the whole conversation first."


def test_the_review_prompt_without_rules_and_with_the_owners_never():
    s = RA.system_prompt({"name": "Asha", "pronoun": "she", "never": "flattering the inspector"}, "")
    assert "want the review done" not in s and "Each must pass every principle: never flattering the inspector." in s
    assert "How she wants the review done, in her words:\nLook closely." in RA.system_prompt({"name": "Asha", "pronoun": "she"}, "Look closely.")


@pytest.fixture
def client(tmp_path):
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    return TestClient(app, base_url="http://127.0.0.1:8765", headers={"Origin": "http://127.0.0.1:8765"})


def test_a_new_lab_is_walked_through_setup(client, new_lab_files, monkeypatch):
    monkeypatch.delitem(LC.TEST_OVERRIDES, "claude_code")
    st = client.get("/api/octo/settings/setup").json()
    assert [s["id"] for s in st["steps"]] == ["mac", "models", "you", "principles", "model", "iphone", "pair", "away",
                                              "voice"] and not st["complete"]
    assert st["empty"]  # no recordings yet: the page offers to bring in an export
    assert not any(s["done"] for s in st["steps"] if s["id"] not in ("mac", "models"))  # those two are this Mac's
    assert client.post("/api/octo/settings/owner", json={"name": "Asha", "pronoun": "she"}).json()["profile"]["name"] == "Asha"
    assert client.post("/api/octo/settings/owner", json={"name": "Asha", "pronoun": "it"}).status_code == 422
    r = client.post("/api/octo/settings/rubric", json={"principles": "1. Listen before answering.", "rules": ""}).json()
    assert r["parsed"][0]["n"] == 1
    done = {s["id"]: s["done"] for s in client.get("/api/octo/settings/setup").json()["steps"]}
    assert {k: done[k] for k in ("you", "principles", "model", "voice")} == \
        {"you": True, "principles": True, "model": False, "voice": False}
    # the phone steps can be skipped (and taken back); the others can't
    st = client.post("/api/octo/settings/setup/skip", json={"id": "iphone"}).json()
    assert next(s for s in st["steps"] if s["id"] == "iphone")["skipped"]
    assert client.post("/api/octo/settings/setup/skip", json={"id": "voice"}).status_code == 400
    st = client.post("/api/octo/settings/setup/skip", json={"id": "iphone", "skip": False}).json()
    assert not next(s for s in st["steps"] if s["id"] == "iphone")["skipped"]
    assert client.get("/api/octo/progress").json()["first_lab"] is False  # a buyer's lab: Progress shows the beta's card


def test_setup_says_how_to_get_the_full_octo_app(client, monkeypatch):
    """install.sh, when the command line tools didn't arrive: "The Setup page will say how to finish" (install review, 5 Nov)."""
    from octo import doctor as DR
    monkeypatch.setattr(DR, "apple_tools", lambda: [DR.Check(DR.WARN, "Apple's command line tools", "not installed", "xcode-select --install")])
    monkeypatch.setattr(DR, "launcher", lambda: [DR.Check(DR.WARN, "The Octo app", "the simple launcher", "octo launcher install")])
    mac = next(s for s in client.get("/api/octo/settings/setup").json()["steps"] if s["id"] == "mac")
    assert {c["what"] for c in mac["why"]} >= {"Apple's command line tools", "The Octo app"}


def test_jays_lab_is_the_first_lab(tmp_path, client):
    conn = connect(tmp_path / "t.db")
    conn.execute("""INSERT OR REPLACE INTO app_config (key, value, updated_at) VALUES ('lab_origin', '"first"', 0)""")  # as migrated: it had reviews before 7 Oct (octo/labconfig.py)
    assert client.get("/api/octo/progress").json()["first_lab"] is True  # no tester's card in the owner's own lab


def test_profile_and_principles_are_mac_only(tmp_path):
    """No settings route is open to a phone (octo/phone_api.py PHONE_ROUTES; the gate refuses the rest)."""
    from octo import phone_api
    assert not [path for _, path in phone_api.PHONE_ROUTES if path.startswith("/api/octo/settings")]


def test_two_connections_opening_a_new_database_at_once_both_work(tmp_path):
    """8 Oct: the server opens several connections as it starts; two adding the same flag raised
    IntegrityError and killed the recorder's sync thread. Both must open cleanly."""
    import sqlite3
    import threading

    from octo import db as DB
    path = tmp_path / "race.db"
    first = DB.connect(path)
    first.execute("DELETE FROM app_config")  # flags not yet decided, like the first lab before 8 Oct
    first.execute("PRAGMA user_version = 0")  # and from before SCHEMA_VERSION
    errors, start = [], threading.Barrier(6)

    def open_it():
        try:
            start.wait()
            DB.connect(path).execute("SELECT 1")
        except sqlite3.Error as e:  # pragma: no cover - the failure being guarded against
            errors.append(e)

    threads = [threading.Thread(target=open_it) for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    assert {r["key"] for r in DB.connect(path).execute("SELECT key FROM app_config")} >= {"lab_origin"}


def test_the_settings_page_says_when_the_lab_was_last_exported(client):
    assert client.get("/api/octo/settings/export").json() == {"last_export_at": None, "since": 0}


def test_principles_as_people_paste_them_and_the_preview_agrees_with_the_save(new_lab_files):
    # Onboarding review, 28 Oct: bullets and "1)" were dropped without a word, a second line was lost, and a
    # "## " heading typed in the box ended the section, so the preview showed more than the review read.
    typed = "- Listen before answering.\n  Even when I'm sure.\n\n## At work\n\n• Keep promises small.\n1) Say no early."
    shown = RB.preview(typed)
    assert [(p["n"], p["text"]) for p in shown] == [
        (1, "Listen before answering. Even when I'm sure."), (2, "Keep promises small."), (3, "Say no early.")]  # 1 taken
    out = RB.save(typed, "## Look closely.")
    assert [(p["n"], p["text"]) for p in I.principles(RB.text())] == [(p["n"], p["text"]) for p in shown]
    assert "### At work" in out["principles"] and I.review_rules(RB.text()) == "### Look closely."



def test_once_every_gated_model_is_accepted_checking_starts_the_download(client, monkeypatch):
    started = []
    monkeypatch.setattr(MD.Fetcher, "start", lambda self: started.append(1) or True)
    answers = iter([{"name": "sam", "access": {"pyannote/a": "ok", "pyannote/b": "accept"}},
                    {"name": "sam", "access": {"pyannote/a": "ok", "pyannote/b": "ok"}}, {"name": None}])
    monkeypatch.setattr(MD, "account", lambda token="": next(answers))
    for _ in range(3):
        client.get("/api/octo/settings/huggingface")
    assert started == [1]  # only the check where both were accepted


def test_only_a_new_lab_is_offered_the_example_principles(client, new_lab_files, monkeypatch):
    # 31 Oct: two examples, used only when the owner taps Use this; the first lab never sees them.
    monkeypatch.setattr(LC, "origin", lambda conn: LC.FIRST)
    assert "examples" not in client.get("/api/octo/settings/rubric").json()
    monkeypatch.setattr(LC, "origin", lambda conn: LC.PRODUCT)
    got = client.get("/api/octo/settings/rubric").json()
    assert [e["text"] for e in got["examples"]] == ["Let others finish before I speak.", "Do what I said I would do."]
    assert got["principles"] == ""  # shown, never filled in


def test_an_app_put_on_the_iphone_by_hand_counts_once_it_is_paired(client, tmp_path, monkeypatch):
    from octo import iphone as IP
    from octo import phone as PH
    monkeypatch.setattr(PH, "DIR", tmp_path / "phone")
    monkeypatch.setattr(IP, "INSTALLED", tmp_path / "iphone.json")  # the lab never installed it: from Xcode by hand
    monkeypatch.setattr(PH, "tailscale_status", lambda: {"installed": False, "running": False, "ips": [], "phones": []})
    steps = lambda: {s["id"]: s for s in client.get("/api/octo/settings/setup").json()["steps"]}
    assert not steps()["iphone"]["done"]
    PH.add_device(connect(tmp_path / "t.db"), "iPhone")
    st = steps()
    assert st["iphone"]["done"] and st["pair"]["done"] and not st["away"]["done"]
