"""The pages about Octo itself: /about built from docs/landing, /case built or sent to its published copy."""

import json
import os
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import bench.api as api_mod
from bench.runner import Runner
from bench.store import RunStore
from octo import pages as P


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(P.make_router())
    with TestClient(app) as c:
        yield c


def test_about_is_the_landing_page_built_now(client):
    if not P.LANDING_BUILD.exists():
        pytest.skip("no docs/landing (a public copy): see the next test")
    r = client.get("/about")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    page = r.text
    assert page.startswith("<!doctype html>") and '<meta charset="utf-8">' in page
    assert "<title>Octo Conversation Lab</title>" in page
    # three principles shown, their titles straight from the rubric; never the full text, its sources or
    # its author (9 Oct: the page is public-facing)
    assert page.count('<ol class="featured">') == 1 and page.count("<q>") >= 3
    for word in ("Sam", "principle-1"):
        assert word not in page, word
    # the words that must never be public (public-copy.json, which the public copy itself doesn't carry)
    config = Path(__file__).resolve().parents[1] / "public-copy.json"
    for word in json.loads(config.read_text())["block"] if config.exists() else []:
        assert not re.search(rf"(?<!\w){re.escape(word)}(?!\w)", page, re.I), word
    assert "<!--@" not in page and "/*@" not in page


def test_case_goes_to_the_published_page_when_it_cant_be_built(client, tmp_path, monkeypatch):
    monkeypatch.setattr(P, "CASE_PAGE", tmp_path / "octo-case.html")
    monkeypatch.setattr(P, "can_build_case", lambda: False)
    r = client.get("/case", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == P.CASE_PUBLISHED


def test_case_serves_a_current_build(client, tmp_path, monkeypatch):
    source, page = tmp_path / "case.py", tmp_path / "octo-case.html"
    source.write_text("# sources")
    page.write_text("<title>Octo Case</title><p>the case</p>")
    os.utime(source, (1_000, 1_000))   # the sources are older than the build
    monkeypatch.setattr(P, "CASE_SOURCES", [source])
    monkeypatch.setattr(P, "CASE_PAGE", page)
    monkeypatch.setattr(P, "build_case", lambda: pytest.fail("a current page isn't rebuilt"))
    r = client.get("/case")
    assert r.status_code == 200
    assert r.text.startswith("<!doctype html>") and "<p>the case</p>" in r.text


def test_case_is_rebuilt_when_its_sources_changed(client, tmp_path, monkeypatch):
    source, page = tmp_path / "case.py", tmp_path / "octo-case.html"
    page.write_text("<p>old</p>")
    os.utime(page, (1_000, 1_000))
    source.write_text("# changed since")   # newer than the page
    monkeypatch.setattr(P, "CASE_SOURCES", [source])
    monkeypatch.setattr(P, "CASE_PAGE", page)
    monkeypatch.setattr(P, "can_build_case", lambda: True)
    monkeypatch.setattr(P, "build_case", lambda: page.write_text("<p>new</p>"))
    r = client.get("/case")
    assert r.status_code == 200 and "<p>new</p>" in r.text


def test_the_ui_does_not_hide_them(tmp_path, monkeypatch):
    """The built UI is mounted at "/"; the pages must be registered before it."""
    ui = tmp_path / "dist"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><title>the lab</title>")
    monkeypatch.setattr(api_mod, "UI_DIST", ui)
    monkeypatch.setattr(P, "CASE_PAGE", tmp_path / "missing.html")
    monkeypatch.setattr(P, "can_build_case", lambda: False)
    app = api_mod.create_app(Runner(store=RunStore(tmp_path)), steps={}, run_all=[], poll_device=False)
    with TestClient(app) as c:
        about = c.get("/about", follow_redirects=False)
        assert "<title>Octo Conversation Lab</title>" in about.text if P.LANDING_BUILD.exists() else about.status_code == 307
        assert c.get("/case", follow_redirects=False).status_code == 307
        assert "the lab" in c.get("/").text


def test_no_other_site_can_frame_the_lab_and_its_pages_run_only_its_own_scripts(tmp_path):
    """Security review L3 (28 Oct). The landing page keeps its inline code, so only the framing rules there."""
    app = api_mod.create_app(Runner(store=RunStore(tmp_path)), steps={}, run_all=[], poll_device=False)
    with TestClient(app) as c:
        api, about = c.get("/api/steps"), c.get("/about", follow_redirects=False)
    for r in (api, about):
        assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert "default-src 'self'" in api.headers["Content-Security-Policy"]
    assert "default-src" not in about.headers["Content-Security-Policy"]
