"""The lab's home (14 Oct, the move out of ~/Downloads): the code in one folder, what it records in another.
Where HOME comes from, that a home set on purpose with no lab in it doesn't quietly start a new one, what
`octo doctor` says about it, and that what the database stores stays relative to HOME, never to the code."""

import plistlib
import re
from pathlib import Path

import pytest

import octo
from octo import db as DB
from octo import doctor as DR
from octo import login_item as LI
from octo.db import connect

CODE = Path(octo.__file__).resolve().parent.parent


def test_where_home_comes_from(tmp_path, monkeypatch):
    monkeypatch.setattr(octo, "HOME_FILE", tmp_path / ".octo-home")
    monkeypatch.delenv("OCTO_HOME", raising=False)
    assert octo._home() == (octo.ROOT, "checkout")  # as before: the checkout itself
    octo.HOME_FILE.write_text("  \n")
    assert octo._home() == (octo.ROOT, "checkout")  # an empty file says nothing
    octo.HOME_FILE.write_text(f"{tmp_path / 'lab'}\n")
    assert octo._home() == ((tmp_path / "lab").resolve(), ".octo-home")
    monkeypatch.setenv("OCTO_HOME", str(tmp_path / "other"))
    assert octo._home() == ((tmp_path / "other").resolve(), "OCTO_HOME")  # the environment wins


def test_a_home_with_no_lab_in_it_isnt_a_new_lab(tmp_path, monkeypatch):
    db = tmp_path / "moved" / "data" / "octo.db"
    monkeypatch.setattr(DB, "DB_PATH", db)
    monkeypatch.setattr(DB, "HOME_FROM", ".octo-home")
    monkeypatch.delenv("OCTO_NEW_LAB")
    with pytest.raises(RuntimeError, match="no lab at"):
        connect(db)
    assert not db.parent.exists()  # nothing made on the way
    connect(tmp_path / "elsewhere.db").close()  # any other database opens as before
    monkeypatch.setattr(DB, "HOME_FROM", "checkout")
    connect(db).close()  # the checkout as home: a new lab starts, as it always has
    monkeypatch.setattr(DB, "HOME_FROM", ".octo-home")
    connect(db).close()  # and once it's there, it opens


def test_doctor_on_the_home(tmp_path, monkeypatch):
    home = tmp_path / "lab"
    (home / "audio" / "lab").mkdir(parents=True)
    home.chmod(0o755)
    monkeypatch.setattr(DR, "HOME", home)
    conn = connect(tmp_path / "t.db")
    (home / "audio" / "lab" / "a.flac").write_bytes(b"x")
    for rid, path in (("a", "audio/lab/a.flac"), ("b", "audio/lab/b.flac")):
        conn.execute("INSERT INTO recordings (id, path, source, duration_s, created_at) VALUES (?, ?, 'phone', 1, 0)", (rid, path))
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "com.octo.lab.plist").write_bytes(plistlib.dumps({"WorkingDirectory": "/somewhere/else"}))
    got = {c.what: c for c in DR.home(conn, agents=agents)}
    assert str(home) in got["The lab's folder"].detail
    assert got["Only you can open it"].mark == DR.WARN and "chmod 700" in got["Only you can open it"].fix
    rec = got["Recordings where the database says"]
    assert rec.mark == DR.FAIL and rec.detail == "1 of 2"
    assert got["The login item runs this copy"].mark == DR.WARN
    home.chmod(0o700)
    conn.execute("DELETE FROM recordings WHERE id='b'")
    (agents / "com.octo.lab.plist").write_bytes(plistlib.dumps(
        {"WorkingDirectory": str(DR.ROOT), "EnvironmentVariables": {"OCTO_HOME": str(home)}}))
    assert all(c.mark == DR.OK for c in DR.home(conn, agents=agents))


def test_the_login_item_keeps_what_it_writes_private_and_names_the_home(tmp_path, monkeypatch):
    plists = LI._plists()
    assert all(p["Umask"] == 0o077 for p in plists.values())
    monkeypatch.setattr(LI, "HOME", tmp_path)
    lab = LI._plists()["com.octo.lab"]
    assert lab["EnvironmentVariables"] == {"OCTO_HOME": str(tmp_path)} and lab["WorkingDirectory"] == str(LI.ROOT)
    monkeypatch.setattr(LI, "HOME", LI.ROOT)
    assert "EnvironmentVariables" not in LI._plists()["com.octo.lab"]


# What the database stores is relative to HOME, and what the lab records lives under it: a stored path joined to
# or made relative to ROOT (the code) works only while the two are the same folder, and breaks Delete and Forget
# the day they aren't. (The recorder's secrets.h files are code-side: firmware is built from them.)
STORED = re.compile(r'ROOT\s*/\s*"(data|audio|notes|runs)"|ROOT\s*/\s*(rec|row|c|s|sess|chunk)\b\s*[\[(]|(?<!SECRETS_H)(?<!WIFI_SECRETS)\.relative_to\(ROOT\)')


def test_nothing_the_lab_records_is_placed_by_the_code_folder():
    found = [f"{f.relative_to(CODE)}:{n}" for d in ("octo", "bench") for f in sorted((CODE / d).rglob("*.py"))
             for n, line in enumerate(f.read_text().splitlines(), 1) if STORED.search(line)]
    assert found == []


def test_the_tests_run_in_a_home_of_their_own():
    assert octo.HOME_FROM == "OCTO_HOME" and octo.HOME != octo.ROOT and "octo-test-home-" in str(octo.HOME)
