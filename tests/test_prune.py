"""Superseded stage runs lose their output (octo/prune.py, architecture review 4) and the backups are compact and
fewer (octo/backup.py): the pages and counts stay as they were, and a backup still restores."""

import json
import shutil
import time
from datetime import date, timedelta

import pytest

from octo import backup as B
from octo import eta as E
from octo import pipeline as P
from octo import prune as PR
from octo import review as RV
from octo import sessions as S
from octo.db import connect, row, rows

DAY = 86400


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """The demo lab, each conversation's listening steps run once before, 30 days ago: a copy of each current run,
    superseded by it. Its first conversation's old diarize run is named by a correction, and its second's old
    transcribe run is what its current speakers run was built on (a review kept on the earlier transcript)."""
    from tests import demo_lab as DL
    monkeypatch.setattr(DL, "ROOT", tmp_path)
    monkeypatch.setattr(DL, "DEMO", tmp_path / "data" / "demo")
    monkeypatch.setattr(DL, "DB", tmp_path / "data" / "demo" / "octo.db")
    DL.build()
    conn = connect(DL.DB)
    old_at = time.time() - 30 * DAY
    conn.execute("UPDATE stage_runs SET queued_at=100")
    third = rows(conn, "SELECT id FROM sessions WHERE status='active' ORDER BY id")[2]["id"]
    cur = rows(conn, f"SELECT * FROM stage_runs WHERE is_current=1 AND stage IN ({','.join('?' * len(PR.STAGES))})", *PR.STAGES)
    copied = {r["id"] for r in cur}
    for r in cur:
        up = json.dumps([f"old_{u}" if u in copied else u for u in json.loads(r["upstream"])])  # built on the old ones
        out = json.loads(r["output"])
        if r["stage"] == "transcribe" and r["target"] == third:
            out["merged"] = {"calls": 2}  # a Hindi merge: the ETA still sees it as one
        conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, upstream, params, output, "
                     "queued_at, started_at, finished_at) VALUES (?, ?, ?, 'old', 'done', 0, ?, '{}', ?, 50, ?, ?)",
                     ("old_" + r["id"], r["stage"], r["target"], up, json.dumps(out), old_at - 60, old_at))
    sids = [s["id"] for s in rows(conn, "SELECT id FROM sessions WHERE status='active' ORDER BY id")]
    dz = row(conn, "SELECT id FROM stage_runs WHERE target=? AND stage='diarize' AND is_current=0", sids[0])["id"]
    conn.execute("INSERT INTO corrections (id, session_id, diarize_run, kind, data, created_at) VALUES ('co_t', ?, ?, 'line', "
                 "'{\"line\": 999, \"to\": \"Z\"}', 0)", (sids[0], dz))
    tr = row(conn, "SELECT id FROM stage_runs WHERE target=? AND stage='transcribe' AND is_current=0", sids[1])["id"]
    sp = row(conn, "SELECT id, upstream FROM stage_runs WHERE target=? AND stage='speakers' AND is_current=1", sids[1])
    conn.execute("UPDATE stage_runs SET upstream=? WHERE id=?", (json.dumps([tr]), sp["id"]))
    return conn, dz, tr


def _pages(conn) -> str:
    """What the owner sees: every conversation's list row and page, Progress's counts, the open steps, the ETAs' fit."""
    out = {"progress": RV.checks(conn), "tasks": S.open_tasks(conn), "rates": E.rates(conn),
           "calls": P.claude_calls_since(conn, 0)}
    for s in rows(conn, "SELECT s.*, r.source, r.original, r.started_at, r.path, r.audio_deleted_at, r.created_at AS added "
                        "FROM sessions s JOIN recordings r ON r.id = s.recording_id WHERE s.status='active' ORDER BY s.id"):
        out[s["id"]] = [S.summary(conn, s), S.detail(conn, s)]
    return json.dumps(out, sort_keys=True, default=str)


def test_a_pruned_lab_shows_the_same_pages_and_counts(lab):
    conn, kept_dz, kept_tr = lab
    P.plan(conn)  # what the demo lab queues anyway
    before = _pages(conn)
    old = {r["id"] for r in rows(conn, "SELECT id FROM stage_runs WHERE id LIKE 'old_%'")}
    named = {kept_dz, kept_tr, *json.loads(row(conn, "SELECT upstream FROM stage_runs WHERE id=?", kept_dz)["upstream"])}
    assert len(named) == 4  # the named diarize run, what it was built on (old too), and the speakers run's transcript
    assert PR.prune(conn) == len(old) - len(named)
    assert _pages(conn) == before
    cleared = rows(conn, "SELECT id, output FROM stage_runs WHERE output LIKE '{\"pruned\": true%'")
    assert {c["id"] for c in cleared} == old - named
    assert any(c["output"] == PR.MERGED for c in cleared)
    assert PR.prune(conn) == 0  # nothing twice
    assert P.plan(conn) == []  # nothing re-runs: the planner reads only current runs


def test_recent_current_or_named_runs_are_never_cleared(lab):
    conn, _, _ = lab
    conn.execute("UPDATE stage_runs SET finished_at=? WHERE is_current=0", (time.time() - 3 * DAY,))
    assert PR.prune(conn) == 0  # within the grace
    conn.execute("UPDATE stage_runs SET finished_at=? WHERE is_current=0", (time.time() - 30 * DAY,))
    sid = rows(conn, "SELECT id FROM sessions WHERE status='active' ORDER BY id")[2]["id"]
    nb = row(conn, "SELECT id FROM stage_runs WHERE target=? AND stage='numbers' AND is_current=0", sid)["id"]
    conn.execute("UPDATE stage_runs SET status='failed' WHERE id=?", (nb,))  # a failed run holds back a retry
    ins = row(conn, "SELECT id FROM stage_runs WHERE target=? AND stage='insight' AND is_current=1", sid)["id"]
    conn.execute("UPDATE stage_runs SET is_current=0 WHERE id=?", (ins,))  # an old review the owner rated, on old runs
    conn.execute("UPDATE stage_runs SET upstream=? WHERE id=?",
                 (json.dumps([f"old_{r['id']}" for r in rows(conn, "SELECT id FROM stage_runs WHERE target=? AND "
                                                                 "stage='identify' AND is_current=1", sid)]), ins))
    conn.execute("INSERT INTO feedback (insight_run, item, session_id, verdict, updated_at) VALUES (?, 'p1', ?, 'right', 0)",
                 (ins, sid))
    PR.prune(conn)
    left = {r["stage"] for r in rows(conn, "SELECT stage FROM stage_runs WHERE target=? AND is_current=0 AND "
                                           "output NOT LIKE '{\"pruned\": true%'", sid)}
    assert {"identify", "diarize", "transcribe", "speakers", "insight"} <= left  # the rated review's, all the way up
    assert row(conn, "SELECT output FROM stage_runs WHERE id=?", nb)["output"] != PR.PRUNED


def test_backups_are_compact_and_restore(lab, tmp_path):
    conn, _, _ = lab
    PR.prune(conn)
    before = _pages(conn)
    dest = tmp_path / "backups"
    made = B.daily(conn, dest, date.today())
    assert made and made.stat().st_mode & 0o777 == 0o600
    pages = conn.execute("PRAGMA page_count").fetchone()[0] - conn.execute("PRAGMA freelist_count").fetchone()[0]
    copy = connect(made)
    assert copy.execute("PRAGMA freelist_count").fetchone()[0] == 0
    assert copy.execute("PRAGMA page_count").fetchone()[0] <= pages
    assert copy.execute("PRAGMA user_version").fetchone()[0] == conn.execute("PRAGMA user_version").fetchone()[0]
    copy.close()
    restored = tmp_path / "restored.db"  # restoring is putting the copy in octo.db's place
    shutil.copy(made, restored)
    assert _pages(connect(restored)) == before


def test_seven_daily_backups_and_a_sunday_until_14_days(tmp_path):
    conn = connect(tmp_path / "live.db")
    conn.execute("INSERT INTO people (id, name, created_at) VALUES ('g', 'Nisha', 0)")
    dest = tmp_path / "backups"
    start = date(2026, 10, 1)  # a Thursday
    for i in range(30):
        assert B.daily(conn, dest, start + timedelta(days=i))
    assert B.daily(conn, dest, start + timedelta(days=29)) is None  # already done today
    kept = [b.name for b in B.backups(dest)]
    # 30 Oct is today: 24-30 Oct daily, and Sunday 18 Oct (12 days old); Sunday 11 Oct is 19 days old
    assert kept == ["octo-2026-10-18.db"] + [f"octo-2026-10-{d}.db" for d in range(24, 31)]
    assert connect(B.backups(dest)[-1]).execute("SELECT name FROM people").fetchone()[0] == "Nisha"
    # The Mac off for a fortnight: nothing older than 14 days is kept, however few are left
    assert B.daily(conn, dest, start + timedelta(days=44))
    assert [b.name for b in B.backups(dest)] == ["octo-2026-11-14.db"]
