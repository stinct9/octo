"""Export and import (octo/export.py, docs/export-plan.md): a made-up lab in the test's own folder, through real
encrypted disk images (hdiutil), never the owner's lab."""

import json
import re
import sqlite3
from contextlib import nullcontext
from pathlib import Path

import pytest

from octo import ROOT
from octo import db as DB
from octo import export as EX

PW = "correct-horse-battery"


def make_lab(home: Path, recordings: int = 1) -> None:
    (home / "data" / "phone").mkdir(parents=True)
    conn = DB.connect(home / "data" / "octo.db")
    for i in range(recordings):
        rel = f"audio/lab/recordings/r{i}.flac"
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_bytes(b"audio %d" % i * 1000)
        conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES (?, 'file', ?, 60, 1)",
                     (f"r{i}", rel))
        conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) "
                     "VALUES (?, ?, 0, 60, 50, 1)", (f"s{i}", f"r{i}"))
    conn.execute("INSERT INTO people (id, name, created_at) VALUES ('p1', 'Someone', 1)")
    (home / "audio/lab/drills").mkdir(parents=True)  # B18: a practice take goes with the lab
    (home / "audio/lab/drills/d0.flac").write_bytes(b"take" * 100)
    conn.execute("INSERT INTO drills (id, prompt, path, duration_s, created_at) VALUES ('d0', 'p', 'audio/lab/drills/d0.flac', 9, 1)")
    conn.close()
    for rel, text in {"data/device.json": "{}", "data/phone/ca.key": "ca", "data/mac.key": "old key",
                      "data/backups/octo-2026-10-01.db": "old", "data/logs/lab.log": "log", "data/process.log": "log",
                      "data/octo.db.lock": "", "data/vadcompare/x.json": "{}", "data/compare/s0.json": "{}",
                      "audio/lab/sessions/s0-0-60000-l.wav": "clip", "notes/n.md": "note", "runs/r.jsonl": "{}"}.items():
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(text)


class Lab(Path):
    """The lab's folder, which can point the module at another one (a fresh install's)."""


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """A lab at tmp/lab, with the module pointed at it and the login items left alone."""
    home = Lab(tmp_path / "lab")
    make_lab(home)

    def point(h: Path):
        for name, value in {"HOME": h, "DATA": h / "data", "DB_PATH": h / "data" / "octo.db",
                            "STAGE": h / ".export-staging.noindex",
                            "IMPORTING": h.parent / "octo-importing.noindex"}.items():
            monkeypatch.setattr(EX, name, value)
    point(home)
    monkeypatch.setattr(EX, "OUT", tmp_path / "exports")
    monkeypatch.setattr(EX, "_installed_here", lambda: tmp_path / "code")
    monkeypatch.setattr(EX, "lab_held", lambda *a, **k: nullcontext())
    monkeypatch.setattr(EX, "agents", lambda *a: [])
    monkeypatch.setattr(EX, "CLOUD", ())
    home.point = point
    return home


def test_what_goes_in():
    for rel in ("data/device.json", "data/phone/ca.key", "data/phone/settings.json", "data/compare/s.json",
                "data/ticktick.json", "data/providers.json", "data/rubric.md", "notes/n.md", "runs/r.jsonl"):
        assert not EX.skipped(rel), rel
    for rel in ("data/octo.db", "data/octo.db-wal", "data/mac.key", "data/backups/octo-2026-10-01.db", "data/logs/lab.log",
                "data/process.log", "data/octo.db.lock", "data/phone/settings.json.lock", "data/vadcompare/x.json",
                "data/diarcompare/x", "data/bench/x.wav", "data/uploads/x", "data/.octo-can-save",
                "data/.device.json.123.ab.tmp", "data/phone/server.csr", "data/device_status.json"):
        assert EX.skipped(rel), rel


def test_every_file_the_code_keeps_in_data_is_classified():
    """A file a new feature adds under data/ is in the export unless named here: this makes the choice on purpose."""
    names = set()
    for f in (ROOT / "octo").glob("*.py"):
        names |= set(re.findall(r'DATA / "([^"/]+)"', f.read_text()))
    unknown = {n for n in names if n not in EX.KEPT and not EX.skipped(f"data/{n}") and not EX.skipped(f"data/{n}/x")}
    assert not unknown, f"add each to export.KEPT or SKIP_*: {unknown}"


def test_safe_paths():
    assert EX.safe_rel("audio/lab/recordings/a.flac")
    for bad in ("/etc/passwd", "../x", "data/../../x", "audio/lab/../../.ssh/id", "audio/x.wav", "data//x", "data/./x", "code/x"):
        assert not EX.safe_rel(bad), bad


def test_round_trip_into_an_empty_lab(lab, tmp_path, monkeypatch):
    out = EX.export(password=PW, say=lambda *a: None)
    assert out.parent == tmp_path / "exports" and out.stat().st_mode & 0o777 == 0o600
    assert not EX.STAGE.exists()
    with EX.mounted(out, PW) as point:
        man = EX.read_manifest(point)
        assert (point / ".metadata_never_index").exists() and (point / "README.txt").exists()
    files = set(man["files"])
    assert {"data/octo.db", "audio/lab/recordings/r0.flac", "data/device.json", "data/phone/ca.key",
            "data/compare/s0.json", "notes/n.md", "runs/r.jsonl", "audio/lab/drills/d0.flac"} <= files
    assert not {f for f in files if "backups" in f or "logs" in f or "sessions/" in f or f.endswith((".log", ".lock"))
                or f == "data/mac.key" or "vadcompare" in f}
    assert man["counts"]["conversations"] == 1 and man["counts"]["people"] == 1
    c = sqlite3.connect(lab / "data" / "octo.db")
    assert json.loads(c.execute("SELECT value FROM app_config WHERE key='last_export_at'").fetchone()[0]) == man["made_at"]
    c.close()

    new = tmp_path / "new" / "lab"  # a fresh install: an empty lab with its own browser key
    (new / "data").mkdir(parents=True)
    DB.connect(new / "data" / "octo.db").close()
    (new / "data" / "mac.key").write_text("new key")
    lab.point(new)
    assert EX.import_lab(str(out), password=PW, yes=True, say=lambda *a: None) == 0
    assert (new / "audio/lab/recordings/r0.flac").read_bytes() == (lab / "audio/lab/recordings/r0.flac").read_bytes()
    assert (new / "data" / "mac.key").read_text() == "new key"  # this install's, not the export's
    assert EX.counts(new / "data" / "octo.db") == man["counts"]
    assert sqlite3.connect(new / "data" / "octo.db").execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert not EX.IMPORTING.exists() and not EX.asides(new.parent)  # the empty lab it replaced isn't kept


def test_import_refuses_a_lab_with_recordings_and_replace_sets_it_aside(lab, tmp_path):
    out = EX.export(password=PW, say=lambda *a: None)
    with pytest.raises(EX.ExportError, match="already has 1 recordings"):
        EX.import_lab(str(out), password=PW, yes=True, say=lambda *a: None)
    assert EX.import_lab(str(out), replace=True, password=PW, yes=True, say=lambda *a: None) == 0
    [aside] = EX.asides(lab.parent)
    assert (aside / "audio/lab/recordings/r0.flac").exists() and not (aside / "audio/lab/sessions").exists()


def test_password_is_exact(lab):
    """No newline goes with it: a password typed in Finder must open the image (hdiutil keeps a trailing newline)."""
    out = EX.export(password=PW, say=lambda *a: None)
    with pytest.raises(EX.ExportError, match="wrong password"):
        with EX.mounted(out, PW + "\n"):
            pass
    with pytest.raises(EX.ExportError):
        EX.export(password="short", say=lambda *a: None)


def test_an_image_hdiutil_left_attached_is_detached(tmp_path):
    """hdiutil create now and then leaves its image attached; the check's attach then failed as "a wrong password"."""
    import plistlib
    import subprocess
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "f").write_text("x")
    image = tmp_path / "left.dmg"
    subprocess.run(["hdiutil", "create", "-quiet", "-srcfolder", tmp_path / "src", "-format", "ULFO", image], check=True)
    subprocess.run(["hdiutil", "attach", "-quiet", "-nobrowse", "-noautoopen", "-mountpoint", tmp_path / "m", image],
                   check=True)

    def attached():
        info = plistlib.loads(subprocess.run(["hdiutil", "info", "-plist"], capture_output=True).stdout)
        return [im for im in info.get("images", []) if Path(im["image-path"]).resolve() == image.resolve()]
    assert attached()
    EX.detach_leftover(image)
    assert not attached()
    EX.detach_leftover(image)  # nothing attached: nothing to do


def test_a_crafted_database_is_refused(tmp_path):
    db = tmp_path / "octo.db"
    DB.connect(db).close()
    c = sqlite3.connect(db)
    c.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('x', 'file', '../../.ssh/id', 1, 1)")
    c.commit()
    with pytest.raises(EX.ExportError, match="outside the lab"):
        EX.check_db(db)
    c.execute("DELETE FROM recordings")
    c.execute("CREATE TRIGGER t AFTER INSERT ON people BEGIN DELETE FROM sessions; END")
    c.commit()
    with pytest.raises(EX.ExportError, match="code Octo doesn't make"):
        EX.check_db(db)
    c.execute("DROP TRIGGER t")
    c.execute(f"PRAGMA user_version = {DB.SCHEMA_VERSION + 1}")
    c.commit()
    c.close()
    with pytest.raises(EX.ExportError, match="newer Octo"):
        EX.check_db(db)


def test_the_manifest_cant_reach_outside(tmp_path):
    # L8 (security review 28 Oct): nor bring what an export never writes, like the filler model built into the recorder
    for rel in ("../evil", "data/mac.key", "/etc/x", "data/filler/octo_filler_model.h", "data/logs/lab.log", "data/x.lock"):
        (tmp_path / "manifest.json").write_text(json.dumps(
            {"format": 1, "files": {"data/octo.db": {"size": 1, "sha256": ""}, rel: {"size": 1, "sha256": ""}}}))
        with pytest.raises(EX.ExportError, match="can't hold"):
            EX.read_manifest(tmp_path)
    f = tmp_path / "f"
    f.write_bytes(b"12345")
    with pytest.raises(EX.ExportError, match="isn't the file"):
        EX.check_file(f, 4, "")
    (tmp_path / "link").symlink_to(f)
    with pytest.raises(EX.ExportError, match="isn't the file"):
        EX.check_file(tmp_path / "link", 5, "")


def test_sweep(lab):
    EX.STAGE.mkdir()
    (EX.STAGE / ".pid").write_text("999999")  # its export is gone
    half = EX.IMPORTING
    half.mkdir()
    (half / EX.MARK).write_text("999999")
    old = lab.parent / "octo-before-import-20261001-120000.noindex"
    new = lab.parent / "octo-before-import-20261018-120000.noindex"
    old.mkdir(), new.mkdir()
    from datetime import datetime
    EX.sweep(now=datetime(2026, 10, 19).timestamp(), say=lambda *a: None)
    assert not EX.STAGE.exists() and not half.exists() and not old.exists() and new.exists()
    half.mkdir()  # no mark: a lab an interrupted swap moved out, so it's set aside, not deleted
    EX.sweep(now=datetime(2026, 10, 19).timestamp(), say=lambda *a: None)
    assert not half.exists() and len(EX.asides(lab.parent)) == 2


def test_swap_is_one_step(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    (a / "x").write_text("a")
    (b / "x").write_text("b")
    EX.swap(a, b)
    assert (a / "x").read_text() == "b" and (b / "x").read_text() == "a"


def test_preview_says_what_import_would_do_and_changes_nothing(lab, tmp_path):
    out = EX.export(password=PW, say=lambda *a: None)
    before = sorted(p.name for p in lab.parent.iterdir())
    pre = EX.preview(str(out), PW)
    assert pre["have"] == 1 and pre["counts"]["conversations"] == 1 and "Anything deleted" in pre["text"]
    assert sorted(p.name for p in lab.parent.iterdir()) == before


def test_a_lab_that_keeps_recordings_exports_them_after_60_days(lab, monkeypatch):  # B29
    from octo import labconfig as LC
    from octo import privacy as PV
    monkeypatch.setitem(LC.TEST_OVERRIDES, "keep_recordings", True)
    conn = DB.connect(lab / "data" / "octo.db")
    assert PV.drop_old_audio(conn) == []  # r0 was added in 1970
    conn.close()
    out = EX.export(password=PW, say=lambda *a: None)
    with EX.mounted(out, PW) as point:
        assert "audio/lab/recordings/r0.flac" in EX.read_manifest(point)["files"]
