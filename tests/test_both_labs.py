"""A smoke set on the demo lab as both kinds of lab (architecture review 8, 28 Oct): the first, and a new buyer's. The rest of
the suite runs as the first lab (conftest's as_jays_lab); here each lab's own `lab_origin` decides its flags, so the product
that ships to strangers gets the same pass: every GET route answers, and the pipeline drains on a fake model."""

import json
import socket

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import claude_code as CC
from octo import labconfig as LC
from octo import pipeline as P
from octo import providers as PV
from octo import settings as ST
from octo import worker as W
from octo.api import make_router
from octo.db import connect, row, rows

KINDS = ("first", "buyer")
# The route has nothing to show for this lab (no such id, nothing recorded yet): fine. A 5xx never is.
NOTHING_HERE = {404, 409, 410}  # 410: the audio went at 60 days (here: never under HOME)


@pytest.fixture(scope="session")
def built(tmp_path_factory):
    """Each kind of demo lab built once (building is the slow part), into its own folder; the tests copy or read it."""
    from tests import demo_lab as DL
    out = {}
    for kind in KINDS:
        root = tmp_path_factory.mktemp(f"demo-{kind}")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(DL, "ROOT", root)
            mp.setattr(DL, "DEMO", root / "data" / "demo")
            mp.setattr(DL, "DB", root / "data" / "demo" / "octo.db")
            DL.build(new_lab=kind == "buyer")
        out[kind] = root / "data" / "demo" / "octo.db"
    return out


@pytest.fixture(params=KINDS)
def lab(request, built, tmp_path, monkeypatch):
    """A copy of the built lab, as itself: no test overrides, so `lab_origin` decides. Nothing reaches the network."""
    monkeypatch.setattr(LC, "TEST_OVERRIDES", {})

    def refuse(*a, **k):
        raise OSError("no network in tests")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    db = tmp_path / "octo.db"
    db.write_bytes(built[request.param].read_bytes())
    conn = connect(db)
    conn.execute("UPDATE recordings SET audio_deleted_at=1")  # the demo's audio isn't under HOME
    return request.param, db, conn


def _ids(conn) -> dict[str, str]:
    """A real id for each path parameter, where the demo has one."""
    def first(sql):
        r = conn.execute(sql).fetchone()
        return r[0] if r else "none"
    return {"session_id": first("SELECT id FROM sessions WHERE status='active' ORDER BY id"),
            "person_id": first("SELECT id FROM people WHERE is_self=0 ORDER BY id"),
            "note_id": first("SELECT id FROM coach_notes"),
            "ask_id": first("SELECT id FROM asks"),
            "drill_id": first("SELECT id FROM drills"),
            "rehearsal_id": first("SELECT id FROM rehearsals"),
            "stage": "insight"}


def test_every_get_route_answers_without_a_server_error(lab):
    kind, db, conn = lab
    router = make_router(lambda: connect(db), processor=type("P", (), {"kick": lambda self: None})())
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app, base_url="http://127.0.0.1:8765", headers={"Origin": "http://127.0.0.1:8765"},
                   raise_server_exceptions=False)
    ids = _ids(conn)
    routes = [r for r in router.routes if "GET" in getattr(r, "methods", ())]
    assert len(routes) > 40  # collected from the app, so a new route is covered without being listed here
    bad = {}
    for r in routes:
        path = r.path
        # a required query (search's q, commitments' person) gets an id of the demo's where one fits, else a word
        query = {q.name: ids.get(f"{q.name}_id", "Asha") for q in r.dependant.query_params if q.field_info.is_required()}
        got = c.get(path.format(**ids), params=query)
        if got.status_code >= 400 and got.status_code not in NOTHING_HERE:
            bad[path] = (got.status_code, got.text[:200])
    assert bad == {}, f"{kind}'s lab: {bad}"


def _fake_answer(schema: dict) -> dict:
    """Something of the answer's own shape: every list empty, a summary where the schema has one."""
    props = schema.get("properties", {})
    out = {k: [] if v.get("type") == "array" else "" if v.get("type") == "string" else None for k, v in props.items()}
    if "summary" in props:
        out["summary"] = "A made-up review."
    return out


@pytest.mark.parametrize("pick", ("key", "claude-code"))
def test_the_pipeline_drains_on_a_fake_model(lab, monkeypatch, pick):
    kind, db, conn = lab
    if kind == "first" and pick == "key":
        pytest.skip("The first lab chooses nothing: it runs as it always has")
    from octo import rubric as RB
    RB.OWN.write_text("# Rubric\n\n## Conduct principles\n\n1. Listen before answering.\n\n2. Keep your word.\n")  # the buyer's lab starts with none
    asked = []

    def fake(who):
        def answer(*a, **k):
            asked.append(who)
            return _fake_answer(k.get("schema") or next(x for x in a if isinstance(x, dict) and "properties" in x)), {}
        return answer
    monkeypatch.setattr(CC, "ask", fake("claude-code"))
    monkeypatch.setattr(CC, "claude_bin", lambda: "fake")
    monkeypatch.setattr(PV, "_anthropic", fake("anthropic"))
    monkeypatch.setattr(PV, "_openai", fake("openai"))
    if kind == "buyer":  # a buyer's lab reviews only once its owner chooses: an API key, or Claude Code (P5, 31 Oct)
        if pick == "key":
            c = PV.add("anthropic", api_key="sk-ant-test-0000")
            ST.choose(conn, "insight", f"{c['id']}:claude-opus-5-5")
        else:
            ST.choose(conn, "insight", "claude-opus-5-5")

    def execute(run_id):  # the worker, in-process, against this lab
        run = row(conn, "SELECT * FROM stage_runs WHERE id=?", run_id)
        conn.execute("UPDATE stage_runs SET status='running' WHERE id=?", (run_id,))
        out = P.STAGES[run["stage"]].fn(W.inputs_for(conn, run), json.loads(run["params"]))
        conn.execute("UPDATE stage_runs SET status='done', output=? WHERE id=?", (json.dumps(out), run_id))
        return 0, ""

    # One review out of date and asked for again (Redo the review), so one is made in either lab, answered or not.
    old = rows(conn, "SELECT id, target FROM stage_runs WHERE stage='insight' AND is_current=1 AND status='done' ORDER BY target")[0]
    sid = old["target"]
    conn.execute("UPDATE stage_runs SET stale=1 WHERE id=?", (old["id"],))
    conn.execute("UPDATE sessions SET redo_review=? WHERE id=?", (old["id"], sid))
    # B35: the demo's one discussion in parts, its first part answered: Redo the review makes one of the whole.
    head = row(conn, "SELECT first_id FROM parts")["first_id"]
    conn.execute("UPDATE sessions SET redo_review=? WHERE id=?", (P.current(conn, head, "insight")["id"], head))
    before = {r["id"] for r in rows(conn, "SELECT id FROM stage_runs")}
    counts = P.run_pending(conn, log=lambda m: None, execute=execute)
    assert counts["done"] > 0 and counts["failed"] == 0
    # The demo's step shown "running" was left so by no runner: counted as stopped, tried again after the wait (review 5 Nov).
    assert rows(conn, "SELECT stage, status FROM stage_runs WHERE status IN ('queued', 'running', 'failed') "
                      "AND coalesce(error, '') != ?", P.STOPPED) == []
    review = row(conn, "SELECT id, output FROM stage_runs WHERE target=? AND stage='insight' AND is_current=1", sid)
    assert review["id"] not in before and json.loads(review["output"])["summary"] == "A made-up review."
    whole = row(conn, "SELECT id, params, upstream FROM stage_runs WHERE target=? AND stage='insight' AND is_current=1", head)
    assert whole["id"] not in before and len(json.loads(whole["params"])["parts"]) == len(json.loads(whole["upstream"])) == 2
    # Whatever the owner chose, and only that: Claude Code is never used unless chosen (or the first lab, as always).
    assert set(asked) == ({"anthropic"} if kind == "buyer" and pick == "key" else {"claude-code"})
    if kind == "buyer" and pick == "claude-code":  # the buyer's own profile, never the first lab's way of addressing
        assert "owner" in json.loads(row(conn, "SELECT params FROM stage_runs WHERE id=?", review["id"])["params"])


def test_a_buyers_lab_offers_claude_code_but_never_chooses_it_and_asks_before_keeping_others_voices(lab):
    kind, db, conn = lab
    buyer = kind == "buyer"
    from octo import voice as V
    assert PV.claude_code_default(conn) is not buyer
    assert any(c.claude for c in ST.choices(conn, ST.BY_STAGE["insight"]))  # every lab may choose it (31 Oct)
    assert ST.reader(conn) is None if buyer else ST.reader(conn)["kind"] == "claude-code"
    assert (ST.selected(conn, ST.BY_STAGE["insight"]) is None) is buyer
    other = rows(conn, "SELECT id FROM people WHERE is_self=0 AND consent IS NULL")[0]["id"]
    me = rows(conn, "SELECT id FROM people WHERE is_self=1")[0]["id"]
    assert V.needs_consent(conn) is buyer
    assert V.may_keep_print(conn, other) is not buyer
    assert V.may_keep_print(conn, me)  # the owner's own voice, always
