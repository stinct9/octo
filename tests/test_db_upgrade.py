"""Real old databases brought forward (5 Nov review, risk 3): the demo lab as each schema's own code built it, in
tests/fixtures/schemas/demo-<N>.sql.gz, opened by today's db.connect. Each must end with the tables, columns and
indexes a new database has, keep its rows, and leave a snapshot of itself first; a database from a newer Octo is
refused untouched.

A fixture for a new schema, from a commit that has it (here: in a scratch worktree of that commit):
    python -I -c "import sys; sys.path.insert(0, '.'); from tests.demo_lab import build; import sqlite3, gzip; \\
      c = sqlite3.connect(build(now=1793000000.0)); open('demo.sql', 'w').write('\\n'.join(c.iterdump()))"
    gzip -9 demo.sql   # then tests/fixtures/schemas/demo-<N>.sql.gz
"""

import gzip
import re
import sqlite3
from pathlib import Path

import pytest

from octo import db as DB

FIXTURES = sorted(Path(__file__).parent.joinpath("fixtures", "schemas").glob("demo-*.sql.gz"))


def shape(conn) -> dict:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
    return {"columns": {t: {r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')} for t in tables},
            "indexes": {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL")}}


def load(fixture: Path, path: Path) -> int:
    version = int(re.search(r"demo-(\d+)", fixture.name)[1])
    conn = sqlite3.connect(path)
    conn.executescript(gzip.decompress(fixture.read_bytes()).decode())
    conn.execute(f"PRAGMA user_version = {version}")
    conn.close()
    return version


def test_there_are_old_schemas_to_bring_forward():
    assert len(FIXTURES) >= 2


@pytest.mark.parametrize("fixture", FIXTURES, ids=[f.name for f in FIXTURES])
def test_an_old_lab_comes_forward_whole(tmp_path, fixture):
    path = tmp_path / "data" / "octo.db"
    path.parent.mkdir()
    version = load(fixture, path)
    before = sqlite3.connect(path)
    counts = {t: before.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in shape(before)["columns"]}
    before.close()

    conn = DB.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == DB.SCHEMA_VERSION
    new = DB.connect(tmp_path / "new.db")
    assert shape(conn) == shape(new)
    for t, n in counts.items():
        if t in shape(conn)["columns"]:
            assert conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] == n, t
    snap = path.parent / "backups" / f"pre-schema-{version}.db"
    if version < DB.SCHEMA_VERSION:
        old = sqlite3.connect(snap)
        assert old.execute("PRAGMA user_version").fetchone()[0] == version
        assert old.execute("SELECT count(*) FROM sessions").fetchone()[0] == counts["sessions"]
        old.close()
        assert snap.stat().st_mode & 0o777 == 0o600
    else:
        assert not snap.exists()


def test_a_database_from_a_newer_octo_is_refused_untouched(tmp_path):
    path = tmp_path / "octo.db"
    load(FIXTURES[-1], path)
    conn = sqlite3.connect(path)
    conn.execute(f"PRAGMA user_version = {DB.SCHEMA_VERSION + 1}")
    conn.close()
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="from a newer Octo"):
        DB.connect(path)
    assert path.read_bytes() == before and not (tmp_path / "backups").exists()


def test_a_snapshot_made_after_someone_else_migrated_is_dropped(tmp_path):
    path = tmp_path / "octo.db"
    conn = DB.connect(path)  # at SCHEMA_VERSION already: what a second connection finds once the first has migrated
    assert DB.snapshot(conn, path, DB.SCHEMA_VERSION - 1) is None
    assert list((tmp_path / "backups").iterdir()) == []
