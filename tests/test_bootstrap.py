"""The one-line installer (scripts/bootstrap.sh) and install.sh's checks, run for real in a made-up HOME (install review,
5 Nov): a re-run replaces the code and keeps the lab's own, a refused check touches nothing, and --check changes nothing."""

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
echo "${OCTO_NEW_LAB:-}" > "$(dirname "$0")/installed-new-lab"
"""


def release(tmp: Path, name: str, files: dict[str, str]) -> str:
    src = tmp / name / "octo"
    for f, text in {"install.sh": FAKE_INSTALL, **files}.items():
        (src / f).parent.mkdir(parents=True, exist_ok=True)
        (src / f).write_text(text)
    (src / "install.sh").chmod(0o755)
    subprocess.run(["tar", "-czf", str(tmp / f"{name}.tar.gz"), "-C", str(tmp / name), "octo"], check=True)
    return (tmp / f"{name}.tar.gz").as_uri()


def bootstrap(home: Path, url: str, **env) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(BOOTSTRAP)], capture_output=True, text=True,
                          env={"PATH": os.environ["PATH"], "HOME": str(home), "OCTO_RELEASE": url, **env})


def test_a_rerun_replaces_the_code_and_keeps_the_lab(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    app = home / "Library" / "Application Support" / "octo" / "app"
    first = bootstrap(home, release(tmp_path, "r1", {"octo/a.py": "1\n", "octo/gone.py": "x\n"}))
    assert first.returncode == 0, first.stdout + first.stderr
    assert (app / "installed-new-lab").read_text() == "1\n"  # a new lab, in the folder the installer keeps it in
    assert (app / ".octo-home").read_text().strip().endswith("lab.noindex")
    assert (app.parent / "lab.noindex").stat().st_mode & 0o777 == 0o700
    (app / ".venv").mkdir()
    (app / ".venv" / "kept").write_text("")
    second = bootstrap(home, release(tmp_path, "r2", {"octo/a.py": "2\n"}))
    assert second.returncode == 0, second.stdout + second.stderr
    assert (app / "octo" / "a.py").read_text() == "2\n" and not (app / "octo" / "gone.py").exists()
    assert (app / ".venv" / "kept").exists() and (app / ".octo-home").exists() and not (app / DP.PAUSE).exists()


def test_a_refused_check_touches_nothing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    app = home / "Library" / "Application Support" / "octo" / "app"
    bootstrap(home, release(tmp_path, "r1", {"octo/a.py": "1\n"}))
    got = bootstrap(home, release(tmp_path, "r2", {"octo/a.py": "2\n"}), FAIL_CHECK="1")
    assert got.returncode == 1 and "Only 9 GB free" in got.stdout
    assert (app / "octo" / "a.py").read_text() == "1\n"


def test_no_release_there_is_not_called_offline(tmp_path):
    got = bootstrap(tmp_path, (tmp_path / "nowhere.tar.gz").as_uri())
    assert got.returncode == 1 and "online" in got.stdout  # file:// has no status: it can only be "couldn't download"
    (tmp_path / "page.tar.gz").write_text("<!doctype html>")
    got = bootstrap(tmp_path, (tmp_path / "page.tar.gz").as_uri())
    assert got.returncode == 1 and "isn't an Octo release" in got.stdout


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
