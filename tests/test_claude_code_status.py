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


def test_found_through_the_login_shell(tmp_path, monkeypatch):
    """Review 10 Nov: under nvm or volta `claude` is only on the shell's PATH; the shell is asked once, and the child
    gets its folder on PATH (for the node beside it). The shell call is faked."""
    exe = tmp_path / "nvm" / "bin" / "claude"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    calls = []

    def run(args, **kw):
        calls.append(args)
        return CC.subprocess.CompletedProcess(args, 0, stdout=f"Now using node v22\n{exe}\n", stderr="")

    monkeypatch.setattr(CC, "_shell", {"path": None, "asked": 0.0})
    monkeypatch.setattr(CC.subprocess, "run", run)
    assert "nvm" not in CC.clean_env().get("PATH", "")
    assert CC._from_login_shell() == str(exe) and CC._from_login_shell() == str(exe)
    assert len(calls) == 1 and calls[0][1:] == ["-ilc", "command -v claude"]
    assert CC.clean_env()["PATH"].startswith(str(exe.parent) + ":")


def test_not_found_through_the_login_shell_is_asked_again_later(monkeypatch):
    calls = []
    monkeypatch.setattr(CC, "_shell", {"path": None, "asked": 0.0})
    monkeypatch.setattr(CC.subprocess, "run", lambda args, **kw: calls.append(args)
                        or CC.subprocess.CompletedProcess(args, 1, stdout="", stderr=""))
    assert CC._from_login_shell() is None and CC._from_login_shell() is None and len(calls) == 1
    CC._shell["asked"] -= CC.SHELL_RETRY_S + 1
    assert CC._from_login_shell() is None and len(calls) == 2
