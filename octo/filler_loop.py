"""The filler cue's loop (B22, docs/filler-plan.md step 3): the live cue labelled by the transcript afterwards, a
weekly retrain, and a better model offered on Settings for the owner to install. Nobody labels anything.

- Labels: a cue the recorder flashed is right when the transcript has a "you know" there; one of the owner's "you
  know"s is caught when a cue followed it. Only conversations recorded while the cue was on count for "caught": the
  lab logs each change of the cue's switch and threshold as the 30 s status check sees it (`note_cue_state`).
- Retrain: `octo filler train` (weekly from the server, at night) runs the spike's bench (spikes/b20-filler-cue/loop.py)
  in the lab's venv: the installed model's data against everything to date, same folds and seeds. Only a retrain
  better beyond the seed spread is exported (export.py) and converted to int8 (convert.py, TensorFlow in a venv of
  its own, never the lab's) into data/filler/candidate/.
- Install: the owner's button (the recorder refuses updates while recording, and nothing here stops it). Since rec-27
  (B29) the candidate goes to the recorder as data (`pack`, `device.send_filler_model`) into the slot not in use, and
  Undo puts the one before back; before rec-27 it becomes data/filler/octo_filler_model.h and `octo device flash` builds
  it in ("you know" only). Either way the cue gets the candidate's threshold; a failure leaves the one before.

- Any filler (B29, docs/filler-training-plan.md): a target is a phrase from the owner's list, or "uh" for the
  hesitations (um, uh, ah, … are one sound the transcribers spell differently: learnings, Speech, 23 Oct). Training a
  target is a job the Settings page starts, follows and cancels; one at a time, never during an install. "you know"
  keeps its files where they were; other targets have their own folder under data/filler/targets/.

Everything here stays in data/filler/: the model is trained on the owner's voice, so it's never in git and not
exported (export.SKIP_DIRS).
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import BACKGROUND, DATA, HOME, ROOT
from . import device as D
from . import files as FL
from . import fillers as FI
from .db import rows

FILLER = DATA / "filler"
MODEL = D.FILLER_MODEL                     # what `octo device flash` builds in
MODEL_META = FILLER / "model.json"         # the installed model's bench and conversations (none for the first, 21 Oct)
MODEL_BEFORE = FILLER / "model.before.json"  # the one before's, for Undo (rec-27)
CANDIDATE = FILLER / "candidate"           # a better model waiting for the owner
CUE_LOG = FILLER / "cue_log.jsonl"         # the cue's switch and threshold, each change
TRAIN_STATE = FILLER / "train.json"        # the last retrain: when, and what it found
INSTALL_STATE = FILLER / "install.json"
JOB = FILLER / "job.json"                  # the training job running now: its target and process group
LOG = DATA / "logs" / "filler-train.log"
SPIKE = ROOT / "spikes" / "b20-filler-cue"
TF_VENV = Path.home() / "Library" / "Application Support" / "octo" / "filler-tf"  # TensorFlow, for convert.py only
TF_PINS = ["tensorflow==2.19.0", "numpy==2.1.3"]

DAY = 86400
EVERY_S = 7 * DAY
NIGHT = range(1, 6)        # local hours a weekly retrain may start in (it takes the GPU for about an hour)
MATCH_S = 0.7              # a cue this close to where it should sit (fillers.CUE_LAG_S from the phrase's middle) is a hit
YOU_KNOW = "you know"      # the first target, the recorder's built-in one (rec-24)
HESITATION = "uh"          # the hesitation group's name on the page and the recorder
READY = 100                # of the owner's examples: "you know" on half its data (~100) did as well (learnings, 21 Oct)
LEANS = 1.5                # his rate over others' below this: mostly an ordinary word for him


# ---- the cue's switch, as the status check sees it ------------------------------------------------------------

def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def cue_log() -> list[dict]:
    try:
        lines = CUE_LOG.read_text().splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def note_cue_state(st: dict | None, now: float | None = None) -> None:
    """A line in the cue log when the recorder's cue switch or threshold differs from the last one logged."""
    if not st or "filler_on" not in st:
        return
    cur = {"on": bool(st["filler_on"] and st.get("filler_ready", True)), "threshold": st.get("filler_threshold")}
    log = cue_log()
    if log and {k: log[-1].get(k) for k in cur} == cur:
        return
    FILLER.mkdir(parents=True, exist_ok=True)
    with open(CUE_LOG, "a") as f:
        f.write(json.dumps({"at": round(now or time.time(), 1), **cur}) + "\n")


def on_spans(log: list[dict], now: float) -> list[tuple[float, float]]:
    """When the cue was on, from the log."""
    out, since = [], None
    for e in log:
        if e["on"] and since is None:
            since = e["at"]
        elif not e["on"] and since is not None:
            out.append((since, e["at"]))
            since = None
    if since is not None:
        out.append((since, now))
    return out


# ---- the live cue, labelled by the transcript -----------------------------------------------------------------

def phrases_of(target: str) -> list[str]:
    return sorted(FI.HESITATIONS) if target == HESITATION else [target]


def _norm(w: str) -> str:
    return "".join(ch for ch in w.lower() if ch.isalpha() or ch == "'")


def hits(ws: list[dict], target: str) -> list[tuple[int, int]]:
    """Where the target is said in timed words, as (first word, how many): a phrase's words in a row by one speaker;
    for the hesitations any one of them however long it's held ("umm"), but not "uh huh" or "mm hmm" (listening)."""
    out, i = [], 0
    if target == HESITATION:
        pat = FI.matcher(phrases_of(target))
        for i, w in enumerate(ws):
            nxt = _norm(ws[i + 1]["w"]) if i + 1 < len(ws) else ""
            if w["spk"] and pat.fullmatch(_norm(w["w"])) and nxt not in ("huh", "hmm"):
                out.append((i, 1))
        return out
    parts = [p.split() for p in phrases_of(target)]
    while i < len(ws):
        k = next((len(p) for p in parts if len(ws[i:i + len(p)]) == len(p) and ws[i]["spk"]
                  and all(_norm(w["w"]) == x and w["spk"] == ws[i]["spk"] for w, x in zip(ws[i:i + len(p)], p))), 0)
        if k:
            out.append((i, k))
        i += k or 1
    return out


def occurrences(ws: list[dict], target: str) -> list[tuple[float, bool]]:
    """Each time the target is said: (its middle, the owner's?)."""
    return [((ws[i]["start"] + ws[i + k - 1]["end"]) / 2, ws[i]["me"]) for i, k in hits(ws, target)]


def heard(conn, session_id: str, target: str = YOU_KNOW) -> tuple[list[dict], list[tuple[float, bool]]] | None:
    """(the conversation's lines, each time the target is said), or None until it's transcribed and diarized."""
    from .delivery import timed_words
    out, mine, _, tr = FI.load(conn, session_id)
    if not out:
        return None
    ws = timed_words(tr, out["lines"], mine)
    if ws is None:
        return None
    return out["lines"], occurrences(ws, target)


def label(cue_at: list[float], yk: list[tuple[float, bool]]) -> tuple[int, int, int]:
    """(cues that were right, the owner's "you know"s, how many of those were caught)."""
    hit = lambda at, m: abs(at - m - FI.CUE_LAG_S) <= MATCH_S
    right = sum(any(hit(at, m) for m, _ in yk) for at in cue_at)
    his = [m for m, me in yk if me]
    return right, len(his), sum(any(hit(at, m) for at in cue_at) for m in his)


def live(conn, days: int = 7, now: float | None = None) -> dict:
    """The live cue over the last `days`: its cues and how many were right; the owner's "you know"s while it was on
    and how many it caught. Conversations not yet transcribed wait."""
    now = now or time.time()
    target = installed_target()  # a flash is labelled against the filler it was for (its manifest line says)
    log = cue_log()
    spans = on_spans(log, now)
    first = log[0]["at"] if log else now
    tot = {"days": days, "conversations": 0, "cues": 0, "right": 0, "his": 0, "caught": 0, "waiting": 0}
    for sess in rows(conn, "SELECT s.id, s.start_s, s.end_s, r.source, r.started_at FROM sessions s "
                           "JOIN recordings r ON r.id = s.recording_id WHERE s.status='active' AND r.source='device' "
                           "AND r.started_at + s.start_s >= ?", now - days * DAY):
        a, b = sess["started_at"] + sess["start_s"], sess["started_at"] + sess["end_s"]
        got = heard(conn, sess["id"], target)
        if got is None:
            tot["waiting"] += 1
            continue
        lines, yk = got
        cues = [c["at"] for c in FI.recorder_cues(conn, dict(sess), lines) or [] if c["filler"] == target]
        # On while it was recorded: the log says so, or (before the log began) it flashed at least once.
        on = any(s < b and e > a for s, e in spans) or (a < first and bool(cues))
        if not on and not cues:
            continue
        right, his, caught = label(cues, yk)
        tot["conversations"] += 1
        tot["cues"] += len(cues)
        tot["right"] += right
        if on:
            tot["his"] += his
            tot["caught"] += caught
    return tot


# ---- the weekly retrain ---------------------------------------------------------------------------------------

def state() -> dict:
    """What Settings shows: the installed model's bench, its candidate, its last retrain and the last install."""
    train, cand = paths(installed_target())
    return {"installed": _read_json(MODEL_META), "has_model": MODEL.exists(),
            "candidate": _read_json(cand / "candidate.json") if (cand / "octo_filler_model.h").exists() else None,
            "train": _read_json(train), "install": _read_json(INSTALL_STATE)}


def _write(path: Path, d: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    FL.write_private(path, json.dumps(d, indent=1))


def installed_target() -> str:
    """The filler the recorder's model is for, as it last said (rec-27: never from the lab's own files, which a full
    flash or a rollback can make wrong); else the lab's record, "you know" until another is installed (B29)."""
    st = (D.last_status() or {}).get("status") or {}
    return st.get("filler_name") or (_read_json(MODEL_META) or {}).get("target") or YOU_KNOW


def slug(target: str) -> str:
    return "-".join(target.split())


def paths(target: str) -> tuple[Path, Path]:
    """(its last training's state, its candidate folder). "you know" keeps the files it had before B29."""
    if target == YOU_KNOW:
        return TRAIN_STATE, CANDIDATE
    d = FILLER / "targets" / slug(target)
    return d / "train.json", d / "candidate"


def due(now: float | None = None) -> bool:
    """A weekly retrain is due: there's a model to better, none ran for a week, and it's night here."""
    now = now or time.time()
    last = (_read_json(TRAIN_STATE) or {}).get("started_at") or 0
    return MODEL.exists() and now - last >= EVERY_S and time.localtime(now).tm_hour in NIGHT


def before_spec(target: str = YOU_KNOW) -> dict:
    """What the installed model was trained on: its conversations, or for the first (21 Oct, made before the loop
    kept them) the conversations recorded before its file was written. A filler not on the recorder has none."""
    if target != installed_target():
        return {"ids": []}
    meta = _read_json(MODEL_META)
    if meta and meta.get("conversations"):
        return {"ids": meta["conversations"]}
    return {"until": MODEL.stat().st_mtime} if MODEL.exists() else {"ids": []}


def tf_python(log) -> Path:
    """The TensorFlow venv's python, made on first use (TensorFlow ~1.4 GB, downloaded once)."""
    py = TF_VENV / "bin" / "python"
    if not py.exists():
        from .privacy import WARN_GB, free_gb
        if free_gb() < WARN_GB:  # TensorFlow is ~1.4 GB on disk
            raise RuntimeError(f"the trainer needs TensorFlow (1.4 GB) and this Mac has {free_gb()} GB free: "
                               f"make room for {WARN_GB} GB, then train again")
        log(f"making a TensorFlow venv in {TF_VENV} (once; downloads TensorFlow)")
        subprocess.run([getattr(sys, "_base_executable", sys.executable), "-m", "venv", str(TF_VENV)], check=True)
        subprocess.run([str(py), "-m", "pip", "install", "-q", *TF_PINS], check=True)
    return py


def _run(cmd: list, log, target: str = YOU_KNOW, on_line=None) -> None:
    """A step in its own process, its output into the log; `on_line` sees each line as it comes (the progress)."""
    env = {**os.environ, "OCTO_HOME": str(HOME), "OCTO_FILLER": target}
    with open(LOG, "a") as f:
        f.write(time.strftime(f"\n%Y-%m-%d %H:%M:%S $ {' '.join(map(str, cmd[1:]))}\n"))
        f.flush()
        pr = subprocess.Popen([str(c) for c in cmd], cwd=SPIKE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True)
        for ln in pr.stdout:
            f.write(ln)
            f.flush()
            if on_line:
                on_line(ln)
        r = pr.wait()
    if r:
        raise RuntimeError(f"{Path(str(cmd[1])).name} failed (exit {r}); see {LOG}")


def train(log=print, target: str = YOU_KNOW) -> dict:
    """The bench; and if the retrain is better beyond the seed spread (or, for a filler not on the recorder, catches
    at least loop.FLOOR), a candidate model for the owner to install. Its progress is in its state file as it goes:
    gathering, testing (k of n trainings), training on everything, making it small for the recorder."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    state_path, cand_dir = paths(target)
    st = {"target": target, "started_at": time.time(), "running": True, "step": "gathering"}
    _write(state_path, st)

    def progress(ln: str) -> None:
        if ln.startswith("{\"plan\""):  # loop.py: how many trainings, from how many conversations and examples
            st.update(json.loads(ln)["plan"], step="testing", done=0)
        elif " fold " in ln and st.get("step") == "testing":
            st["done"] = st.get("done", 0) + 1
        else:
            return
        _write(state_path, st)

    try:
        work = (state_path.parent / "work") if target != YOU_KNOW else FILLER / "work"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        (work / "before.json").write_text(json.dumps(before_spec(target)))
        log(f"bench for “{target}”: 3 folds x 3 seeds")
        _run([sys.executable, SPIKE / "loop.py", work / "bench.json", work / "before.json"], log, target, progress)
        bench = json.loads((work / "bench.json").read_text())
        st.update(bench={k: v for k, v in bench.items() if k not in ("before", "now")},
                  before=[g["caught"] for g in bench["before"]], now=[g["caught"] for g in bench["now"]])
        log(f"caught at <= 3 wrong an hour: installed data {bench['before_mean']:.0%}, all to date {bench['now_mean']:.0%}"
            f" ({bench['before_conversations']} and {bench['conversations']} conversations)")
        if not bench["better"] or bench["threshold"] is None:
            log("not better beyond the seed spread (or under the floor for a first model): nothing to install")
        else:
            log("better: training it on everything and converting it for the chip")
            st["step"] = "everything"
            _write(state_path, st)
            _run([sys.executable, SPIKE / "export.py", "w32", work / "board", bench["threshold"]], log, target)
            st["step"] = "small"
            _write(state_path, st)
            _run([tf_python(log), SPIKE / "convert.py", work / "board", work / "octo_filler_model.h"], log, target)
            meta = json.loads((work / "board" / "meta.json").read_text())
            shutil.rmtree(cand_dir, ignore_errors=True)
            cand_dir.mkdir(parents=True)
            shutil.copy(work / "octo_filler_model.h", cand_dir / "octo_filler_model.h")
            _write(cand_dir / "candidate.json", {"made_at": time.time(), "target": target, "threshold": bench["threshold"],
                                                 "conversations": meta["conversations"], "bench": st["bench"],
                                                 "before": st["before"], "now": st["now"]})
            st["candidate"] = True
            log("a better cue is ready: install it from Settings (or `octo filler install`)")
        shutil.rmtree(work, ignore_errors=True)
    except Exception as e:
        st["error"] = str(e)
        log(f"retrain failed: {e}")
    st.update(running=False, finished_at=time.time(), step="done")
    _write(state_path, st)
    return st


# ---- one training job at a time, from the page (B29) ----------------------------------------------------------

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def job() -> dict | None:
    """The training job running now ({target, pid, started_at}), or None (a job whose process has gone isn't one)."""
    j = _read_json(JOB)
    return j if j and _alive(j.get("pid")) else None


class JobRefused(Exception):
    pass


def start_job(target: str) -> subprocess.Popen:
    """`octo filler train --target …` in its own process group, so Cancel stops it and everything it started."""
    if job():
        raise JobRefused(f"“{job()['target']}” is training: one at a time")
    if (_read_json(INSTALL_STATE) or {}).get("running"):
        raise JobRefused("a cue is being installed on the recorder")
    from .privacy import STOP_GB, free_gb
    if free_gb() < STOP_GB:
        raise JobRefused(f"this Mac has {free_gb()} GB free: the lab stops below {STOP_GB} GB")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        pr = subprocess.Popen([*BACKGROUND, sys.executable, "-m", "octo.cli", "filler", "train", "--target", target], cwd=ROOT,
                              stdout=f, stderr=f, start_new_session=True)
    _write(JOB, {"target": target, "pid": pr.pid, "started_at": time.time()})
    return pr


def hold_job(target: str) -> bool:
    """`octo filler train` takes the one job slot (started by the page, it's already its own); False if another has it."""
    j = job()
    if j and j["pid"] != os.getpid():
        return False
    _write(JOB, {"target": target, "pid": os.getpid(), "started_at": (j or {}).get("started_at") or time.time()})
    return True


def cancel_job() -> bool:
    """Stop the running job; its state says it was cancelled. False if none was running."""
    j = job()
    if not j:
        return False
    try:
        os.killpg(j["pid"], signal.SIGTERM)
    except OSError:
        pass
    state_path, _ = paths(j["target"])
    st = _read_json(state_path) or {"target": j["target"], "started_at": j["started_at"]}
    _write(state_path, {**st, "running": False, "cancelled": True, "finished_at": time.time(), "step": "done"})
    JOB.unlink(missing_ok=True)
    return True


def last_training(target: str) -> dict | None:
    """A target's last training as the page shows it: running and how far, or what it found."""
    state_path, cand_dir = paths(target)
    st = _read_json(state_path)
    if not st:
        return None
    if st.get("running") and not (job() and job()["target"] == target):
        st = {**st, "running": False, "error": st.get("error") or "it stopped before it finished"}
    keep = ("started_at", "finished_at", "running", "step", "done", "trainings", "conversations", "examples", "error",
            "cancelled")
    out = {k: st[k] for k in keep if k in st}
    if st.get("bench"):
        out["caught"] = st["bench"].get("now_mean")
        out["before"] = st["bench"].get("before_mean") if st["bench"].get("before_conversations") else None
    out["candidate"] = (cand_dir / "octo_filler_model.h").exists()
    return out


# ---- which fillers are ready to train (B29, plan §1) --------------------------------------------------------------

def targets(conn) -> list[str]:
    """The owner's list as training targets: the hesitations on it as one ("uh")."""
    phr = FI.phrases(conn)
    return [p for p in phr if p not in FI.HESITATIONS] + ([HESITATION] if any(p in FI.HESITATIONS for p in phr) else [])


def readiness(conn, now: float | None = None) -> list[dict]:
    """Each target: the owner's examples in recorder conversations on the fillers' line (what the chip hears), how much
    more he says it than the people he talks with, and whether it's ready to train, with why not in plain words."""
    now = now or time.time()
    ts = targets(conn)
    pats = {t: FI.matcher(phrases_of(t)) for t in ts}
    his = dict.fromkeys(ts, 0)
    oth = dict.fromkeys(ts, 0)
    words = {True: 0, False: 0}
    first = None
    phr = FI.phrases(conn)
    for sess in rows(conn, "SELECT s.*, r.started_at FROM sessions s JOIN recordings r ON r.id = s.recording_id "
                           "WHERE s.status='active' AND r.source='device'"):
        loaded = FI.load(conn, sess["id"])
        c = FI.conversation(conn, dict(sess), phr, loaded=loaded)
        if not c or not c["on_line"]:
            continue
        first = min(first or c["at"], c["at"])
        out, mine = loaded[0], loaded[1]
        for ln in out["lines"]:
            me = ln["speaker"] in mine
            words[me] += FI.words(ln["text"])
            for t in ts:
                (his if me else oth)[t] += len(FI.find(ln["text"], pats[t], phrases_of(t)))
    weeks = max(1.0, (now - first) / (7 * DAY)) if first else None
    on = installed_target() if MODEL.exists() else None
    got = []
    for t in ts:
        lean = round((his[t] / words[True]) / (oth[t] / words[False]), 1) if his[t] and oth[t] and words[False] else None
        per_week = his[t] / weeks if weeks else 0
        if t == on:
            status = "on"
        elif not his[t]:
            status = "none"
        elif lean is not None and lean < LEANS:
            status = "ordinary"
        elif his[t] >= READY:
            status = "ready"
        else:
            status = "growing"
        got.append({"target": t, "phrases": phrases_of(t), "his": his[t], "others": oth[t], "leans": lean,
                    "status": status, "need": READY,
                    "weeks_left": round((READY - his[t]) / per_week, 1) if status == "growing" and per_week else None,
                    "training": last_training(t)})
    return got


def better_than_installed(cand: dict) -> str:
    """One line for Settings: the candidate's catch against the installed model's, at the same strictness."""
    b = cand["bench"]
    return (f"caught {b['now_mean']:.0%} of your “{installed_target()}”s, against {b['before_mean']:.0%} for the one on the "
            f"recorder, with at most 3 wrong flashes an hour ({b['conversations']} conversations it hadn't heard, 3 tries each)")


# ---- installing the candidate ---------------------------------------------------------------------------------

class InstallRefused(Exception):
    pass


def install(log=print, flash=None, set_threshold=None, target: str | None = None, send=None) -> dict:
    """A target's candidate onto the recorder (the installed filler's by default): refused while it records (the
    recorder would refuse too). rec-27 and later take it as data (`send`, `device.send_filler_model` by default);
    older firmware only "you know", by `flash` (`octo device flash` in a subprocess). Either raises on failure, and
    the lab's record of the one before is put back then."""
    target = target or installed_target()
    cand_dir = paths(target)[1]
    cand_h, cand = cand_dir / "octo_filler_model.h", _read_json(cand_dir / "candidate.json")
    if not cand_h.exists() or not cand:
        raise InstallRefused("no better cue is waiting")
    st = (D.last_status() or {}).get("status") or {}
    if st.get("state") == "recording":
        raise InstallRefused("the recorder is recording: switch it off (hold its button 1 s), install, then switch it on")
    if job():
        raise InstallRefused(f"“{job()['target']}” is training: install once it's done")
    as_data = bool(st.get("filler_models"))
    if not as_data and target != YOU_KNOW:
        raise InstallRefused(f"the recorder's firmware ({st.get('fw') or 'not seen yet'}) only has “you know”: update it "
                             "to rec-27 or later first (`octo device flash`)")
    prev = MODEL.read_bytes() if MODEL.exists() else None
    prev_meta = MODEL_META.read_text() if MODEL_META.exists() else None
    _write(INSTALL_STATE, {"started_at": time.time(), "running": True, "target": target})
    meta = {"installed_at": time.time(), "target": target, **cand}
    try:
        if as_data:
            (send or _send)(pack(cand_h.read_text(), target), log)
        else:
            MODEL.write_bytes(cand_h.read_bytes())
            _write(MODEL_META, meta)
            (flash or _flash)(log)
    except Exception as e:  # the recorder kept the firmware or model before (or went back to it): so does the lab
        if prev is not None:
            MODEL.write_bytes(prev)
        if prev_meta is not None:
            FL.write_private(MODEL_META, prev_meta)
        else:
            MODEL_META.unlink(missing_ok=True)
        _write(INSTALL_STATE, {"running": False, "finished_at": time.time(), "target": target, "error": str(e)})
        raise
    if as_data:
        if target == YOU_KNOW:  # what `octo device flash` builds in stays the newest "you know"
            MODEL.write_bytes(cand_h.read_bytes())
        if prev_meta is not None:
            FL.write_private(MODEL_BEFORE, prev_meta)
        _write(MODEL_META, meta)
    shutil.rmtree(cand_dir, ignore_errors=True)
    res = {"running": False, "finished_at": time.time(), "target": target, "threshold": cand["threshold"]}
    try:
        (set_threshold or _set_threshold)(cand["threshold"])
    except Exception as e:  # the new model is on the recorder; only its strictness is left to set
        res["error"] = (f"installed, but its strictness wasn't set ({e}): "
                        f"`octo device filler on {cand['threshold']}` once the recorder is on the Wi-Fi")
    _write(INSTALL_STATE, res)
    return res


def undo(log=print, send_undo=None) -> dict:
    """The model before back on the recorder (rec-27 keeps it in its other slot), with its own threshold; the lab's
    records of the two swap. Refused while recording or training, or when the recorder has nothing before."""
    st = (D.last_status() or {}).get("status") or {}
    if not st.get("filler_before"):
        raise InstallRefused("the recorder has no model before this one to put back")
    if st.get("state") == "recording":
        raise InstallRefused("the recorder is recording: switch it off (hold its button 1 s), then undo")
    if job():
        raise InstallRefused(f"“{job()['target']}” is training: undo once it's done")
    now = (send_undo or _send_undo)(log)
    cur, before = (MODEL_META.read_text() if MODEL_META.exists() else None), _read_json(MODEL_BEFORE)
    _write(MODEL_META, before if before and before.get("target") == now.get("filler_name")
           else {"installed_at": time.time(), "target": now.get("filler_name") or st["filler_before"]})
    if cur is not None:
        FL.write_private(MODEL_BEFORE, cur)
    res = {"running": False, "finished_at": time.time(), "target": now.get("filler_name"), "undone": True}
    _write(INSTALL_STATE, res)
    return res


MODEL_RE = r"kws_model\[(\d+)\] = \{(.*?)\};"
FB_RE = r"kws_fb\[257\]\[40\] = \{(.*?)\};"


def pack(header: str, name: str) -> bytes:
    """convert.py's octo_filler_model.h as the data rec-27 takes (firmware/recorder/octo_kws_blob.h): a header with
    the filler's name, the threshold and the SHA-256 of what follows, the TFLite model, then the filterbank."""
    import hashlib
    import re
    import struct
    raw = name.encode()
    if not 0 < len(raw) < 24 or any(c < 0x20 or c > 0x7E or c in b'"\\' for c in raw):
        raise ValueError(f"not a name the recorder can show: {name!r}")
    thr = float(re.search(r"KWS_THRESHOLD = ([0-9.eE+-]+)f;", header).group(1))
    m = re.search(MODEL_RE, header, re.S)
    model = bytes(int(b) for b in m.group(2).replace("\n", "").split(",") if b.strip())
    fb = [float(v.rstrip("f")) for v in re.findall(r"[-+0-9.eE]+f", re.search(FB_RE, header, re.S).group(1))]
    if len(model) != int(m.group(1)) or len(fb) != 257 * 40:
        raise ValueError("not a whole model header")
    payload = model + bytes(-len(model) % 4) + struct.pack(f"<{len(fb)}f", *fb)
    head = (b"OKWS" + struct.pack("<HH", 1, 80) + raw.ljust(24, b"\0") +
            struct.pack("<fII", thr, len(model), len(fb) * 4) + hashlib.sha256(payload).digest())
    return head.ljust(80, b"\0") + payload


def _send(blob: bytes, log) -> None:
    from .db import connect
    with D.holding(connect(), wait_s=30):
        st = D.send_filler_model(D.Device.from_config(), blob, log)
    D.save_status(st)


def _send_undo(log) -> dict:
    from .db import connect
    with D.holding(connect(), wait_s=30):
        st = D.undo_filler_model(D.Device.from_config(), log)
    D.save_status(st)
    return st


def _set_threshold(threshold: float, tries: int = 4) -> None:
    """The cue on at the candidate's threshold, once the recorder is back after its update."""
    from .db import connect
    for i in range(tries):
        try:
            with D.holding(connect(), wait_s=30):
                st = D.set_filler(D.Device.from_config(), True, threshold)
            D.save_status(st)
            return
        except D.DeviceError:
            if i == tries - 1:
                raise
            time.sleep(15)


def _flash(log) -> None:
    env = {**os.environ, "PATH": f"/opt/homebrew/bin:/usr/local/bin:{os.environ.get('PATH', '')}"}  # arduino-cli
    if not shutil.which("arduino-cli", path=env["PATH"]):
        raise RuntimeError("arduino-cli isn't installed: the recorder's firmware is built here with the model in it")
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(time.strftime("\n%Y-%m-%d %H:%M:%S $ octo device flash\n"))
        f.flush()
        r = subprocess.run([sys.executable, "-m", "octo.cli", "device", "flash"], cwd=ROOT, env=env, stdout=f, stderr=f,
                           timeout=1800)
    if r.returncode:
        tail = LOG.read_text().strip().splitlines()[-1:] or ["?"]
        raise RuntimeError(f"the update didn't go through: {tail[0]}")


# ---- the server's weekly turn ---------------------------------------------------------------------------------

class Trainer:
    """Once an hour, whether a weekly retrain is due; if so `octo filler train` in a subprocess (torch and the
    bench stay out of the server). Off until `bench serve` starts it, so tests never train."""

    def __init__(self) -> None:
        self.running = False
        self.every: float | None = None
        threading.Thread(target=self._loop, daemon=True, name="octo-filler-loop").start()

    def keep_going(self, every_s: float = 3600) -> None:
        self.every = every_s

    def _loop(self) -> None:
        while True:
            time.sleep(self.every or 60)
            if self.every is None or self.running or job() or not due():
                continue
            from .privacy import STOP_GB, free_gb
            if free_gb() < STOP_GB:
                continue
            self.running = True
            try:
                start_job(installed_target()).wait(timeout=6 * 3600)
            except (JobRefused, subprocess.TimeoutExpired):
                pass
            finally:
                self.running = False
