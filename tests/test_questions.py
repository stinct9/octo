"""Others' questions and who spoke next (B4, octo/questions.py), on made-up lines."""

from octo import questions as Q

ME = {"A"}


def L(i, who, start, end, text):
    return {"id": i, "speaker": who, "start": start, "end": end, "text": text}


def test_jay_speaks_next():
    m = Q.moments([L(0, "B", 0, 3, "Can we talk about the date?"), L(1, "A", 3.5, 6, "Sure, go ahead.")], ME)
    assert m == [{"line": 0, "start": 0, "end": 3, "by": "B", "outcome": "you", "reply": 1, "after_s": 0.5}]


def test_someone_else_or_nobody():
    m = Q.moments([L(0, "B", 0, 3, "Who owns QA?"), L(1, "C", 3.2, 5, "I do."),
                   L(2, "B", 10, 12, "And the date?"), L(3, "A", 30, 32, "Let's see.")], ME)
    assert [(x["line"], x["outcome"]) for x in m] == [(0, "other"), (2, "nobody")]  # 18 s later isn't an answer


def test_questions_in_a_row_are_one_and_the_last_is_shown():
    m = Q.moments([L(0, "B", 0, 2, "Is it Friday?"), L(1, "B", 2, 4, "Or Monday? Which one?"), L(2, "A", 4.2, 6, "Friday.")], ME)
    assert len(m) == 1 and m[0]["line"] == 1 and m[0]["start"] == 0 and m[0]["outcome"] == "you"


def test_jays_own_questions_and_statements_dont_count():
    assert Q.moments([L(0, "A", 0, 2, "What do you think?"), L(1, "B", 2, 4, "It's fine.")], ME) == []


def test_talking_over_the_question_is_still_next():
    m = Q.moments([L(0, "B", 0, 5, "Shall we hold it, or move it?"), L(1, "A", 4.0, 6, "Hold it.")], ME)
    assert m[0]["outcome"] == "you" and m[0]["after_s"] == -1.0


def test_the_last_line_has_nobody_after_it():
    assert Q.moments([L(0, "B", 0, 2, "Any questions?")], ME)[0]["outcome"] == "nobody"


def test_the_check_by_ear_is_kept_against_what_the_app_said(tmp_path, monkeypatch):
    from octo.db import connect, rows
    c = connect(tmp_path / "t.db")
    c.execute("INSERT INTO recordings (id, source, path, started_at, duration_s, created_at) VALUES ('r', 'device', 'x', 1, 60, 0)")
    c.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES ('ses_x', 'r', 0, 60, 30, 0)")
    lines = [L(0, "B", 0, 3, "Can we talk about the date?"), L(1, "A", 20, 22, "Sorry, yes.")]
    monkeypatch.setattr(Q, "_lines", lambda conn, sid: (lines, ME, 1))
    import octo.sessions as SV
    monkeypatch.setattr(SV, "speakers", lambda conn, sid: [{"letter": "B", "name": "Meera"}, {"letter": "A", "name": "You"}])
    got = Q.to_check(c)["items"]
    assert [(i["outcome"], i["text"], i["by_name"], i["verdict"]) for i in got] == [("nobody", "Can we talk about the date?", "Meera", None)]
    assert Q.save_check(c, "ses_x", 0.0, "answered") == {"by_app": {"nobody": {"answered": 1}}, "checked": 1}
    assert Q.to_check(c)["items"][0]["verdict"] == "answered"
    Q.save_check(c, "ses_x", 0.0, "unanswered")  # changed his mind: one answer, the latest
    assert rows(c, "SELECT app, verdict, text FROM question_checks") == [{"app": "nobody", "verdict": "unanswered", "text": "Can we talk about the date?"}]
    assert Q.save_check(c, "ses_x", 0.0, None)["checked"] == 0
    import pytest
    with pytest.raises(ValueError):
        Q.save_check(c, "ses_x", 0.0, "maybe")
    with pytest.raises(KeyError):
        Q.save_check(c, "ses_x", 9.0, "answered")
