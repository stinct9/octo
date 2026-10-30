"""Practice drills (B18, octo/drills.py) on a made-up lab: a take is heard, measured against the earlier takes (the
pitch memos first), and never reaches the list, search, the coach or Progress's counts (K4)."""

import json
import time
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octo import HOME
from octo import audio as A
from octo import coach as CO
from octo import drills as DR
from octo import focus as FO
from octo import privacy as PV
from octo.api import make_router
from octo.db import connect, row
from tests.test_search import L, _conv

UPLOAD = {"x-octo-upload": "1"}


def segs(text: str, t: float = 0.0, step: float = 0.4) -> list[dict]:
    """Qwen's segments for a take said evenly, a word every `step` seconds."""
    ws = [{"w": w, "start": round(t + i * step, 2), "end": round(t + i * step + step * 0.8, 2)} for i, w in enumerate(text.split())]
    return [{"id": 0, "start": ws[0]["start"], "end": ws[-1]["end"], "text": text, "words": ws}]


SAID = ("The answer is yes we ship on Friday. Um the reason is, you know, the tests pass and I think the risk is "
        "small. We, we checked it twice. ") * 4  # 29 words a round: a filler "um", "you know", a hedge, a restart


@pytest.fixture
def lab(tmp_path, monkeypatch):
    db = lambda: connect(tmp_path / "t.db")
    conn = db()
    conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES ('me', 'Jay', 1, 0), ('ravi', 'Ravi', 0, 0)")
    _conv(conn, "ses_talk", time.time() - 86400, [L(0, "A", 0, "How is the release going?"), L(1, "B", 4, "Fine, thanks.")],
          {"A": "me", "B": "ravi"})
    _conv(conn, "ses_memo", 1000, [L(0, "A", 0, "So um the pitch is that, you know, we save time. " * 10)], {"A": "me"})
    conn.execute("UPDATE sessions SET in_gate=0 WHERE id='ses_memo'")  # J6: a pitch memo, out of the counts
    monkeypatch.setattr(DR, "transcribe", lambda path: segs(SAID))
    app = FastAPI()
    app.include_router(make_router(db, processor=SimpleNamespace(kick=lambda: None)))
    return SimpleNamespace(conn=conn, db=db, client=TestClient(app), tmp=tmp_path)


def wav(tmp_path, s: float = 2.0):
    p = tmp_path / "take.wav"
    A.write(p, np.zeros(int(16000 * s), dtype=np.float32))
    return p


def take(lab, text: str = SAID, at: float | None = None) -> str:
    """A heard take, straight into the table."""
    did = f"drl_{time.time_ns()}"
    lab.conn.execute("INSERT INTO drills (id, prompt, path, duration_s, created_at, status, transcript) "
                     "VALUES (?, 'p', 'audio/lab/drills/x.flac', 90, ?, 'done', ?)", (did, at or time.time(), json.dumps(segs(text))))
    return did


def test_a_take_is_stored_heard_and_measured_on_the_six(lab):
    did = DR.add(lab.conn, wav(lab.tmp), DR.today(lab.conn))
    d = row(lab.conn, "SELECT * FROM drills WHERE id=?", did)
    assert d["status"] == "queued" and d["path"].startswith("audio/lab/drills/") and round(d["duration_s"]) == 2
    assert d["prompt"] == "Explain one thing from your work in 90 s, answer first"  # C7's, the only one to start
    DR.hear(lab.conn, did)
    got = DR.detail(lab.conn, did)
    assert got["status"] == "done" and got["words"] == 116
    by = {r["measure"]: r for r in got["rows"]}
    assert list(by) == ["fillers", "pauses", "restarts", "hedges", "pace", "longest"]
    assert by["fillers"]["value"] == 6.9  # "um" and "you know" each round: 8 in 116 words
    assert by["restarts"]["value"] == 3.45 and by["hedges"]["value"] == 3.45  # "We, we" · "I think"
    assert by["pace"]["value"] == 150.26  # a word every 0.4 s, while speaking
    assert by["fillers"]["last"] is not None  # the pitch memo is the line's start
    assert by["fillers"]["state"] == "no_usual"  # one earlier point: no usual yet, and no verdict
    (HOME / d["path"]).unlink()


def test_against_the_earlier_takes_in_neutral_words_and_the_focus_leads(lab):
    for i in range(5):
        take(lab, at=time.time() - 1000 + i)
    fewer = take(lab, SAID.replace("Um the", "The").replace("you know, ", ""))
    FO.start(lab.conn, "Fewer restarts", measure="restarts")
    got = DR.detail(lab.conn, fewer)
    assert got["lead"] == "restarts" and got["rows"][0]["measure"] == "restarts"
    by = {r["measure"]: r for r in got["rows"]}
    assert by["fillers"]["state"] == "low" and by["fillers"]["value"] == 0.0 and by["fillers"]["conversations"] == 6
    assert by["pauses"]["state"] == "about"
    assert set(r["state"] for r in got["rows"]) <= {"low", "about", "high", "no_usual", "short", "unmeasured"}


def test_a_drill_is_never_in_the_list_search_the_coach_or_progresss_counts(lab):
    before = {"list": lab.client.get("/api/octo/sessions").json(),
              "progress": {k: v for k, v in lab.client.get("/api/octo/progress").json().items() if k != "drills"},
              "coach": json.dumps(CO.material(lab.conn, now=1e9), sort_keys=True, default=str)}
    take(lab, "Zanzibar is the codeword I said only in a drill")
    assert lab.client.get("/api/octo/sessions").json() == before["list"]
    hits = lab.client.get("/api/octo/search", params={"q": "zanzibar"}).json()
    assert "Zanzibar" not in json.dumps(hits)
    p = lab.client.get("/api/octo/progress").json()
    assert {k: v for k, v in p.items() if k != "drills"} == before["progress"]
    assert json.dumps(CO.material(lab.conn, now=1e9), sort_keys=True, default=str) == before["coach"]
    # Its own line: the pitch memo first, then the take; the conversation with Ravi isn't on it.
    assert p["drills"]["memos"] == 1 and p["drills"]["takes"] == 1
    assert [x["memo"] for x in p["drills"]["points"]] == [True, False]


def test_upload_replay_delete_and_the_prompt_list(lab):
    c = lab.client
    page = c.get("/api/octo/drills").json()
    assert page["prompts"] == DR.PROMPTS_DEFAULT and page["today"] == DR.PROMPTS_DEFAULT[0] and page["takes"] == []
    assert c.post("/api/octo/drills", params={"prompt": "x", "name": "t.wav"}, content=b"x").status_code == 403  # no upload header
    did = c.post("/api/octo/drills", params={"prompt": page["today"], "name": "t.wav"}, headers=UPLOAD,
                 content=wav(lab.tmp).read_bytes()).json()["id"]
    assert c.get(f"/api/octo/drills/{did}").json()["status"] == "queued"
    assert c.get(f"/api/octo/drills/{did}/audio").status_code == 200
    assert c.post(f"/api/octo/drills/{did}/delete", json={"confirm": "yes"}).status_code == 422
    path = HOME / row(lab.conn, "SELECT path FROM drills WHERE id=?", did)["path"]
    assert c.post(f"/api/octo/drills/{did}/delete", json={"confirm": "delete"}).json() == {"deleted": did}
    assert not path.exists() and c.get(f"/api/octo/drills/{did}").status_code == 404
    got = c.post("/api/octo/drills/prompts", json={"phrase": "Say no to a request,  kindly", "on": True}).json()
    assert got["prompts"] == [DR.PROMPTS_DEFAULT[0], "Say no to a request, kindly"]
    assert c.post("/api/octo/drills/prompts", json={"phrase": "x", "on": True}).status_code == 422
    assert c.post("/api/octo/drills/prompts", json={"phrase": DR.PROMPTS_DEFAULT[0], "on": False}).json()["prompts"] == \
        ["Say no to a request, kindly"]


def test_audio_goes_at_60_days_and_the_numbers_stay(lab):
    did = DR.add(lab.conn, wav(lab.tmp), "p")
    DR.hear(lab.conn, did)
    path = HOME / row(lab.conn, "SELECT path FROM drills WHERE id=?", did)["path"]
    PV.drop_old_audio(lab.conn, now=time.time() + 61 * 86400)
    assert not path.exists()
    assert lab.client.get(f"/api/octo/drills/{did}/audio").status_code == 410
    got = lab.client.get(f"/api/octo/drills/{did}").json()
    assert got["audio"] is False and got["words"] == 116


def test_a_take_the_worker_could_not_hear_says_why(lab, monkeypatch):
    did = take(lab)
    lab.conn.execute("UPDATE drills SET status='queued' WHERE id=?", (did,))
    seen = []
    monkeypatch.setattr(DR.subprocess, "run", lambda cmd, **k: seen.append(cmd) or SimpleNamespace(stderr="Boom: no model\n"))
    assert DR.run_queued(lab.conn, log=lambda m: None) == 1
    assert seen[0][-2:] == ["octo.drills", did]
    d = row(lab.conn, "SELECT status, error FROM drills WHERE id=?", did)
    assert d == {"status": "failed", "error": "Boom: no model"}
    monkeypatch.setattr(DR, "transcribe", lambda p: 1 / 0)
    lab.conn.execute("UPDATE drills SET status='queued' WHERE id=?", (did,))
    DR.hear(lab.conn, did)
    assert row(lab.conn, "SELECT error FROM drills WHERE id=?", did)["error"].startswith("ZeroDivisionError")


def test_progress_line_starts_with_the_pitch_memos(lab):
    for i in range(7):
        take(lab, at=time.time() - 100 + i)
    p = DR.progress(lab.conn)
    assert p["memos"] == 1 and p["takes"] == 7 and p["lately_n"] == 5
    fil = next(r for r in p["rows"] if r["measure"] == "fillers")
    assert fil["start"] == 18.18 and fil["latest"] == fil["lately"] == 6.9  # the memo: "um" and "you know" in 11 words


CLAUDE = {"kind": "claude-code", "model": "claude-opus-5-5", "effort": "high"}


def asker(answer: dict | Exception, seen: list):
    def ask(model, effort, system, prompt, schema):
        seen.append({"model": model, "system": system, "prompt": prompt})
        if isinstance(answer, Exception):
            raise answer
        return answer, {"output_tokens": 40}
    return ask


def test_where_the_point_came_one_call_its_quote_checked_and_timed(lab, monkeypatch):
    from octo import pipeline as P
    from octo import settings as ST
    monkeypatch.setattr(ST, "reader", lambda conn: CLAUDE)
    did = take(lab)
    seen = []
    ask = asker({"point_line": 0, "point_quote": "the reason is, you know, the tests pass", "build_up_right": False,
                 "build_up_why": "The answer came first.", "short": "We ship Friday: the tests pass."}, seen)
    before = P.claude_calls_since(lab.conn, 0)
    assert DR.find_points(lab.conn, log=lambda m: None, ask=ask) == 1
    assert len(seen) == 1 and "[0] 0:00 Jay: The answer is yes" in seen[0]["prompt"] and '"p"' in seen[0]["system"]
    p = DR.detail(lab.conn, did)["point"]
    assert p["state"] == "done" and p["short_words"] == 6 and p["build_up_right"] is False
    assert p["quote"] == {"text": "the reason is, you know, the tests pass", "line": 0, "at": 3.6, "measured": True}  # the 10th word, not the line's start
    assert P.claude_calls_since(lab.conn, 0) == before + 1  # within the 60 a day
    assert DR.find_points(lab.conn, log=lambda m: None, ask=ask) == 0 and len(seen) == 1  # asked once

    made_up = take(lab)
    DR.find_points(lab.conn, log=lambda m: None, ask=asker({"point_line": 0, "point_quote": "we never said this",
                                                             "build_up_right": True, "build_up_why": "", "short": "x"}, seen))
    p = DR.detail(lab.conn, made_up)["point"]
    assert p["quote"] is None and p["dropped"] and p["short"] == "x"


def test_no_call_for_a_short_take_and_a_usage_limit_waits_for_its_reset(lab, monkeypatch):
    from octo import settings as ST
    monkeypatch.setattr(ST, "reader", lambda conn: CLAUDE)
    short = take(lab, "Yes we ship on Friday.")
    seen = []
    DR.find_points(lab.conn, log=lambda m: None, ask=asker({}, seen))
    assert seen == [] and DR.detail(lab.conn, short)["point"] == {"state": "skipped", "why": "under 60 words"}

    did = take(lab)
    now = time.time()
    DR.find_points(lab.conn, log=lambda m: None, ask=asker(RuntimeError("You've hit your usage limit"), seen), now=now)
    assert DR.detail(lab.conn, did)["point"] == {"state": "waiting"} and len(seen) == 1
    ok = asker({"point_line": 0, "point_quote": "The answer is yes", "build_up_right": False, "build_up_why": "", "short": "Yes."}, seen)
    DR.find_points(lab.conn, log=lambda m: None, ask=ok, now=now + 60)
    assert len(seen) == 1  # not before it resets (an hour, with no time in the message)
    DR.find_points(lab.conn, log=lambda m: None, ask=ok, now=now + 3700)
    assert len(seen) == 2 and DR.detail(lab.conn, did)["point"]["quote"]["at"] == 0.0

    failed = take(lab)
    DR.find_points(lab.conn, log=lambda m: None, ask=asker(RuntimeError("Boom"), seen), now=now + 3700)
    DR.find_points(lab.conn, log=lambda m: None, ask=ok, now=now + 9999)
    assert len(seen) == 3 and DR.detail(lab.conn, failed)["point"] == {"state": "failed", "error": "Boom"}

    monkeypatch.setattr(ST, "reader", lambda conn: None)  # no model chosen yet: nothing asked, the page says so
    lab.conn.execute("UPDATE drills SET point=NULL, point_error=NULL")
    assert DR.find_points(lab.conn, log=lambda m: None, ask=ok) == 0
    assert DR.detail(lab.conn, did)["point"] == {"state": "off"}
