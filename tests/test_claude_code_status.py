"""What Settings and `octo doctor` show for Claude Code on this Mac (31 Oct): found, version, logged in, and the
command that fixes each. A fake `claude` script stands in; nothing reaches Claude."""

import json

from octo import claude_code as CC


def fake(tmp_path, monkeypatch, version: str, auth: dict):
    exe = tmp_path / "claude"
    exe.write_text(f"#!/bin/sh\nif [ \"$1\" = --version ]; then echo '{version} (Claude Code)'; "
                   f"else echo '{json.dumps(auth)}'; fi\n")
    exe.chmod(0o755)
    monkeypatch.setattr(CC, "claude_bin", lambda: str(exe))


def test_not_installed():
    s = CC.status()  # conftest: `claude` is never found
    assert s["path"] is None and s["fix"] == f"Install Claude Code: {CC.INSTALL}"


def test_too_old_for_opus_5_5(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "2.1.279", {"loggedIn": True})
    s = CC.status()
    assert s["version"] == "2.1.279" and not s["version_ok"] and "claude update" in s["fix"]


def test_not_logged_in(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "2.1.287", {"loggedIn": False})
    s = CC.status()
    assert s["version_ok"] and not s["logged_in"] and s["fix"].endswith("claude auth login")


def test_ready_on_a_plan(tmp_path, monkeypatch):
    fake(tmp_path, monkeypatch, "2.2.0", {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max",
                                          "email": "someone@example.com"})
    s = CC.status()
    assert s["fix"] is None and s["plan"] == "max" and s["auth"] == "claude.ai"
    assert "someone@example.com" not in json.dumps(s)  # the account's address never reaches the page
