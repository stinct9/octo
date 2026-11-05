"""Calling Claude through Claude Code (`claude -p`) on the owner's own Claude plan (Pro or Max): the review when the
owner chooses it (octo/review_any.py through octo/providers.py), and every step that reads text through the review's
model (the Hindi merge, the coach, Ask, …). Any lab may choose it since 31 Oct (product plan P5).

Until 19 Oct the first lab's review kept its own copy of these three functions in octo/claude_review.py,
frozen byte for byte while the gate counted (K5; review 4 Oct, M30), so every other Claude step called
from here. The gate was retired that day and the review moved here too.

What leaves the Mac: the prompt (transcript or draft text), nothing else. The call runs with tools
off, no MCP, no settings, no saved session, in an empty folder, with a parent Claude Code's own
variables removed from the environment.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

TIMEOUT_S = 1200
MIN_VERSION = (2, 1, 280)  # the first with Claude Opus 5.5
INSTALL = "curl -fsSL https://claude.ai/install.sh | bash"


def claude_bin() -> str | None:
    """The `claude` command. A login item doesn't get the shell's PATH, so look where Homebrew and
    the installer put it too."""
    found = shutil.which("claude")
    if found:
        return found
    for p in ("/opt/homebrew/bin/claude", "/usr/local/bin/claude", str(Path.home() / ".local/bin/claude"),
              str(Path.home() / ".claude/local/claude")):
        if os.access(p, os.X_OK):
            return p
    return None


def status() -> dict:
    """What Settings shows for Claude Code on this Mac: found, its version, logged in and how, each with the
    command that fixes it ("fix", None when it's ready). Reads nothing of the account but its kind of login and plan."""
    exe = claude_bin()
    out = {"path": exe, "version": None, "version_ok": False, "logged_in": False, "auth": None, "plan": None, "fix": None}
    if not exe:
        return {**out, "fix": f"Install Claude Code: {INSTALL}"}
    need = ".".join(map(str, MIN_VERSION))
    try:
        v = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15, env=clean_env()).stdout
        out["version"] = v.split()[0] if v.split() else None
        out["version_ok"] = tuple(int(x) for x in out["version"].split(".")[:3]) >= MIN_VERSION
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError):
        pass
    if not out["version_ok"]:
        return {**out, "fix": f"Update Claude Code to {need} or later: claude update"}
    try:
        a = json.loads(subprocess.run([exe, "auth", "status", "--json"], capture_output=True, text=True, timeout=15,
                                      env=clean_env()).stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        a = {}
    out.update(logged_in=bool(a.get("loggedIn")), auth=a.get("authMethod"), plan=a.get("subscriptionType"))
    if not out["logged_in"]:
        out["fix"] = "Log in with your Claude account: claude auth login"
    return out


def clean_env() -> dict:
    """The environment without a parent Claude Code's own variables (2 Oct: "OAuth session expired").
    DISABLE_TELEMETRY and DISABLE_ERROR_REPORTING (octo/__init__.py) pass through."""
    return {k: v for k, v in os.environ.items()
            if not (k == "CLAUDECODE" or k == "ANTHROPIC_BASE_URL" or k.startswith("CLAUDE_AGENT_SDK")
                    or (k.startswith("CLAUDE_CODE_") and k != "CLAUDE_CODE_OAUTH_TOKEN"))}


def ask(model: str, effort: str, system: str, prompt: str, schema: dict, timeout: int = TIMEOUT_S) -> tuple[dict, dict]:
    """(the structured answer, usage). Raises with Claude Code's own message on any failure. Every call made, or failed
    after it may have used the plan (a timeout, an error with a cost), is a row in model_calls (db.model_call)."""
    from .db import model_call
    reader = {"kind": "claude-code", "model": model}
    try:
        answer, usage = _run(model, effort, system, prompt, schema, timeout)
    except subprocess.TimeoutExpired:
        model_call(reader, False)
        raise
    except Billed as e:
        model_call(reader, False)
        raise RuntimeError(str(e)) from None
    model_call(reader, True)
    return answer, usage


class Billed(RuntimeError):
    """A failure after Claude worked on the request: counted in the day's calls."""


def _run(model: str, effort: str, system: str, prompt: str, schema: dict, timeout: int) -> tuple[dict, dict]:
    exe = claude_bin()
    if not exe:
        raise RuntimeError("Claude Code isn't installed (the `claude` command wasn't found)")
    args = [exe, "-p", "--model", model, "--effort", effort, "--output-format", "json",
            "--json-schema", json.dumps(schema), "--system-prompt", system,
            "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--no-session-persistence"]
    with tempfile.TemporaryDirectory(prefix="octo-claude-") as empty:  # no CLAUDE.md or project to find
        r = subprocess.run(args, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=empty,
                           env=clean_env())
    try:
        d = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise RuntimeError(f"Claude Code failed (exit {r.returncode}): {(r.stderr or r.stdout).strip()[:500]}")
    if d.get("is_error"):
        msg = str(d.get("result") or d.get("subtype") or "unknown error")
        if "authenticate" in msg.lower() or "login" in msg.lower():
            msg += " -- sign in on this Mac: claude auth login"  # pipeline.SIGNED_OUT_WORDS
        raise (Billed if d.get("total_cost_usd") else RuntimeError)(f"Claude Code: {msg}")
    answer = d.get("structured_output")
    if answer is None:
        try:
            answer = json.loads(d.get("result") or "{}")
        except json.JSONDecodeError:
            raise Billed("Claude Code: the answer wasn't the JSON asked for")
    usage = {"cost_usd_equivalent": d.get("total_cost_usd"), "duration_ms": d.get("duration_ms"),
             "input_tokens": (d.get("usage") or {}).get("input_tokens"),
             "output_tokens": (d.get("usage") or {}).get("output_tokens")}
    return answer, usage
