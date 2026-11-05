"""Fake models whose calls are counted as real ones are (model_calls, architecture review 5 Nov, risk 7): the fake stands in
for `claude -p` itself (claude_code._run) or for a service's reply (providers._anthropic), so the call still goes through
claude_code.ask or providers.ask, which write the row."""

from unittest.mock import patch

from octo import claude_code as CC
from octo import providers as PV


def through_claude_code(fake):
    """`fake(model, effort, system, prompt, schema)`, called through claude_code.ask."""
    def ask(model, effort, system, prompt, schema, timeout=CC.TIMEOUT_S):
        with patch.object(CC, "_run", lambda *a: fake(model, effort, system, prompt, schema)):
            return CC.ask(model, effort, system, prompt, schema, timeout)
    return ask


def through_reader(fake):
    """`fake(who, effort, system, prompt, schema)` (the features' own `_ask`), called through providers.ask: Claude Code's
    path for a claude-code reader, a key's for any other."""
    def ask(who, effort, system, prompt, schema):
        run = lambda *a, **k: fake(who, effort, system, prompt, schema)  # noqa: E731
        with patch.object(CC, "_run", run), patch.object(PV, "_anthropic", run), patch.object(PV, "_openai", run), \
                patch.object(PV, "connection", lambda c, cfg=None: {"id": c}):
            return PV.ask({**who, "effort": effort, "model": who.get("model") or "claude-opus-5-5"}, system, prompt, schema)
    return ask


def spend(conn, n: int, reader: dict | None = None, at: float | None = None) -> None:
    """`n` calls made already today (to Claude Code unless `reader` says otherwise)."""
    import json
    import time
    conn.executemany("INSERT INTO model_calls (at, reader, purpose, ok) VALUES (?, ?, 'test', 1)",
                     [(at or time.time(), json.dumps(reader or {"kind": "claude-code"}))] * n)
