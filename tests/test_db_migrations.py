"""The schema and its migrations (review 8 Oct, finding 1): once per database, under one write lock, skipped
when the database is at SCHEMA_VERSION. And data/device.json written whole, never over a file that didn't
read (finding 2)."""

import hashlib
import inspect
import json
import sqlite3
import threading

import pytest

from octo import db as DB
from octo import device as D
from octo import labconfig as LC


def test_a_new_database_opened_by_many_at_once_migrates_once_without_errors(tmp_path):
    """Reproduced before the fix: 102 "duplicate column" errors in 30 new databases opened by 6 threads each."""
    errors = []
    for n in range(30):
        path = tmp_path / f"new{n}.db"
        start = threading.Barrier(8)

        def open_it():
            try:
                start.wait()
                DB.connect(path).execute("SELECT consent FROM people LIMIT 1").fetchall()
            except Exception as e:  # noqa: BLE001 - any failure is the bug
                errors.append(repr(e))
        threads = [threading.Thread(target=open_it) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert errors == []
    conn = DB.connect(tmp_path / "new0.db")
    assert conn.execute("PRAGMA user_version").fetchone()[0] == DB.SCHEMA_VERSION
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_an_older_database_gets_its_columns_once(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, recording_id TEXT, start_s REAL, end_s REAL, status TEXT)")
    old.execute("INSERT INTO sessions (id, status) VALUES ('ses_1', 'active')")
    old.commit()
    old.close()
    conn = DB.connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)")}
    assert {"kept", "reviewed_at", "provisional", "left_out_spans"} <= cols
    assert conn.execute("SELECT id FROM sessions").fetchone()["id"] == "ses_1"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == DB.SCHEMA_VERSION
    # At the version, a connection runs neither the schema nor the migrations.
    calls = []
    real = DB._prepare
    DB._prepare = lambda c: calls.append(c)
    try:
        DB.connect(path)
    finally:
        DB._prepare = real
    assert calls == []


def test_changing_the_schema_needs_a_new_version():
    """SCHEMA and _migrate as they were at SCHEMA_VERSION. A change without a bump would never reach the labs
    that are already at it: bump SCHEMA_VERSION, then put the new fingerprint here."""
    seen = hashlib.sha256((DB.SCHEMA + inspect.getsource(DB._migrate)).encode()).hexdigest()[:16]
    assert {1: "1c51cdf3f06d1522", 2: "54686faa5b108404", 3: "ea5edcb9f747cfed", 4: "e3aaf20b56b01f00", 5: "d29d1c5660c19823",
            6: "39d396643d83f0a9", 7: "ecb012df8aa45162", 8: "90169815ed0e2e6d", 9: "0695602aa7936b11", 10: "8a5bae1a291c8db0", 11: "4e00a1248b00861c", 12: "b2108a075833d24a", 13: "ebdabdd6bba688b4",
            14: "1c9fdad7bcd03498", 15: "e5a3924225090778", 16: "4dfc8acc087835cc", 17: "8b8134991b9f6c6d", 18: "ff6ee1fbf2df50ca", 19: "aadd92d97a5fe083", 20: "0273e559d957842d", 21: "a2da785ec2e2ec61"}.get(DB.SCHEMA_VERSION) == seen, f"bump SCHEMA_VERSION; then expect {seen}"


@pytest.fixture
def own_config(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "CONFIG", tmp_path / "device.json")
    return D.CONFIG


def test_device_config_is_written_whole_and_never_over_one_that_did_not_read(own_config):
    D.update_config(lambda c: c.update(key="aa" * 32, enc_private="bb" * 32))
    assert oct(own_config.stat().st_mode & 0o777) == "0o600"
    assert [p.name for p in own_config.parent.iterdir() if p.name.endswith(".tmp")] == []  # no leftovers
    own_config.write_text('{"key": "aa')  # torn, or damaged by hand
    with pytest.raises(D.DeviceError, match="can't be read"):
        D.update_config(lambda c: c.update(ip="192.168.1.9"))
    assert own_config.read_text() == '{"key": "aa'  # left alone for a person to look at


def test_a_new_address_never_drops_the_keys(own_config, monkeypatch):
    """Before 8 Oct a failed read became {} and the recorder's address was written over everything."""
    D.update_config(lambda c: c.update(key="aa" * 32, enc_private="bb" * 32, host="octo-rec.local"))
    monkeypatch.setattr(D, "resolve", lambda host: "192.168.1.50")
    D.Device("octo-rec.local", "aa" * 32)._address()
    cfg = json.loads(own_config.read_text())
    assert cfg["ip"] == "192.168.1.50" and cfg["key"] == "aa" * 32 and cfg["enc_private"] == "bb" * 32
    own_config.write_text("")  # an empty file
    monkeypatch.setattr(D, "resolve", lambda host: "192.168.1.51")
    D.Device("octo-rec.local", "aa" * 32)._address()  # finds the recorder, writes nothing
    assert own_config.read_text() == ""


def test_a_lab_at_19_has_its_joins_made_parts(tmp_path):
    """Schema 20 (B35, 30 Oct): 18's audio joins become two conversations again, grouped as parts of one discussion
    (an undone one stays decided), and `joins` goes."""
    path = tmp_path / "v19.db"
    conn = DB.connect(path)
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('r1', 'device', 'x', 9000, 0)")
    for sid, a, b, status in (("s1", 0, 3700, "active"), ("s2", 1846, 3700, "superseded"), ("s3", 4000, 4500, "active"),
                              ("s4", 4600, 5000, "active")):
        conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at, status) "
                     "VALUES (?, 'r1', ?, ?, 1, 0, ?)", (sid, a, b, status))
    conn.execute("DROP TABLE parts")
    conn.execute("CREATE TABLE joins (recording_id TEXT, a_s REAL, b_s REAL, session_id TEXT, second_id TEXT, "
                 "created_at REAL, by TEXT, undone_at REAL, PRIMARY KEY (recording_id, a_s))")
    conn.execute("INSERT INTO joins VALUES ('r1', 1800, 1846, 's1', 's2', 5, 'lab', NULL), ('r1', 4500, 4600, 's3', 's4', 6, 'sam', 7)")  # before D9: the owner's name
    conn.execute("PRAGMA user_version=19")
    conn.close()
    conn = DB.connect(path)
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='joins'").fetchone()
    assert [tuple(r) for r in conn.execute("SELECT first_id, second_id, by, undone_at FROM parts ORDER BY first_id")] == \
        [("s1", "s2", "lab", None), ("s3", "s4", "owner", 7)]
    assert [tuple(r) for r in conn.execute("SELECT id, end_s, status FROM sessions WHERE id IN ('s1', 's2') ORDER BY id")] == \
        [("s1", 1800.0, "active"), ("s2", 3700.0, "active")]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == DB.SCHEMA_VERSION


def test_a_lab_at_20_keeps_no_name_in_what_is_stored(tmp_path):
    """Schema 21 (D9, 31 Oct): the first lab's kind, who split or grouped by hand and D10's note were the first owner's
    name ("sam" here). They become "first", "owner" and "Your voice", and the lab behaves as it did."""
    path = tmp_path / "v20.db"
    conn = DB.connect(path)
    conn.execute("""UPDATE app_config SET value='"sam"' WHERE key='lab_origin'""")
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('r1', 'device', 'x', 9000, 0)")
    conn.execute("INSERT INTO splits (recording_id, at_s, created_at, by) VALUES ('r1', 10, 0, 'sam'), ('r1', 20, 0, 'lab')")
    conn.execute("INSERT INTO parts (first_id, second_id, by, created_at) VALUES ('s1', 's2', 'sam', 0), ('s2', 's3', 'lab', 0)")
    notes = ("Sam's voice not heard (D10). Deleted with other raw audio after the retention period; restore with: octo keep s1",
             "Not sure you're in this one: kept to be safe.")
    for n, (sid, note) in enumerate(zip(("s1", "s2"), notes)):
        conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at, status, note) "
                     "VALUES (?, 'r1', ?, ?, 1, 0, 'discarded', ?)", (sid, n * 10, n * 10 + 1, note))
    conn.execute("PRAGMA user_version=20")
    conn.close()
    conn = DB.connect(path)
    assert LC.origin(conn) == LC.FIRST and LC.FLAGS["claude_code"][LC.origin(conn)]
    assert [r[0] for r in conn.execute("SELECT by FROM splits ORDER BY at_s")] == ["owner", "lab"]
    assert [r[0] for r in conn.execute("SELECT by FROM parts ORDER BY first_id")] == ["owner", "lab"]
    assert [r[0] for r in conn.execute("SELECT note FROM sessions ORDER BY id")] == [
        "Your voice not heard (D10). Deleted with other raw audio after the retention period; restore with: octo keep s1",
        notes[1]]
    new = DB.connect(tmp_path / "new.db")
    new.execute("""UPDATE app_config SET value='"product"' WHERE key='lab_origin'""")
    new.execute("PRAGMA user_version=20")
    new.close()
    assert LC.origin(DB.connect(tmp_path / "new.db")) == LC.PRODUCT


def test_a_lab_at_18_gets_the_plans_tables(tmp_path):
    """Schema 19 (B36): a lab one version behind gains `preparations` and `preparation_sources` once."""
    path = tmp_path / "v18.db"
    conn = DB.connect(path)
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at) VALUES ('r1', 'device', 'x', 1, 0)")
    conn.execute("DROP TABLE preparation_sources")
    conn.execute("DROP TABLE preparations")
    conn.execute("PRAGMA user_version=18")
    conn.close()
    conn = DB.connect(path)
    assert {r["name"] for r in conn.execute("PRAGMA table_info(preparations)")} >= {"person_id", "status", "plan", "sent"}
    assert {r["name"] for r in conn.execute("PRAGMA table_info(preparation_sources)")} == {"prep_id", "session_id"}
    assert conn.execute("SELECT count(*) FROM recordings").fetchone()[0] == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == DB.SCHEMA_VERSION
