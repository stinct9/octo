"""Ask your conversations (B24, octo/ask.py) on a made-up lab: picked on the Mac, quotes checked, the day's count,
and delete and forget reaching the answers. The model is never called."""

import json
from datetime import datetime

import pytest

from octo import ask as AK
from octo import corrections as C
from octo import pipeline as P
from octo import voice as V
from octo.privacy import delete_session
from tests.test_search import L, _conv

NOW = datetime(2026, 10, 27, 12, 0).timestamp()
DAY = 86400


@pytest.fixture(autouse=True)
def model(monkeypatch):
    monkeypatch.setattr("octo.settings.reader", lambda conn: {"kind": "claude-code", "model": "m", "effort": "high"})
    monkeypatch.setattr("octo.claude_code.claude_bin", lambda: "claude")
    monkeypatch.setattr("octo.privacy._silence", lambda *a: None)
    AK._LIVE.clear()


def _lab(tmp_path):
    from octo.db import connect
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO people (id, name, is_self, created_at) VALUES ('me', 'Jay', 1, 0), ('ravi', 'Ravi Kumar', 0, 0), "
                 "('priya', 'Priya', 0, 0)")
    _conv(conn, "ses_ravi", NOW - 3 * DAY, [L(0, "A", 0, "How are things?"), L(1, "B", 4, "Honestly my plate is too full."),
                                            L(2, "A", 9, "Let's look at it."), L(3, "B", 12, "The deadlines keep moving.")],
          {"A": "me", "B": "ravi"})
    _conv(conn, "ses_priya", NOW - 40 * DAY, [L(0, "A", 0, "I promise to send the deck by Friday."),
                                              L(1, "B", 4, "Thanks, the deck matters.")], {"A": "me", "B": "priya"})
    _conv(conn, "ses_tv", NOW - DAY, [L(0, "A", 0, "Ravi Kumar on the deck, says the TV")], {}, status="set_aside")
    conn.execute("INSERT INTO drills (id, prompt, path, duration_s, created_at, status, transcript) "
                 "VALUES ('d1', 'p', 'x', 30, ?, 'done', ?)", (NOW, json.dumps([{"text": "my deck pitch", "start": 0, "end": 3}])))
    return conn


def answer(*claims, limits=""):
    """A model's answer: each claim (text, [(conversation, line, quote)])."""
    def ask(model, effort, system, prompt, schema):
        ask.prompt = prompt
        return {"claims": [{"text": t, "quotes": [{"conversation": c, "line": n, "text": q} for c, n, q in qs]} for t, qs in claims],
                "not_found": "", "limits": limits}, {"duration_ms": 1000, "input_tokens": 10, "output_tokens": 5}
    return ask


def test_picks_by_person_and_date_and_sends_only_those(tmp_path):
    conn = _lab(tmp_path)
    got = AK.start(conn, "What has Ravi said about his workload this month?", NOW)
    sent = got["sent"]
    assert sent["people"] == ["Ravi Kumar"] and sent["dates"] == "this month"
    assert [c["session_id"] for c in sent["conversations"]] == ["ses_ravi"] and sent["lines"] == 4
    ask = answer(("Ravi said his plate is too full.", [("c1", 1, "my plate is too full")]))
    out = AK.run(conn, got["id"], ask=ask)
    assert "plate is too full" in ask.prompt and "deck" not in ask.prompt  # Priya's conversation, the TV, the drill: absent
    q = out["answer"]["claims"][0]["quotes"][0]
    assert (q["session_id"], q["line"], q["who"], q["start"]) == ("ses_ravi", 1, "Ravi Kumar", 4)


def test_words_pick_when_nothing_else_does_and_left_out_stays_out(tmp_path):
    conn = _lab(tmp_path)
    got = AK.start(conn, "When did I last promise something?", NOW)
    assert [c["session_id"] for c in got["sent"]["conversations"]] == ["ses_priya"]
    C.add(conn, "ses_priya", "dz_ses_priya", "exclude", {"letter": "A"})  # the owner left their own lines out
    AK._LIVE.clear()
    conn.execute("UPDATE asks SET status='done'")
    got = AK.start(conn, "When did I last promise something?", NOW)
    assert got["status"] == "done" and got["sent"]["conversations"] == []  # nothing picked: no call
    assert "nothing was sent" in got["answer"]["not_found"]


def test_the_cap_holds(tmp_path, monkeypatch):
    conn = _lab(tmp_path)
    monkeypatch.setattr(AK, "MAX_LINES", 3)
    got = AK.start(conn, "What did Ravi say this month?", NOW)
    assert got["sent"]["lines"] <= 3


def test_quotes_are_checked_against_what_was_sent(tmp_path):
    conn = _lab(tmp_path)
    got = AK.start(conn, "What has Ravi said this month?", NOW)
    out = AK.run(conn, got["id"], ask=answer(
        ("His plate is full.", [("c1", 1, "my plate ... full"), ("c1", 3, "deadlines never move")]),  # the second is invented
        ("He is burnt out.", [("c2", 0, "I promise to send the deck")])))  # a conversation that wasn't sent
    a = out["answer"]
    assert [len(c["quotes"]) for c in a["claims"]] == [1, 0] and a["dropped"] == 2


def test_counts_in_the_days_calls_and_a_limit_holds_the_pipeline(tmp_path):
    conn = _lab(tmp_path)
    before = P.claude_calls_since(conn, 0)
    got = AK.start(conn, "What has Ravi said this month?", NOW)
    with pytest.raises(AK.AskError):  # one at a time
        AK.start(conn, "And Priya?", NOW)
    AK.run(conn, got["id"], ask=answer())
    assert P.claude_calls_since(conn, 0) == before + 1

    def limited(*a):
        raise RuntimeError("Claude Code: Claude AI usage limit reached|%d" % (NOW + 3600))
    got = AK.start(conn, "What has Ravi said this month?", NOW)
    assert AK.run(conn, got["id"], ask=limited)["status"] == "failed"
    conn.execute("UPDATE asks SET finished_at=? WHERE status='failed'", (NOW,))
    assert P.claude_blocked_until(conn, NOW) is not None
    assert "usage limit" in AK.why_not(conn, NOW)


def test_kept_only_when_kept_then_delete_and_forget_reach_it(tmp_path):
    conn = _lab(tmp_path)
    got = AK.start(conn, "What has Ravi said this month?", NOW)
    AK.run(conn, got["id"], ask=answer(("Full plate.", [("c1", 1, "my plate is too full")])))
    assert conn.execute("SELECT question, answer FROM asks").fetchone()[:] == (None, None)  # nothing on disk yet
    kept = AK.keep(conn, got["id"])
    assert kept["question"].startswith("What has Ravi") and AK.summary(conn, NOW)["kept"][0]["id"] == got["id"]
    V.forget(conn, "Ravi Kumar")
    assert AK.summary(conn, NOW)["kept"] == [] and conn.execute("SELECT question FROM asks").fetchone()[0] is None

    got = AK.start(conn, "When did I last promise Priya something?", NOW)
    AK.run(conn, got["id"], ask=answer())
    delete_session(conn, "ses_priya")  # not kept: gone from memory too
    with pytest.raises(AK.AskError):
        AK.get(conn, got["id"])
    assert P.claude_calls_since(conn, 0) == 2  # the rows stay, without words, for the day's count


def test_an_answer_not_kept_is_gone_after_a_while(tmp_path):
    conn = _lab(tmp_path)
    got = AK.start(conn, "What has Ravi said this month?", NOW)
    AK.run(conn, got["id"], ask=answer())
    assert AK.summary(conn, NOW)["live"][0]["id"] == got["id"]
    assert AK.summary(conn, NOW + AK.LIVE_S + 1)["live"] == []


def test_dates_named():
    assert AK.dates_named("anything last week?", NOW)[2] == "last week"
    a, b, said = AK.dates_named("what did we agree in October", NOW)
    assert said == "in october" and datetime.fromtimestamp(a).day == 1 and datetime.fromtimestamp(b).month == 11
    a, _, _ = AK.dates_named("since december", NOW)
    assert datetime.fromtimestamp(a).year == 2025
    assert AK.dates_named("the past 3 days", NOW)[2] == "past 3 days"
    assert AK.dates_named("what about Ravi", NOW) is None
