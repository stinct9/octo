"""One row per model call (architecture review 5 Nov, risk 7): claude_code.ask and providers.ask write model_calls, which
every call goes through, and the day's count is one query on it. Before, it was summed from nine tables and seven delete
triggers, and a call billed but failed wasn't counted at all."""

import ast
import json
import subprocess
import time
from pathlib import Path

import pytest

from octo import claude_code as CC
from octo import db as DB
from octo import pipeline as P
from octo import providers as PV
from octo import worker as W
from octo.db import calling, connect, rows
from octo.stages import Stage

CLAUDE = {"kind": "claude-code", "model": "claude-opus-5-5"}


def _rows(conn):
    return rows(conn, "SELECT reader, purpose, ref, ok FROM model_calls ORDER BY rowid")


def test_claude_code_writes_a_row_for_each_call_made_or_billed(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    out = {"stdout": json.dumps({"structured_output": {"ok": True}, "total_cost_usd": 0.1})}
    monkeypatch.setattr(CC, "claude_bin", lambda: "claude")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out["stdout"], ""))
    with calling(conn, "coach", "note_1"):
        assert CC.ask("claude-opus-5-5", "high", "s", "p", {})[0] == {"ok": True}
        out["stdout"] = json.dumps({"is_error": True, "result": "You've hit your usage limit · resets 11pm", "total_cost_usd": 0})
        with pytest.raises(RuntimeError, match="usage limit"):  # refused before any work: no row
            CC.ask("claude-opus-5-5", "high", "s", "p", {})
        out["stdout"] = json.dumps({"is_error": True, "result": "something broke", "total_cost_usd": 0.4})
        with pytest.raises(RuntimeError, match="something broke"):  # it worked on it first: billed, counted
            CC.ask("claude-opus-5-5", "high", "s", "p", {})

        def slow(*a, **k):
            raise subprocess.TimeoutExpired("claude", 1)
        monkeypatch.setattr(subprocess, "run", slow)
        with pytest.raises(subprocess.TimeoutExpired):
            CC.ask("claude-opus-5-5", "high", "s", "p", {})
    assert _rows(conn) == [{"reader": json.dumps(CLAUDE), "purpose": "coach", "ref": "note_1", "ok": 1},
                           {"reader": json.dumps(CLAUDE), "purpose": "coach", "ref": "note_1", "ok": 0},
                           {"reader": json.dumps(CLAUDE), "purpose": "coach", "ref": "note_1", "ok": 0}]
    assert P.claude_calls_since(conn, P._midnight(time.time())) == 3
    monkeypatch.setattr(CC, "claude_bin", lambda: None)
    with calling(conn, "coach"), pytest.raises(RuntimeError, match="isn't installed"):
        CC.ask("claude-opus-5-5", "high", "s", "p", {})
    assert len(_rows(conn)) == 3


def test_a_key_writes_a_row_never_the_key_and_a_refusal_writes_none(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    monkeypatch.setattr(PV, "connection", lambda c, cfg=None: {"id": c, "api_key": "sk-secret"})
    reader = {"kind": "anthropic", "connection": "mine", "model": "claude-sonnet-5-5", "effort": "high"}
    answers = [({"ok": True}, {}), PV.Billed("The answer was cut off"), PV.ProviderError("Anthropic didn't accept the API key"),
               PV.ProviderError(f"Couldn't reach Anthropic: {PV.TRANSIENT}")]

    def reply(*a):
        a = answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a
    monkeypatch.setattr(PV, "_anthropic", reply)
    with calling(conn, "ask", "ask_1"):
        PV.ask(reader, "s", "p", {})
        for _ in range(3):
            with pytest.raises(PV.ProviderError):
                PV.ask(reader, "s", "p", {})
    got = _rows(conn)
    assert [r["ok"] for r in got] == [1, 0] and "sk-secret" not in json.dumps(got)
    assert json.loads(got[0]["reader"]) == {"kind": "anthropic", "model": "claude-sonnet-5-5", "connection": "mine"}
    assert P._calls_since(conn, 0, connection="mine") == 2 and P.claude_calls_since(conn, 0) == 0


def test_a_call_made_in_a_step_is_counted_by_the_worker(tmp_path, monkeypatch):
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO recordings (id, path, source, duration_s, created_at) VALUES ('r1', 'a.wav', 'phone', 1, 0)")
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, params, queued_at) "
                 "VALUES ('run_1', 'asks', 'r1', 'h', 'queued', '{}', 0)")
    monkeypatch.setattr(CC, "_run", lambda *a: ({"ok": True}, {}))
    monkeypatch.setattr(W, "STAGES", {"asks": Stage("asks", 1, "recording", None, {},
                                                    lambda i, p: CC.ask("claude-opus-5-5", "high", "s", "p", {})[0])})
    monkeypatch.setattr(W, "connect", lambda: connect(tmp_path / "t.db"))
    assert W.main("run_1") == 0
    assert _rows(conn) == [{"reader": json.dumps(CLAUDE), "purpose": "asks", "ref": "run_1", "ok": 1}]


def test_a_call_outside_calling_goes_to_the_labs_own_database(tmp_path, monkeypatch):
    lab = tmp_path / "octo.db"
    connect(lab).close()
    monkeypatch.setattr(DB, "CALLS_DB", lab)
    monkeypatch.setattr(CC, "_run", lambda *a: ({"ok": True}, {}))
    CC.ask("claude-opus-5-5", "low", "s", "p", {})  # Settings' test of a connection, say
    assert len(_rows(connect(lab))) == 1


# Modules that call a model run as a step (the worker sets `calling`), or set it themselves around the call.
IN_A_STEP = {"stages.py", "review_any.py", "commitments.py", "hindi.py"}
NOT_A_MODEL = {"export.py", "uninstall.py"}  # their `ask` is input() in Terminal


def test_every_call_path_says_where_its_call_is_counted():
    """A call made outside `db.calling` (and outside a step) would be counted in the lab's own database only by luck: in a
    test, nowhere. Every module that calls a model sets it around the call."""
    root = Path(__file__).resolve().parent.parent / "octo"
    callers = set()
    for f in [*root.glob("*.py"), *root.glob("routes/*.py")]:
        text = f.read_text()
        if f.name in ("claude_code.py", "providers.py") or not any(w in text for w in ("claude_code", "providers", "_ask")):
            continue
        for n in ast.walk(ast.parse(text)):
            if isinstance(n, ast.Call) and (getattr(n.func, "attr", None) or getattr(n.func, "id", None)) in ("ask", "_ask", "cc_ask"):
                callers.add(f)
    assert {f.name for f in callers} >= {"coach.py", "ask.py", "drills.py", "rehearse.py", "talk.py", "stages.py"}
    for f in callers:
        assert f.name in IN_A_STEP | NOT_A_MODEL or "calling(" in f.read_text(), f.name


def test_todays_calls_carry_over_from_before_model_calls_and_the_triggers_go(tmp_path):
    conn = connect(tmp_path / "t.db")
    now = time.time()
    conn.execute("CREATE TABLE calls_gone (at REAL NOT NULL, n INTEGER NOT NULL, reader TEXT, stage TEXT, params TEXT)")
    conn.execute("CREATE TRIGGER coach_notes_calls_gone BEFORE DELETE ON coach_notes BEGIN SELECT 1; END")
    conn.execute("INSERT INTO calls_gone (at, n, reader) VALUES (?, 2, ?)", (now, json.dumps(CLAUDE)))
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, params, output, queued_at, finished_at) "
                 "VALUES ('i', 'insight', 'x', 'h', 'done', ?, ?, 0, ?)",
                 (json.dumps({"model": "claude-opus-5-5"}), json.dumps({"usage": {"duration_ms": 1}}), now))
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, params, output, queued_at, finished_at) "
                 "VALUES ('t', 'transcribe', 'x', 'h', 'done', ?, ?, 0, ?)",
                 (json.dumps({"hindi": {}}), json.dumps({"merged": {"calls": 3}}), now))
    conn.execute("INSERT INTO coach_notes (id, created_at, status, model, usage, finished_at) VALUES ('c', 0, 'done', 'm', '{}', ?)", (now,))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("INSERT INTO talks (session_id, created_at, updated_at, turns) VALUES ('s', 0, 0, ?)",
                 (json.dumps([{"by": "me", "at": now}, {"by": "them", "at": now, "reader": CLAUDE}]),))
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, params, output, queued_at, finished_at) "
                 "VALUES ('o', 'insight', 'x', 'h', 'done', ?, ?, 0, ?)",
                 (json.dumps({"model": "claude-opus-5-5"}), json.dumps({"usage": {"duration_ms": 1}}), now - 3 * 86400))
    conn.execute("PRAGMA user_version = 21")
    conn.close()
    conn = connect(tmp_path / "t.db")
    assert P.claude_calls_since(conn, now - 86400) == 2 + 1 + 3 + 1 + 1  # gone, review, merge, coach, talk; not 3 days ago
    assert not rows(conn, "SELECT 1 FROM sqlite_master WHERE name LIKE '%calls_gone%'")
