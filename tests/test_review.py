"""Rating insights, 'reviewed', and the counts on Progress."""

from octo import review as R

OUT = {"skipped": None, "beneath": [{"question": "q", "quotes": []}],
       "conduct": [{"principle": 8, "try_next_time": "Ask first."}, {"principle": 9, "try_next_time": None}]}


def test_what_needs_rating():
    assert [(i["id"], i["required"]) for i in R.items(OUT)] == [("p8", True), ("t8", True), ("p9", True), ("b0", False)]


def test_reviewed_needs_both_answers_on_every_required_item():
    fb = {"p8": {"verdict": "right", "novelty": "new", "useful": None},
          "t8": {"verdict": "partly", "novelty": None, "useful": None},  # half answered
          "p9": {"verdict": "wrong", "novelty": "knew", "useful": None}}
    assert R.left_to_rate(OUT, fb) == 1
    fb["t8"]["novelty"] = "knew"
    assert R.left_to_rate(OUT, fb) == 0  # the question (b0) is optional


def _seed(conn, sid, n_rated_new=0, acted=0, wrong=0):
    """A conversation whose insights ran, with feedback rows."""
    import json
    conn.execute("INSERT INTO stage_runs (id, stage, target, input_hash, status, is_current, output, queued_at) "
                 "VALUES (?, 'insight', ?, 'h', 'done', 1, ?, 0)", (f"in_{sid}", sid, json.dumps(OUT)))
    for i, item in enumerate(["p8", "t8", "p9"]):
        conn.execute("INSERT INTO feedback (insight_run, item, session_id, verdict, novelty, acted, updated_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, 0)", (f"in_{sid}", item, sid, "wrong" if i >= 3 - wrong else "right",
                                                       "new" if i < n_rated_new else "knew", int(i < acted)))
    assert R.mark_reviewed_if_done(conn, sid, f"in_{sid}")


def test_the_gate(tmp_path):
    from octo.db import connect
    conn = connect(tmp_path / "g.db")
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at, started_at) VALUES ('r', 'phone', 'x', 1, 0, 1790000000)")
    sids = [f"ses_2026100{i}_0000000{i}" for i in range(4)]
    for i, sid in enumerate(sids):
        conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES (?, 'r', ?, ?, 1, 0)",
                     (sid, i * 10, i * 10 + 5))
    _seed(conn, sids[0], n_rated_new=1, acted=1, wrong=1)
    _seed(conn, sids[1])
    _seed(conn, sids[2])
    ps = [{"n": 8, "title": "Stay calm"}, {"n": 1, "title": "Work"}]
    g = R.progress(conn, ps)
    counts = g["counts"]
    assert counts["checks"]["reviewed"] == {"value": 3, "provisional": 0}
    assert counts["checks"]["new_share"] == {"value": 1, "of": 3}
    assert counts["checks"]["acted"] == {"value": 1}
    assert counts["wrong"] == {"value": 1, "of": 9}
    assert g["coverage"] == [{"n": 8, "title": "Stay calm", "conversations": 3}, {"n": 1, "title": "Work", "conversations": 0}]
    # Reviewed stays reviewed: the count doesn't drop if feedback is later changed.
    conn.execute("UPDATE feedback SET novelty=NULL WHERE session_id=?", (sids[1],))
    assert R.progress(conn, ps)["counts"]["checks"]["reviewed"]["value"] == 3


def test_conversations_marked_not_counted_stay_out_of_the_counts(tmp_path):
    from octo.db import connect
    conn = connect(tmp_path / "g.db")
    conn.execute("INSERT INTO recordings (id, source, path, duration_s, created_at, started_at) VALUES ('r', 'phone', 'x', 1, 0, 1790000000)")
    for i, sid in enumerate(["ses_20261001_0000000a", "ses_20261001_0000000b"]):
        conn.execute("INSERT INTO sessions (id, recording_id, start_s, end_s, speech_s, created_at) VALUES (?, 'r', ?, ?, 1, 0)", (sid, i * 10, i * 10 + 5))
        _seed(conn, sid)
    conn.execute("UPDATE sessions SET in_gate=0 WHERE id='ses_20261001_0000000b'")  # a work memo kept for testing
    assert R.progress(conn, [])["counts"]["checks"]["reviewed"]["value"] == 1


def test_a_rounded_up_talk_time_doesnt_read_as_enough():  # review 4 Oct, M33
    from octo.sessions import skip_text
    assert skip_text("too short: you spoke for 20 s (insights need 20 s)") == \
        "too short: you spoke for just under 20 s, and insights need 20 s"
    assert skip_text("too short: you spoke for 12 s (insights need 20 s)") == "too short: you spoke for 12 s (insights need 20 s)"
