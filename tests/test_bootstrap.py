"""The one-line installer (scripts/bootstrap.sh) and install.sh's checks, run for real in a made-up HOME (install review,
5 Nov): a re-run replaces the code and keeps the lab's own, a refused check touches nothing, and --check changes nothing."""

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from octo import ROOT
from octo import deploy as DP

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="the installer is for macOS")
BOOTSTRAP = ROOT / "scripts" / "bootstrap.sh"
FAKE_INSTALL = """#!/bin/bash
if [ "${1:-}" = "--check" ]; then [ -z "${FAIL_CHECK:-}" ] || { echo "Only 9 GB free here."; exit 1; }; exit 0; fi
cd "$(dirname "$0")"
echo "${OCTO_NEW_LAB:-}" > installed-new-lab
# As the real one's switch (octo/deploy.py `switch`, tested in test_deploy.py): built, so it's the one that runs.
[ "$(basename "$(dirname "$PWD")")" != versions ] || ln -sfn "versions/$(basename "$PWD")" ../../current
"""
CURRENT = Path("Library") / "Application Support" / "octo" / "current"


def release(tmp: Path, name: str, files: dict[str, str], version: str = "") -> str:
    src = tmp / name / "octo"
    for f, text in {"install.sh": FAKE_INSTALL, "VERSION": f"{version or name}\n", **files}.items():
        (src / f).parent.mkdir(parents=True, exist_ok=True)
        (src / f).write_text(text)
    (src / "install.sh").chmod(0o755)
    subprocess.run(["tar", "-czf", str(tmp / f"{name}.tar.gz"), "-C", str(tmp / name), "octo"], check=True)
    sha = hashlib.sha256((tmp / f"{name}.tar.gz").read_bytes()).hexdigest()
    (tmp / "version.txt").write_text(f"2026.11.05-1+{name}\nsha256 {sha}\n")  # as scripts/release_files.sh writes it
    return (tmp / f"{name}.tar.gz").as_uri()


def bootstrap(home: Path, url: str, **env) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(BOOTSTRAP)], capture_output=True, text=True,
                          env={"PATH": os.environ["PATH"], "HOME": str(home), "OCTO_RELEASE": url, **env})


def test_a_new_version_gets_its_own_folder_and_a_rerun_repairs_it(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    cur, versions = home / CURRENT, home / CURRENT.parent / "versions"
    first = bootstrap(home, release(tmp_path, "r1", {"octo/a.py": "1\n", "octo/gone.py": "x\n"}))
    assert first.returncode == 0, first.stdout + first.stderr
    assert cur.resolve() == (versions / "r1").resolve()
    assert (cur / "installed-new-lab").read_text() == "1\n"  # a new lab, in the folder the installer keeps it in
    assert (cur / ".octo-home").read_text().strip().endswith("lab.noindex")
    assert (cur.parent / "lab.noindex").stat().st_mode & 0o777 == 0o700
    (cur / ".venv").mkdir()
    (cur / ".venv" / "kept").write_text("")
    again = bootstrap(home, release(tmp_path, "r1-again", {"octo/a.py": "1b\n"}, version="r1"))  # the same version: repaired
    assert again.returncode == 0, again.stdout + again.stderr
    assert (cur / "octo" / "a.py").read_text() == "1b\n" and not (cur / "octo" / "gone.py").exists()
    assert (cur / ".venv" / "kept").exists() and not (cur / DP.PAUSE).exists()
    second = bootstrap(home, release(tmp_path, "r2", {"octo/a.py": "2\n"}))
    assert second.returncode == 0, second.stdout + second.stderr
    assert cur.resolve() == (versions / "r2").resolve() and (cur / "octo" / "a.py").read_text() == "2\n"
    assert (cur / ".octo-home").read_text() == (versions / "r1" / ".octo-home").read_text()  # the same lab
    assert (versions / "r1" / ".venv" / "kept").exists()  # the one before, untouched


def test_an_install_from_before_versions_keeps_its_lab(tmp_path):
    home = tmp_path / "home"
    app = home / CURRENT.parent / "app"
    app.mkdir(parents=True)
    (app / ".octo-home").write_text("/somewhere/else\n")
    got = bootstrap(home, release(tmp_path, "r1", {"octo/a.py": "1\n"}))
    assert got.returncode == 0, got.stdout + got.stderr
    assert (home / CURRENT / ".octo-home").read_text() == "/somewhere/else\n"


def test_a_refused_check_touches_nothing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bootstrap(home, release(tmp_path, "r1", {"octo/a.py": "1\n"}))
    got = bootstrap(home, release(tmp_path, "r2", {"octo/a.py": "2\n"}), FAIL_CHECK="1")
    assert got.returncode == 1 and "Only 9 GB free" in got.stdout
    assert (home / CURRENT / "octo" / "a.py").read_text() == "1\n" and not (home / CURRENT.parent / "versions" / "r2").exists()


def test_no_release_there_is_not_called_offline(tmp_path):
    got = bootstrap(tmp_path, (tmp_path / "nowhere.tar.gz").as_uri())
    assert got.returncode == 1 and "online" in got.stdout  # file:// has no status: it can only be "couldn't download"
    (tmp_path / "page.tar.gz").write_text("<!doctype html>")
    got = bootstrap(tmp_path, (tmp_path / "page.tar.gz").as_uri())
    assert got.returncode == 1 and "isn't an Octo release" in got.stdout


def test_a_missing_or_wrong_checksum_installs_nothing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    app = home / CURRENT.parent
    url = release(tmp_path, "r1", {"octo/a.py": "1\n"})
    good = (tmp_path / "version.txt").read_text()
    (tmp_path / "version.txt").write_text("2026.11.05-1+r1\nsha256 " + "0" * 64 + "\n")
    got = bootstrap(home, url)
    assert got.returncode == 1 and "doesn't match its checksum" in got.stdout and not app.exists()
    (tmp_path / "version.txt").write_text("r1\n")  # a release from before 5 Nov: a version alone
    got = bootstrap(home, url)
    assert got.returncode == 1 and "no checksum" in got.stdout and not app.exists()
    (tmp_path / "version.txt").write_text(good)
    assert bootstrap(home, url).returncode == 0 and (home / CURRENT / "octo" / "a.py").exists()


def test_bootstrap_keeps_what_an_update_keeps():
    """A re-run of the one-liner and `octo update` replace the code the same way."""
    kept = set(re.findall(r"--exclude=/(\S+)", BOOTSTRAP.read_text()))
    assert kept == set(DP.KEEP)


def test_install_check_refuses_a_protected_folder_and_changes_nothing(tmp_path):
    home = tmp_path / "home"
    where = home / "Downloads" / "octo"
    where.mkdir(parents=True)
    shutil.copy(ROOT / "install.sh", where / "install.sh")
    got = subprocess.run([str(where / "install.sh"), "--check"], capture_output=True, text=True,
                         env={"PATH": os.environ["PATH"], "HOME": str(home)})
    assert got.returncode == 1 and "inside Downloads" in got.stdout
    assert "stopped at" not in got.stdout  # a refusal, not a download cut short
    assert sorted(p.name for p in where.iterdir()) == ["install.sh"]
