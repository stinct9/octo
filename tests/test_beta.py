"""The beta's results file (octo/beta.py, product plan 4.8): counts only. Nothing in it may name a person,
quote a line or identify a conversation."""

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import beta as B
from octo.api import make_router
from octo.db import connect
from tests.test_coach import DAY, T0, _lab, _three, mark


def test_counts_only_and_nothing_that_names_anyone(tmp_path):
    conn = _lab(tmp_path)
    sids = _three(conn)
    mark(conn, sids[0], "one_thing", novelty="new")
    out = B.results(conn, now=T0 + 30 * DAY)
    assert out["octo_beta_results"] == 1 and out["conversations"] == 5  # _three's three, one earlier, one short
    assert out["days_since_first_conversation"] >= 30 and out["weeks_with_a_conversation"] >= 1
    assert out["reviewed_with_something_new"] <= out["reviewed"] and out["review_model"]["kind"] == "claude-code"
    text = json.dumps(out)
    for secret in ("Sam", "Kiran", "Tara", "ses_", "insight_", *sids):
        assert secret not in text, secret
    def strings(v):
        if isinstance(v, str):
            yield v
        elif isinstance(v, dict):
            for k, x in v.items():
                yield k
                yield from strings(x)
        elif isinstance(v, list):
            for x in v:
                yield from strings(x)
    allowed = {"claude-code", "anthropic", "openai", "local", "claude-opus-5-5"}
    for s in strings(out):  # keys, dates, the version and the model's kind: nothing free-form
        assert s in allowed or s.replace("_", "").replace("-", "").isalnum() or s[:4].isdigit(), s


def test_the_page_can_show_it(tmp_path):
    _lab(tmp_path)
    app = FastAPI()
    app.include_router(make_router(lambda: connect(tmp_path / "t.db"), processor=type("P", (), {"kick": lambda self: None})()))
    got = TestClient(app).get("/api/octo/beta").json()
    assert got["octo_beta_results"] == 1 and "reviewed" in got
