"""`octo deploy` (octo/deploy.py) against throwaway repos: an origin, a live copy, and a lab that's never
really restarted."""

import hashlib
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from octo import ROOT
from octo import deploy as DP

UPDATE_READY = DP.update_ready  # the real one: conftest's no_real_fetch replaces it for every other test


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def commit(repo, files: dict[str, str], msg="change"):
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """origin (bare), dev (pushes to it), live (the lab's copy, named by the plist)."""
    origin, dev, live, agents = (tmp_path / n for n in ("origin.git", "dev", "live", "agents"))
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(dev)], check=True, capture_output=True)
    git(dev, "switch", "-q", "-c", "main")
    commit(dev, {"octo/a.py": "1\n"}, "first")
    git(dev, "push", "-q", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(live)], check=True)
    agents.mkdir()
    (agents / f"{DP.LAB}.plist").write_bytes(plistlib.dumps({"WorkingDirectory": str(live),
                                                            "StandardErrorPath": "/x/lab.log"}))
    done = {"restarts": [], "runs": []}
    monkeypatch.setattr(DP, "steps_running", lambda: 0)
    monkeypatch.setattr(DP, "lab_pid", lambda: 123)
    monkeypatch.setattr(DP, "wait_up", lambda pid: True)
    monkeypatch.setattr(DP, "restart", lambda repo, login_items: done["restarts"].append(login_items))
    real_run = DP._run

    def run(cmd, cwd):
        if str(cmd[0]) == "git":
            return real_run(cmd, cwd)
        done["runs"].append([Path(str(c)).name if i == 0 else str(c) for i, c in enumerate(cmd)])
        return ""
    monkeypatch.setattr(DP, "_run", run)

    def push(files, msg="change"):
        commit(dev, files, msg)
        git(dev, "push", "-q", "origin", "main")
    return {"live": live, "agents": agents, "push": push, "done": done}


def deploy(lab, **kw):
    said = []
    code = DP.deploy(say=said.append, agents=lab["agents"], **kw)
    return code, said


def test_plan_reads_the_changed_files():
    no = {"packages": False, "npm_ci": False, "ui": False, "login_items": False, "mac_app": False, "phone": False}
    assert DP.plan(["octo/x.py"]) == no
    assert DP.plan(["requirements.lock", "ui/src/App.vue"]) == {**no, "packages": True, "ui": True}
    assert DP.plan(["ui/package-lock.json", "octo/relay.py"]) == {**no, "npm_ci": True, "ui": True, "login_items": True}
    assert DP.plan(["macapp/Octo.swift"]) == {**no, "mac_app": True}
    assert DP.plan(["mobile/src/App.vue"]) == {**no, "phone": True}


def test_an_iphone_app_change_is_said_not_built(lab):
    lab["push"]({"mobile/src/App.vue": "<y/>"}, "phone")
    code, said = deploy(lab)
    assert code == 0 and lab["done"]["runs"] == [] and any("octo phone install" in s for s in said)


def test_update_ready_counts_what_origin_has_from_an_hourly_fetch(lab, monkeypatch):
    monkeypatch.setattr(DP, "_UPDATE", {"at": 0.0, "behind": 0})
    monkeypatch.setattr(DP.threading, "Thread", lambda target, args, **kw: type("T", (), {"start": lambda s: target(*args)})())
    clock = [10_000.0]
    assert UPDATE_READY(lab["live"], now=lambda: clock[0]) == 0
    lab["push"]({"octo/a.py": "2\n"}, "second")
    lab["push"]({"octo/a.py": "3\n"}, "third")
    assert UPDATE_READY(lab["live"], now=lambda: clock[0]) == 0  # not asked again within the hour
    clock[0] += DP.CHECK_EVERY_S
    assert UPDATE_READY(lab["live"], now=lambda: clock[0]) == 2
    assert git(lab["live"], "rev-parse", "HEAD") != git(lab["live"], "rev-parse", "origin/main")  # fetched, not applied
    assert UPDATE_READY(lab["live"].parent / "no-repo", now=lambda: clock[0] + 99_999) == 2  # no .git: no fetch


def test_the_mac_app_is_rebuilt_after_the_lab_and_a_failed_build_only_warns(lab, monkeypatch):
    lab["push"]({"macapp/Octo.swift": "// 2\n"}, "app")
    code, said = deploy(lab)
    assert code == 0 and lab["done"]["runs"] == [["octo", "launcher", "install"]]
    lab["push"]({"macapp/Octo.swift": "// 3\n"}, "app again")
    monkeypatch.setattr(DP, "_run", lambda cmd, cwd: (_ for _ in ()).throw(DP.DeployError("swiftc: SDK not supported"))
                        if str(cmd[0]).endswith("octo") else subprocess.run([str(c) for c in cmd], cwd=cwd, check=True,
                                                                         capture_output=True, text=True).stdout.strip())
    code, said = deploy(lab)
    assert code == 0 and any("wasn't rebuilt" in s for s in said)


def test_fast_forwards_and_restarts_only_the_lab(lab):
    lab["push"]({"octo/a.py": "2\n"}, "second")
    code, said = deploy(lab)
    assert code == 0
    assert (lab["live"] / "octo" / "a.py").read_text() == "2\n"
    assert lab["done"]["restarts"] == [False] and lab["done"]["runs"] == []
    assert "second" in said[-1]


def test_up_to_date_and_started_does_nothing(lab):
    deploy(lab)  # no marker yet: the first deploy restarts to be sure
    code, said = deploy(lab)
    assert code == 0 and lab["done"]["restarts"] == [False]
    assert "already on" in said[-1]


def test_packages_ui_and_login_items(lab):
    deploy(lab)
    lab["push"]({"requirements.lock": "x==1\n", "ui/package-lock.json": "{}", "octo/relay.py": "r\n"})
    deploy(lab)
    runs = [" ".join(r) for r in lab["done"]["runs"]]
    assert runs == ["python -m pip install --quiet -r requirements.lock",
                    "python -m pip install --quiet --no-deps -e .",
                    "npm ci --no-audit --no-fund", "npm run build"]
    assert lab["done"]["restarts"] == [False, True]


def test_a_deploy_cut_short_is_finished_next_time(lab, monkeypatch):
    deploy(lab)
    lab["push"]({"ui/src/App.vue": "<x/>"})
    monkeypatch.setattr(DP, "wait_up", lambda pid: False)
    with pytest.raises(DP.DeployError, match="lab.log"):
        deploy(lab)
    monkeypatch.setattr(DP, "wait_up", lambda pid: True)
    lab["done"]["runs"].clear()
    code, _ = deploy(lab)  # already pulled, but the lab never started on it: build and restart again
    assert code == 0 and lab["done"]["runs"] == [["npm", "run", "build"]]


def test_waits_for_running_steps_unless_now(lab, monkeypatch):
    counts = iter([2, 0, 0])
    monkeypatch.setattr(DP, "steps_running", lambda: next(counts))
    monkeypatch.setattr(DP.time, "sleep", lambda s: None)
    _, said = deploy(lab)
    assert said[0].startswith("waiting for 2 running steps")
    monkeypatch.setattr(DP, "steps_running", lambda: pytest.fail("--now mustn't wait"))
    lab["push"]({"octo/a.py": "3\n"})
    assert deploy(lab, now=True)[0] == 0


def test_holds_new_steps_back_until_just_before_the_restart(lab, monkeypatch):  # audit 14 Oct
    seen = []
    monkeypatch.setattr(DP, "steps_running", lambda: seen.append(DP.paused(lab["live"])) or 0)
    monkeypatch.setattr(DP, "restart", lambda repo, login_items: seen.append(DP.paused(repo)))
    lab["push"]({"octo/a.py": "2\n"})
    deploy(lab)
    assert seen == [True, False]  # held while it waits and updates; gone when the new lab starts
    (lab["live"] / DP.PAUSE).write_text("999999\n")  # a deploy that was killed holds nothing back
    assert not DP.paused(lab["live"])


def test_a_failed_deploy_lets_the_lab_carry_on(lab, monkeypatch):
    monkeypatch.setattr(DP, "plan", lambda changed: 1 / 0)
    lab["push"]({"octo/a.py": "2\n"})
    with pytest.raises(ZeroDivisionError):
        deploy(lab)
    assert not (lab["live"] / DP.PAUSE).exists()


def test_refuses_a_live_copy_off_main_or_edited(lab):
    (lab["live"] / "octo" / "a.py").write_text("edited\n")
    with pytest.raises(DP.DeployError, match="changes to tracked files"):
        deploy(lab)
    git(lab["live"], "checkout", "-q", "--", ".")
    git(lab["live"], "switch", "-q", "-c", "feature")
    with pytest.raises(DP.DeployError, match="not main"):
        deploy(lab)
    assert lab["done"]["restarts"] == []


def test_refuses_local_commits_not_on_origin(lab):
    commit(lab["live"], {"octo/b.py": "x\n"}, "made in the live copy")
    with pytest.raises(DP.DeployError, match="aren't on origin/main"):
        deploy(lab)


def test_no_login_item(tmp_path):
    with pytest.raises(DP.DeployError, match="login-item install"):
        DP.deploy(say=lambda s: None, agents=tmp_path)


# ---- an install from a release (beta testers: no git) ------------------------------------------------------------

def release(dir_: Path, version: str, files: dict[str, str]) -> str:
    """A release as scripts/beta_release.sh leaves it: octo.tar.gz (octo/…) and version.txt (version, SHA-256) beside it."""
    src = dir_ / "build" / "octo"
    shutil.rmtree(dir_ / "build", ignore_errors=True)
    for name, text in {"install.sh": "#!/bin/bash\necho \"$OCTO_UPDATE\" > installed-by\n", "VERSION": version + "\n",
                       **files}.items():
        (src / name).parent.mkdir(parents=True, exist_ok=True)
        (src / name).write_text(text)
    (src / "install.sh").chmod(0o755)
    subprocess.run(["tar", "-czf", str(dir_ / "octo.tar.gz"), "-C", str(dir_ / "build"), "octo"], check=True)
    sha = hashlib.sha256((dir_ / "octo.tar.gz").read_bytes()).hexdigest()
    (dir_ / "version.txt").write_text(f"{version}\nsha256 {sha}\n")
    return (dir_ / "octo.tar.gz").as_uri()


def test_a_release_install_updates_from_where_it_came_from(tmp_path, monkeypatch):
    monkeypatch.setattr(DP, "steps_running", lambda: 0)
    rel, app, agents = tmp_path / "rel", tmp_path / "app", tmp_path / "agents"
    rel.mkdir(), app.mkdir(), agents.mkdir()
    url = release(rel, "aaa111", {"octo/a.py": "1\n", "octo/gone.py": "x\n"})
    subprocess.run(["tar", "-xzf", str(rel / "octo.tar.gz"), "-C", str(app), "--strip-components", "1"], check=True)
    (app / DP.RELEASE).write_text(url + "\n")
    (app / ".venv").mkdir()
    (app / ".venv" / "kept").write_text("env")
    (agents / f"{DP.LAB}.plist").write_bytes(plistlib.dumps({"WorkingDirectory": str(app)}))
    said = []
    assert DP.deploy(say=said.append, agents=agents) == 0 and said == ["Octo is up to date (aaa111)"]
    monkeypatch.setattr(DP, "_UPDATE", {"at": 0.0, "behind": 0})
    monkeypatch.setattr(DP.threading, "Thread", lambda target, args, **kw: type("T", (), {"start": lambda s: target(*args)})())
    assert UPDATE_READY(app, now=lambda: 10_000.0) == 0

    release(rel, "bbb222", {"octo/a.py": "2\n"})
    assert UPDATE_READY(app, now=lambda: 10_000.0 + DP.CHECK_EVERY_S) == 1
    said.clear()
    assert DP.deploy(say=said.append, agents=agents) == 0
    assert (app / "octo" / "a.py").read_text() == "2\n" and not (app / "octo" / "gone.py").exists()
    assert (app / ".venv" / "kept").read_text() == "env" and (app / DP.RELEASE).exists()  # the environment stays
    assert (app / "installed-by").read_text() == "1\n" and DP.installed(app) == "bbb222"  # its install.sh, as an update
    assert said[-1] == "Octo is now on bbb222" and not (app / DP.PAUSE).exists()


def test_a_release_update_offline_or_broken_changes_nothing(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "VERSION").write_text("aaa111\n")
    (app / DP.RELEASE).write_text((tmp_path / "nowhere" / "octo.tar.gz").as_uri())
    with pytest.raises(DP.DeployError, match="online"):
        DP.update_release(app)
    (tmp_path / "nowhere").mkdir()
    (tmp_path / "nowhere" / "octo.tar.gz").write_text("<html>not found</html>")
    with pytest.raises(DP.DeployError, match="isn't an Octo release"):
        DP.update_release(app, now=True, fetch=lambda url: ("bbb222", "0" * 64))
    assert DP.installed(app) == "aaa111" and not (app / DP.PAUSE).exists()


def test_a_release_update_checks_its_checksum(tmp_path, monkeypatch):
    """Missing or wrong, version.txt's SHA-256 stops the update before anything is unpacked (5 Nov review, risk 2)."""
    monkeypatch.setattr(DP, "_run", lambda *a: pytest.fail("unpacked"))
    rel, app = tmp_path / "rel", tmp_path / "app"
    rel.mkdir(), app.mkdir()
    (app / DP.RELEASE).write_text(release(rel, "2026.11.05-1+bbb222", {"octo/a.py": "2\n"}))
    assert DP.latest((rel / "octo.tar.gz").as_uri())[1] == hashlib.sha256((rel / "octo.tar.gz").read_bytes()).hexdigest()
    (rel / "version.txt").write_text("2026.11.05-1+bbb222\nsha256 " + "0" * 64 + "\n")
    with pytest.raises(DP.DeployError, match="doesn't match its checksum"):
        DP.update_release(app, now=True)
    (rel / "version.txt").write_text("2026.11.05-1+bbb222\n")  # as before 5 Nov: no checksum
    with pytest.raises(DP.DeployError, match="no checksum"):
        DP.update_release(app, now=True)
    assert not (app / DP.PAUSE).exists() and not (app / "octo").exists()


def test_versions_sort_and_an_older_release_is_refused_unless_asked(tmp_path, monkeypatch):
    assert DP.newer("2026.11.05-2+aaa", "2026.11.05-1+bbb") and DP.newer("2026.11.06-1+a", "2026.11.05-10+b")
    assert not DP.newer("2026.11.05-1+bbb", "2026.11.05-2+aaa") and not DP.newer("2026.11.05-2+a", "2026.11.05-2+b")
    assert DP.newer("2026.11.05-1+a", "3f2a9c1b2d4e") and not DP.newer("3f2a9c1b2d4e", "2026.11.05-1+a")  # before 5 Nov
    assert DP.newer("aaa111", "bbb222")  # two from before 5 Nov only differ
    rel, app = tmp_path / "rel", tmp_path / "app"
    rel.mkdir(), app.mkdir()
    (app / "VERSION").write_text("2026.11.05-2+aaa\n")
    (app / DP.RELEASE).write_text(release(rel, "2026.11.05-1+bbb", {"octo/a.py": "1\n"}))
    monkeypatch.setattr(DP, "_UPDATE", {"at": 0.0, "behind": 1})
    DP._check(app)
    assert DP._UPDATE["behind"] == 0  # no "Octo update ready" for an older one
    with pytest.raises(DP.DeployError, match="older than this one.*--allow-older"):
        DP.update_release(app, now=True)
    monkeypatch.setattr(DP, "_run", lambda *a: (_ for _ in ()).throw(DP.DeployError("went on")))
    with pytest.raises(DP.DeployError, match="went on"):  # past the version and checksum to the unpacking
        DP.update_release(app, now=True, allow_older=True)


def test_the_release_is_asked_for_as_octo(monkeypatch):
    # Cloudflare answers Python's own User-Agent with 403 (31 Oct, beta.octolab.online): the lab would never see an update.
    asked = []
    monkeypatch.setattr(DP.urllib.request, "urlopen", lambda req, timeout: asked.append(req) or (_ for _ in ()).throw(OSError()))
    assert DP.latest("https://beta.example/p/octo.tar.gz") is None
    assert asked[0].full_url == "https://beta.example/p/version.txt" and asked[0].get_header("User-agent") == "octo-update"


# ---- versions/<v> beside `current` (5 Nov review, risk 3) ---------------------------------------------------------

SWITCHING_INSTALL = f"""#!/bin/bash
[ -z "${{FAIL_INSTALL:-}}" ] || {{ echo "pip broke"; exit 1; }}
cd "$(dirname "$0")"
PYTHONPATH={ROOT} {sys.executable} -c 'from pathlib import Path; from octo import deploy; deploy.switch(Path.cwd())'
"""


def test_an_update_goes_into_its_own_folder_and_the_one_before_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(DP, "steps_running", lambda: 0)
    base, rel, lab = tmp_path / "octo", tmp_path / "rel", tmp_path / "octo" / "lab.noindex"
    app = base / "app"  # an install from before 5 Nov
    for d in (app / ".venv", rel, lab / "data"):
        d.mkdir(parents=True)
    (app / "VERSION").write_text("aaa111\n")
    (app / ".octo-home").write_text(f"{lab}\n")
    (app / DP.RELEASE).write_text(release(rel, "2026.11.05-1+b", {"install.sh": SWITCHING_INSTALL, "octo/a.py": "1\n"}))
    DP.update_release(app, now=True)
    v1 = base / "versions" / "2026.11.05-1+b"
    assert (base / "current").resolve() == v1 and (base / "previous").resolve() == app
    assert (v1 / ".octo-home").read_text() == f"{lab}\n" and (v1 / DP.RELEASE).exists() and (app / ".venv").exists()
    assert os.readlink(base / "current") == "versions/2026.11.05-1+b"  # relative: the folder can move

    cur = base / "current"
    release(rel, "2026.11.05-2+c", {"install.sh": SWITCHING_INSTALL, "octo/a.py": "2\n"})
    DP.update_release(cur, now=True)
    assert cur.resolve() == base / "versions" / "2026.11.05-2+c" and (base / "previous").resolve() == v1
    assert app.is_symlink() and app.resolve() == cur.resolve()  # two back: gone, and `app` names what runs

    release(rel, "2026.11.05-3+d", {"install.sh": SWITCHING_INSTALL, "octo/a.py": "3\n"})
    monkeypatch.setenv("FAIL_INSTALL", "1")
    with pytest.raises(DP.DeployError, match="the one you had is still running"):
        DP.update_release(cur, now=True)
    assert (cur / "octo" / "a.py").read_text() == "2\n" and not (base / "versions" / "2026.11.05-3+d").exists()


def test_a_version_that_isnt_a_folder_name_is_refused(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / DP.RELEASE).write_text("file:///x/octo.tar.gz")
    with pytest.raises(DP.DeployError, match="isn't one"):
        DP.update_release(app, now=True, fetch=lambda url: ("../../evil", "0" * 64), allow_older=True)


def test_switch_never_removes_a_folder_with_a_lab_in_it(tmp_path):
    base = tmp_path / "octo"
    (base / "app" / "data").mkdir(parents=True)  # an old install that kept its lab inside
    for v in ("a", "b", "c"):  # each built, then switched to, as install.sh does
        (base / "versions" / v).mkdir(parents=True)
        DP.switch(base / "versions" / v)
    assert sorted(p.name for p in (base / "versions").iterdir()) == ["b", "c"] and (base / "app" / "data").exists()


def test_back_runs_the_one_before_unless_the_database_moved_on(tmp_path, monkeypatch):
    import sqlite3
    base, agents, lab = tmp_path / "octo", tmp_path / "agents", tmp_path / "octo" / "lab.noindex"
    for v, schema in (("v1", 20), ("v2", 21)):
        (base / "versions" / v / "octo").mkdir(parents=True)
        (base / "versions" / v / "octo" / "db.py").write_text(f"SCHEMA_VERSION = {schema}  # …\n")
        (base / "versions" / v / "VERSION").write_text(f"{v}\n")
        (base / "versions" / v / ".octo-home").write_text(f"{lab}\n")
        DP.switch(base / "versions" / v)
    (lab / "data").mkdir(parents=True), agents.mkdir()
    conn = sqlite3.connect(lab / "data" / "octo.db")
    conn.execute("PRAGMA user_version = 21")
    conn.close()
    (agents / f"{DP.LAB}.plist").write_bytes(plistlib.dumps({"WorkingDirectory": str(base / "current")}))
    restarted = []
    monkeypatch.setattr(DP, "_run", lambda cmd, cwd: restarted.append([str(c) for c in cmd]) or "")
    with pytest.raises(DP.DeployError, match="pre-schema-20.db"):
        DP.back(say=lambda s: None, agents=agents)
    assert (base / "current").resolve().name == "v2" and not restarted
    conn = sqlite3.connect(lab / "data" / "octo.db")
    conn.execute("PRAGMA user_version = 20")
    conn.close()
    said = []
    assert DP.back(say=said.append, agents=agents) == 0
    assert (base / "current").resolve().name == "v1" and (base / "previous").resolve().name == "v2"
    assert restarted[0][1:] == ["login-item", "install"] and said[-1] == "Octo is back on v1"
