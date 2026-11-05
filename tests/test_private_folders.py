"""Security review M1, 28 Oct: a new lab's folders and database were readable by other users on the same Mac."""

import os

from octo import db as DB


def test_the_labs_folders_and_database_are_the_owners_alone(tmp_path, monkeypatch):
    home = tmp_path / "lab"
    for d in ("data", "audio/lab", "notes"):
        (home / d).mkdir(parents=True, mode=0o755)
        (home / d).chmod(0o755)
    monkeypatch.setattr(DB, "HOME", home)
    monkeypatch.setattr(DB, "ROOT", tmp_path / "code")
    monkeypatch.setattr(DB, "DATA", home / "data")
    monkeypatch.setattr(DB, "AUDIO", home / "audio" / "lab")
    monkeypatch.setattr(DB, "NOTES", home / "notes")
    monkeypatch.setattr(DB, "DB_PATH", home / "data" / "octo.db")
    monkeypatch.setattr(DB, "HOME_FROM", "checkout")
    monkeypatch.setattr(DB, "_made_private", False)
    calls = []
    real = DB.private
    monkeypatch.setattr(DB, "private", lambda: (calls.append(1), real())[1])
    before = os.umask(0o022)
    try:
        DB.connect(home / "data" / "octo.db").close()
        DB.connect(home / "data" / "octo.db").close()  # a second time locks the file the first one made
        assert os.umask(0o077) == 0o077  # files made from here on are the owner's alone
        assert len(calls) == 1  # once a process, not on every connection (review 5 Nov)
    finally:
        os.umask(before)
    for p in (home, home / "data", home / "audio", home / "notes"):
        assert p.stat().st_mode & 0o777 == 0o700, p
    assert (home / "data" / "octo.db").stat().st_mode & 0o777 == 0o600
