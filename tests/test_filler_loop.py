"""The filler cue's loop (B22, octo/filler_loop.py): the cue's switch logged, the live cue labelled by the transcript,
the weekly retrain's timing, and installing a better model (refused while recording, the model before kept on a
failed update)."""

import json
import time
from datetime import datetime

import pytest

from octo import device as D
from octo import filler_loop as FLP
from tests.test_fillers import QWEN, _client, _conv, _lab

DAY = 86400


@pytest.fixture
def filler_dir(tmp_path, monkeypatch):
    d = tmp_path / "filler"
    monkeypatch.setattr(FLP, "FILLER", d)
    monkeypatch.setattr(FLP, "CUE_LOG", d / "cue_log.jsonl")
    monkeypatch.setattr(FLP, "MODEL", d / "octo_filler_model.h")
    monkeypatch.setattr(FLP, "MODEL_META", d / "model.json")
    monkeypatch.setattr(FLP, "MODEL_BEFORE", d / "model.before.json")
    monkeypatch.setattr(FLP, "CANDIDATE", d / "candidate")
    monkeypatch.setattr(FLP, "TRAIN_STATE", d / "train.json")
    monkeypatch.setattr(FLP, "INSTALL_STATE", d / "install.json")
    monkeypatch.setattr(FLP, "JOB", d / "job.json")
    return d


def test_cue_state_logged_on_change_only(filler_dir):
    st = {"filler_model": True, "filler_on": True, "filler_threshold": 0.96, "filler_ready": True}
    FLP.note_cue_state(st, now=100)
    FLP.note_cue_state(st, now=130)                                  # the same: nothing new
    FLP.note_cue_state({**st, "filler_on": False}, now=200)
    FLP.note_cue_state({**st, "filler_threshold": 0.9}, now=300)     # on again, stricter
    FLP.note_cue_state({"state": "recording"}, now=400)              # firmware without the cue: nothing
    log = FLP.cue_log()
    assert [(e["at"], e["on"], e["threshold"]) for e in log] == [(100, True, 0.96), (200, False, 0.96), (300, True, 0.9)]
    assert FLP.on_spans(log, now=500) == [(100, 200), (300, 500)]
    FLP.note_cue_state({**st, "filler_ready": False}, now=600)       # switched on but not running: off
    assert FLP.cue_log()[-1]["on"] is False


def test_label():
    # on the lab's timeline a cue sits ~0.3 s before the phrase's middle; within 0.7 s of that is a hit
    yk = [(10.0, True), (20.0, False), (30.0, True)]
    right, his, caught = FLP.label([9.7, 20.3, 31.0, 45.0], yk)
    assert (right, his, caught) == (2, 2, 1)  # 31.0 is 1.3 s late; someone else's "you know" makes a right cue, not a caught one of his


def _tr(words):
    return {**QWEN, "segments": [{"words": [{"w": w, "start": a, "end": b} for w, a, b in words]}]}


def test_live_labels_cues_by_the_transcript(tmp_path, filler_dir, monkeypatch):
    conn = _lab(tmp_path)
    now = datetime(2026, 10, 22, 12, 0).timestamp()
    at = now - DAY
    # A (the owner) says "you know" at 0.2-0.6 s; B says one at 4.2-4.6 s; the owner's second, at 2.3 s, isn't caught.
    words = [("so", 0.0, 0.2), ("you", 0.2, 0.4), ("know", 0.4, 0.6), ("the", 0.7, 0.9), ("plan", 0.9, 1.2),
             ("you", 2.2, 2.3), ("know", 2.3, 2.4), ("yes", 4.0, 4.2), ("you", 4.2, 4.4), ("know", 4.4, 4.6)]
    sid = _conv(conn, 1, at, [("A", "so you know the plan you know"), ("B", "yes you know")], tr=_tr(words))
    lines = [json.dumps(d) for d in ({"boot": 7}, {"clock_ms": at * 1000, "at_ms": 0},
                                      {"filler": "you know", "score": 0.97, "at_ms": 200},     # 0.2 s before his first's middle
                                      {"filler": "you know", "score": 0.98, "at_ms": 4300},    # at B's
                                      {"filler": "you know", "score": 0.99, "at_ms": 3200})]   # nothing there
    conn.execute("INSERT INTO device_boots (boot, manifest) VALUES (7, ?)", ("\n".join(lines) + "\n",))
    FLP.note_cue_state({"filler_on": True, "filler_threshold": 0.96}, now=at - 600)
    lv = FLP.live(conn, now=now)
    assert lv == {"days": 7, "conversations": 1, "cues": 3, "right": 2, "his": 2, "caught": 1, "waiting": 0}
    assert sid
    # Switched off before it was recorded, it still counts its cues, but none of his as missed.
    FLP.CUE_LOG.unlink()
    FLP.note_cue_state({"filler_on": True, "filler_threshold": 0.96}, now=at - 900)
    FLP.note_cue_state({"filler_on": False, "filler_threshold": 0.96}, now=at - 600)
    lv = FLP.live(conn, now=now)
    assert (lv["cues"], lv["right"], lv["his"], lv["caught"]) == (3, 2, 0, 0)
    assert FLP.live(conn, now=now + 8 * DAY)["conversations"] == 0  # past the 7 days


def test_due_weekly_at_night(filler_dir):
    night = datetime(2026, 10, 23, 2, 0).timestamp()
    assert not FLP.due(night)                       # no model to better
    FLP.MODEL.parent.mkdir(parents=True)
    FLP.MODEL.write_text("// model")
    assert FLP.due(night)
    assert not FLP.due(night + 10 * 3600)           # noon
    FLP._write(FLP.TRAIN_STATE, {"started_at": night - 3 * DAY})
    assert not FLP.due(night)                       # ran 3 days ago
    assert FLP.due(night + 4 * DAY)


def test_before_spec(filler_dir):
    assert FLP.before_spec() == {"ids": []}
    FLP.MODEL.parent.mkdir(parents=True)
    FLP.MODEL.write_text("// model")
    assert set(FLP.before_spec()) == {"until"}      # the first model, made before the loop kept its conversations
    FLP._write(FLP.MODEL_META, {"conversations": ["ses_a", "ses_b"]})
    assert FLP.before_spec() == {"ids": ["ses_a", "ses_b"]}


def _candidate():
    FLP.MODEL.parent.mkdir(parents=True, exist_ok=True)
    FLP.MODEL.write_text("// old model")
    FLP.CANDIDATE.mkdir(parents=True)
    (FLP.CANDIDATE / "octo_filler_model.h").write_text("// new model")
    FLP._write(FLP.CANDIDATE / "candidate.json", {"made_at": time.time(), "threshold": 0.95, "conversations": ["ses_a"],
                                                  "bench": {"conversations": 30, "before_mean": 0.62, "now_mean": 0.7}})


def test_install(filler_dir, monkeypatch):
    with pytest.raises(FLP.InstallRefused):
        FLP.install(flash=lambda log: None, set_threshold=lambda t: None)   # nothing waiting
    _candidate()
    monkeypatch.setattr(D, "last_status", lambda: {"status": {"state": "recording"}})
    with pytest.raises(FLP.InstallRefused):
        FLP.install(flash=lambda log: None, set_threshold=lambda t: None)
    monkeypatch.setattr(D, "last_status", lambda: {"status": {"state": "stopped"}})

    def fails(log):
        raise RuntimeError("the update didn't go through")
    with pytest.raises(RuntimeError):
        FLP.install(flash=fails, set_threshold=lambda t: None)
    assert FLP.MODEL.read_text() == "// old model" and not FLP.MODEL_META.exists()   # the model before, back
    assert FLP.state()["candidate"] and FLP.state()["install"]["error"]

    set_to = []
    res = FLP.install(flash=lambda log: None, set_threshold=set_to.append)
    assert FLP.MODEL.read_text() == "// new model" and set_to == [0.95] and res["threshold"] == 0.95
    assert json.loads(FLP.MODEL_META.read_text())["conversations"] == ["ses_a"]
    assert FLP.state()["candidate"] is None


def test_install_keeps_the_new_model_when_only_the_threshold_fails(filler_dir, monkeypatch):
    _candidate()
    monkeypatch.setattr(D, "last_status", lambda: {"status": {"state": "stopped"}})

    def no_answer(t):
        raise D.DeviceError("not on the Wi-Fi")
    res = FLP.install(flash=lambda log: None, set_threshold=no_answer)
    assert FLP.MODEL.read_text() == "// new model" and "octo device filler on 0.95" in res["error"]


def test_rec27_takes_any_fillers_model_as_data_and_undoes_it(filler_dir, monkeypatch):
    """B29: a candidate for another filler goes to the recorder as data (never into the build), the lab's record of
    the one before is kept for Undo, and what's installed is what the recorder says."""
    from tests.test_filler_blob import _fake, header
    _candidate()
    FLP._write(FLP.MODEL_META, {"target": "you know", "conversations": ["ses_old"]})
    uh = FLP.paths("uh")[1]
    uh.mkdir(parents=True)
    (uh / "octo_filler_model.h").write_text(header(*_fake(500), thr=0.97))
    FLP._write(uh / "candidate.json", {"made_at": time.time(), "threshold": 0.97, "conversations": ["ses_b"],
                                       "bench": {"conversations": 30, "before_mean": None, "now_mean": 0.5}})
    st = {"state": "stopped", "fw": "rec-26"}
    monkeypatch.setattr(D, "last_status", lambda: {"status": st})
    with pytest.raises(FLP.InstallRefused, match="rec-27"):
        FLP.install(target="uh", flash=lambda log: None, set_threshold=lambda t: None)

    st.update(fw="rec-27", filler_models=True, filler_name="you know")
    sent, set_to = [], []
    res = FLP.install(target="uh", send=lambda blob, log: sent.append(blob), set_threshold=set_to.append)
    assert sent[0][8:10] == b"uh" and res["target"] == "uh" and set_to == [0.97]
    assert FLP.MODEL.read_text() == "// old model"                   # the build's own stays "you know"
    assert json.loads(FLP.MODEL_META.read_text())["target"] == "uh" and not uh.exists()
    assert json.loads(FLP.MODEL_BEFORE.read_text())["conversations"] == ["ses_old"]
    st.update(filler_name="uh", filler_before="you know")
    assert FLP.installed_target() == "uh" and FLP.state()["candidate"] is None   # its own candidate, none yet

    res = FLP.undo(send_undo=lambda log: {**st, "filler_name": "you know", "filler_before": "uh"})
    assert res["target"] == "you know"
    assert json.loads(FLP.MODEL_META.read_text())["conversations"] == ["ses_old"]
    assert json.loads(FLP.MODEL_BEFORE.read_text())["target"] == "uh"
    st.update(filler_before="", state="recording")
    with pytest.raises(FLP.InstallRefused):
        FLP.undo(send_undo=lambda log: {})


def test_a_failed_send_leaves_the_record_as_it_was(filler_dir, monkeypatch):
    from tests.test_filler_blob import _fake, header
    FLP.MODEL.parent.mkdir(parents=True)
    FLP.MODEL.write_text("// old model")
    FLP._write(FLP.MODEL_META, {"target": "you know"})
    FLP.CANDIDATE.mkdir()
    (FLP.CANDIDATE / "octo_filler_model.h").write_text(header(*_fake(500)))
    FLP._write(FLP.CANDIDATE / "candidate.json", {"made_at": 1, "threshold": 0.95, "bench": {}})
    monkeypatch.setattr(D, "last_status", lambda: {"status": {"state": "stopped", "filler_models": True}})

    def fails(blob, log):
        raise D.DeviceError("the recorder didn't start with the new model and went back to the one before")
    with pytest.raises(D.DeviceError):
        FLP.install(send=fails, set_threshold=lambda t: None)
    assert FLP.MODEL.read_text() == "// old model" and json.loads(FLP.MODEL_META.read_text()) == {"target": "you know"}
    assert FLP.state()["candidate"] and "went back" in FLP.state()["install"]["error"]


def test_settings_route(tmp_path, filler_dir, monkeypatch):
    _lab(tmp_path)
    _candidate()
    monkeypatch.setattr(D, "last_status", lambda: {"status": {"state": "recording", "filler_model": True, "filler_on": True}})
    c = _client(tmp_path)
    loop = c.get("/api/octo/settings/recorder/filler").json()["loop"]
    assert loop["candidate"]["threshold"] == 0.95 and "70%" in loop["candidate"]["line"]
    assert loop["live"]["cues"] == 0
    r = c.post("/api/octo/settings/recorder/filler/install", json={})
    assert r.status_code == 409 and "recording" in r.json()["detail"]
    # B29: another filler's candidate needs rec-27; Undo needs a model before
    (FLP.paths("uh")[1]).mkdir(parents=True)
    (FLP.paths("uh")[1] / "octo_filler_model.h").write_text("// uh")
    monkeypatch.setattr(D, "last_status", lambda: {"status": {"state": "stopped", "fw": "rec-26", "filler_model": True}})
    r = c.post("/api/octo/settings/recorder/filler/install", json={"target": "uh"})
    assert r.status_code == 409 and "rec-27" in r.json()["detail"]
    r = c.post("/api/octo/settings/recorder/filler/undo", json={})
    assert r.status_code == 409 and "no model before" in r.json()["detail"]
    st = {"state": "stopped", "filler_model": True, "filler_models": True, "filler_name": "you know", "filler_before": "uh"}
    monkeypatch.setattr(D, "last_status", lambda: {"status": st})
    assert c.get("/api/octo/settings/recorder/filler").json()["before"] == "uh"


# ---- B29: any filler, and training it as a job -----------------------------------------------------------------

def _w(*spec):
    """Timed words from (word, speaker, owner?) triples, 0.3 s apart."""
    return [{"w": w, "start": i * 0.3, "end": i * 0.3 + 0.25, "spk": spk, "me": me} for i, (w, spk, me) in enumerate(spec)]


def test_a_phrase_is_its_words_in_a_row_by_one_speaker():
    ws = _w(("so", "A", True), ("You", "A", True), ("know,", "A", True), ("kind", "A", True), ("of", "B", False),
            ("kind", "B", False), ("of", "B", False), ("you", "B", False), ("know", "A", True))
    assert FLP.hits(ws, "you know") == [(1, 2)]  # the last "you" and "know" are two people's
    assert FLP.hits(ws, "kind of") == [(5, 2)]
    assert FLP.occurrences(ws, "you know") == [((0.3 + 0.85) / 2, True)]


def test_the_hesitations_are_one_target_however_long_but_not_listening_noises():
    ws = _w(("umm", "A", True), ("uh", "B", False), ("huh", "B", False), ("ah", "A", True), ("the", "A", True),
            ("Hmm.", "A", True), ("mm", "B", False), ("hmm", "B", False))
    assert [i for i, _ in FLP.hits(ws, FLP.HESITATION)] == [0, 3, 5, 7]  # "uh huh" and "mm hmm" are listening
    assert "um" in FLP.phrases_of("uh") and FLP.phrases_of("kind of") == ["kind of"]


def test_readiness_says_which_fillers_could_be_trained_and_why_not(tmp_path, filler_dir, monkeypatch):
    from tests.test_fillers import _words
    conn = _lab(tmp_path)
    FI = __import__("octo.fillers", fromlist=["x"])
    FI.set_phrase(conn, "like", True)
    now = datetime(2026, 10, 22, 12, 0).timestamp()
    for n in range(3):  # three weeks: about 40 of his "you know"s a week
        _conv(conn, 10 + n, now - (21 - 7 * n) * DAY, [
            ("A", _words(160, filler_every=4, filler="you know") + " um like"),
            ("B", _words(200, filler_every=10, filler="like") + " you know")])
    got = {r["target"]: r for r in FLP.readiness(conn, now=now)}
    assert got["you know"]["his"] == 120 and got["you know"]["status"] == "ready" and got["you know"]["leans"] > 1.5
    assert got["like"]["status"] == "ordinary"  # others say it far more: mostly an ordinary word for him
    assert got["kind of"]["status"] == "none"
    uh = got["uh"]
    assert uh["his"] == 3 and uh["status"] == "growing" and uh["weeks_left"] == 97.0  # 1 a week, 97 to go
    assert "um" in uh["phrases"] and "um" not in got
    (filler_dir / "model.json").parent.mkdir(parents=True, exist_ok=True)
    FLP.MODEL.write_text("model")
    assert {r["target"]: r["status"] for r in FLP.readiness(conn, now=now)}["you know"] == "on"


def _fake_bench(better: bool):
    def run(cmd, log, target="you know", on_line=None):
        if cmd[1].name != "loop.py":
            raise AssertionError("only the bench runs when it isn't better")
        on_line('{"plan": {"trainings": 9, "conversations": 26, "examples": 453}}\n')
        on_line("  w32 fold 1/3: train 17 conversations\n")
        st = json.loads(FLP.paths(target)[0].read_text())
        assert st["step"] == "testing" and st["done"] == 1 and st["trainings"] == 9  # the page sees it as it goes
        cmd[2].write_text(json.dumps({"conversations": 26, "before_conversations": 0, "hours": 11.0, "his": 453,
                                      "before": [], "now": [{"caught": 0.3}] * 3, "before_mean": 0.0, "now_mean": 0.3,
                                      "better": better, "threshold": None}))
    return run


def test_training_another_filler_keeps_its_own_files_and_progress(filler_dir, monkeypatch):
    monkeypatch.setattr(FLP, "LOG", filler_dir / "train.log")
    monkeypatch.setattr(FLP, "_run", _fake_bench(False))
    st = FLP.train(log=lambda m: None, target="uh")
    assert not st.get("error") and st["step"] == "done" and not st.get("candidate")
    assert (filler_dir / "targets" / "uh" / "train.json").exists() and not FLP.TRAIN_STATE.exists()  # "you know"'s untouched
    lt = FLP.last_training("uh")
    assert lt["caught"] == 0.3 and lt["before"] is None and lt["candidate"] is False and lt["trainings"] == 9
    assert FLP.before_spec("uh") == {"ids": []}  # nothing of it on the recorder to beat


def test_one_job_at_a_time_and_cancel_stops_it(filler_dir, monkeypatch):
    import subprocess
    import sys
    monkeypatch.setattr(FLP, "LOG", filler_dir / "train.log")
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    FLP._write(FLP.JOB, {"target": "uh", "pid": sleeper.pid, "started_at": time.time()})
    FLP._write(FLP.paths("uh")[0], {"target": "uh", "running": True, "step": "testing", "done": 3, "trainings": 9})
    with pytest.raises(FLP.JobRefused, match="one at a time"):
        FLP.start_job("you know")
    assert not FLP.hold_job("you know")
    _candidate()
    monkeypatch.setattr(D, "last_status", lambda: {"status": {"state": "stopped"}})
    with pytest.raises(FLP.InstallRefused, match="training"):
        FLP.install(flash=lambda log: None, set_threshold=lambda t: None)
    assert FLP.cancel_job()
    assert sleeper.wait(timeout=5) != 0 and FLP.job() is None
    assert FLP.last_training("uh")["cancelled"] is True
    assert not FLP.cancel_job()


def test_a_training_whose_process_died_says_so(filler_dir):
    FLP._write(FLP.paths("kind of")[0], {"target": "kind of", "running": True, "step": "testing", "done": 2})
    assert FLP.last_training("kind of")["error"] == "it stopped before it finished"


def test_train_routes(tmp_path, filler_dir, monkeypatch):
    _lab(tmp_path)
    started = []
    monkeypatch.setattr(FLP, "start_job", lambda t: started.append(t))
    monkeypatch.setattr(D, "last_status", lambda: {"status": {"state": "stopped", "filler_model": True}})
    c = _client(tmp_path)
    loop = c.get("/api/octo/settings/recorder/filler").json()["loop"]
    assert loop["target"] == "you know" and loop["job"] is None and "uh" in {t["target"] for t in loop["targets"]}
    assert c.post("/api/octo/settings/recorder/filler/train", json={"target": "Kind of"}).json()["target"] == "kind of"
    assert started == ["kind of"]
    assert c.post("/api/octo/settings/recorder/filler/train", json={"target": "banana"}).status_code == 422
    assert c.post("/api/octo/settings/recorder/filler/train/cancel", json={}).json() == {"cancelled": False}
