"""`octo uninstall` (octo/uninstall.py): what it plans to remove and keep, on made-up folders. Nothing here removes
anything: the plan is only read, and running it goes through a stand-in remover."""

import plistlib
from pathlib import Path

import pytest

from octo import uninstall as U


@pytest.fixture
def mac(tmp_path):
    """A one-line install's layout: code in octo/app, the lab in octo/lab.noindex, its login items, its models."""
    home = tmp_path / "home"
    base = home / "Library" / "Application Support" / "octo"
    code, lab = base / "app", base / "lab.noindex"
    agents, hub = home / "Library" / "LaunchAgents", home / ".cache" / "huggingface" / "hub"
    for d in (code / ".venv", lab / "data", agents, hub / "models--Qwen--Qwen3-ASR-1.7B", hub / "models--someone--else",
              home / "Applications" / "Octo.app", home / "Library" / "Preferences"):
        d.mkdir(parents=True)
    (code / ".octo-home").write_text(f"{lab}\n")
    for f in ("relay.py", "relay.key", "lab.srl", "notes.txt"):
        (base / f).write_text("x")
    (home / "Library" / "Preferences" / f"{U.BUNDLE}.plist").write_text("x")
    (hub.parent / "token").write_text("x")
    for label in ("com.octo.lab", "com.octo.relay"):
        (agents / f"{label}.plist").write_bytes(plistlib.dumps({"Label": label, "WorkingDirectory": str(code)}))
    return dict(home=home, base=base, code=code, lab=lab, agents=agents, hub=hub)


def make(m, keep_data, code=None, lab=None, models=("Qwen/Qwen3-ASR-1.7B", "pyannote/segmentation-3.0")):
    removed, kept = U.plan(keep_data, code or m["code"], lab or m["lab"], app=m["home"] / "Applications" / "Octo.app",
                           agents=m["agents"], base=m["base"], library=m["home"] / "Library", hub=m["hub"],
                           models=list(models), user=m["home"])
    return [p for _, _, p in removed], [p for _, _, p in kept], removed


def test_the_lab_is_found_through_its_login_item(mac):
    assert U.installed(mac["agents"]) == (mac["code"].resolve(), mac["lab"].resolve())


def test_a_lab_home_named_in_the_login_item_wins(mac, tmp_path):
    (mac["agents"] / "com.octo.lab.plist").write_bytes(plistlib.dumps(
        {"WorkingDirectory": str(mac["code"]), "EnvironmentVariables": {"OCTO_HOME": str(tmp_path / "elsewhere")}}))
    assert U.installed(mac["agents"])[1] == (tmp_path / "elsewhere").resolve()


def test_without_a_login_item_it_is_this_copy(tmp_path):
    assert U.installed(tmp_path) == (U.ROOT, U.HOME)


def test_a_one_line_install_goes_entirely_but_the_conversations_stay(mac):
    removed, kept, _ = make(mac, keep_data=True)
    assert mac["lab"] in kept and mac["lab"] not in removed
    assert mac["code"] in removed and removed.index(mac["code"]) > removed.index(mac["base"] / "relay.py")
    for p in ("relay.py", "relay.key", "lab.srl"):
        assert mac["base"] / p in removed
    assert mac["base"] / "notes.txt" not in removed  # not Octo's: left, so the folder stays
    assert removed[-1] == mac["base"]  # only if it's empty by then
    assert [p.name for p in removed[1:3]] == ["com.octo.lab.plist", "com.octo.relay.plist"]
    assert mac["home"] / "Library" / "Preferences" / f"{U.BUNDLE}.plist" in removed


def test_the_conversations_go_only_when_asked(mac):
    removed, kept, _ = make(mac, keep_data=False)
    assert mac["lab"] in removed and mac["lab"] not in kept


def test_only_the_labs_own_models_and_never_the_cache(mac):
    removed, kept, _ = make(mac, keep_data=True)
    hub = mac["hub"]
    assert hub / "models--Qwen--Qwen3-ASR-1.7B" in removed
    assert hub / "models--someone--else" not in removed and hub not in removed
    assert hub / "models--pyannote--segmentation-3.0" not in removed  # listed, but not downloaded
    assert hub.parent / "token" in kept


def test_the_app_and_the_login_items_go_first(mac):
    _, _, removed = make(mac, keep_data=True)
    assert [k for k, _, _ in removed[:3]] == ["app", "agent", "agent"]


def test_a_clone_is_never_removed_and_only_the_labs_folders_go(tmp_path, mac):
    clone = tmp_path / "Developer" / "octo"
    for d in ("data", "audio/lab", "audio/bench", "notes", ".git", "octo"):
        (clone / d).mkdir(parents=True)
    (clone / "data" / "octo.db").write_text("x")
    removed, kept, raw = make(mac, keep_data=False, code=clone, lab=clone)
    assert clone in kept and clone not in removed
    assert {clone / "data", clone / "audio" / "lab", clone / "notes"} <= set(removed)
    assert clone / "audio" / "bench" not in removed and clone / ".git" not in removed
    assert ("empty", clone / "audio") in [(k, p) for k, _, p in raw]  # audio/ itself only if nothing else is in it

    removed, kept, _ = make(mac, keep_data=True, code=clone, lab=clone)
    assert not any(p.is_relative_to(clone) for p in removed)


def test_conversations_kept_inside_the_installed_code_keep_the_code(mac):
    lab = mac["code"] / "lab"
    removed, kept, _ = make(mac, keep_data=True, lab=lab)
    assert mac["code"] not in removed and mac["code"] in kept


def test_running_the_plan_reports_each_step_and_carries_on_after_a_failure(mac):
    _, _, removed = make(mac, keep_data=True)
    calls, said = [], []

    def rm(kind, path, from_app):
        calls.append((kind, path, from_app))
        if path.name == "relay.key":
            raise PermissionError("no")
        return True

    assert U.run(removed, [], from_app=True, say=said.append, rm=rm) == 1
    assert [p for _, p, _ in calls] == [p for _, _, p in removed] and all(f for _, _, f in calls)
    assert any(s.startswith("couldn't remove the recorder relay's key") for s in said)
    assert sum(s.startswith("removed ") for s in said) == len(removed) - 1
    assert (mac["base"] / "relay.key").exists()  # the stand-in removed nothing


def test_the_question_keeps_them_unless_delete_is_typed(mac, monkeypatch):
    seen = []
    monkeypatch.setattr(U, "installed", lambda: (mac["code"], mac["lab"]))
    monkeypatch.setattr(U.LN, "found", lambda: None)
    monkeypatch.setattr(U, "repos", lambda home: [])
    monkeypatch.setattr(U, "run", lambda remove, kept, **kw: seen.append([p for _, _, p in remove]) or 0)
    from octo import models as MD
    monkeypatch.setattr(MD, "cache_dir", lambda: mac["hub"])
    real = U.plan
    monkeypatch.setattr(U, "plan", lambda *a, **kw: real(*a, agents=mac["agents"], base=mac["base"],
                                                         library=mac["home"] / "Library", user=mac["home"], **kw))
    for answer, gone in (("", False), ("no", False), ("Delete ", True)):
        seen.clear()
        assert U.main(None, ask=lambda _: answer, say=lambda _: None) == 0
        assert (mac["lab"] in seen[0]) is gone
    assert U.main(None, ask=lambda _: (_ for _ in ()).throw(EOFError), say=lambda _: None) == 1  # no terminal: nothing



def test_a_home_elsewhere_without_a_lab_in_it_is_never_emptied(mac, tmp_path):
    own = tmp_path / "someone"
    (own / "notes").mkdir(parents=True)
    removed, kept, _ = make(mac, keep_data=False, lab=own)
    assert own in kept and not any(p.is_relative_to(own) for p in removed)


def test_set_aside_keeps_the_conversations_under_another_name_and_says_when_they_were_exported(mac, monkeypatch):
    """`--set-aside` (docs/export-plan.md): a new install starts with an empty lab, and nothing is deleted."""
    import sqlite3

    from octo import db as DB
    from octo import export as EX
    from octo import models as MD
    DB.connect(mac["lab"] / "data" / "octo.db").close()
    monkeypatch.setattr(U, "installed", lambda: (mac["code"], mac["lab"]))
    monkeypatch.setattr(U.LN, "found", lambda: None)
    monkeypatch.setattr(U, "repos", lambda home: [])
    monkeypatch.setattr(MD, "cache_dir", lambda: mac["hub"])
    seen = []
    monkeypatch.setattr(U, "run", lambda remove, kept, **kw: seen.append([p for _, _, p in remove]) or 0)
    said = []
    assert U.main(None, dry_run=True, say=said.append) == 0
    assert "never been exported" in said[-1]
    c = sqlite3.connect(mac["lab"] / "data" / "octo.db")
    c.execute("INSERT INTO app_config (key, value, updated_at) VALUES ('last_export_at', '1760000000', 0)")
    c.commit()
    c.close()
    assert U.main(None, set_aside=True, say=said.append) == 0
    assert mac["lab"] not in seen[0] and not mac["lab"].exists()  # kept, not removed
    [aside] = EX.asides(mac["base"])
    assert (aside / "data" / "octo.db").exists() and "Last export:" in U.last_export(aside)


def test_a_versioned_install_goes_whole(mac):
    """Since 5 Nov the one-line install keeps versions/<v>, `current` and `previous` (octo/deploy.py `switch`)."""
    base = mac["base"]
    v1, v2 = base / "versions" / "2026.11.05-1+a", base / "versions" / "2026.11.05-2+b"
    v1.mkdir(parents=True), v2.mkdir()
    (base / "current").symlink_to("versions/2026.11.05-2+b")
    (base / "previous").symlink_to("versions/2026.11.05-1+a")
    removed, kept, _ = make(mac, keep_data=True, code=(base / "current").resolve())
    assert {base / "current", base / "previous", base / "versions", mac["code"]} <= set(removed)
    assert mac["lab"] in kept
