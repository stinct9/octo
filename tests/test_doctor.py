"""`octo doctor` and `octo models` (product plan 4.3): what they report, with every probe replaced, so
nothing here touches launchd, the network or the Hugging Face cache."""

import json
from types import SimpleNamespace

import pytest

from octo import labconfig as LC
from octo import doctor as DR
from octo import models as MD
from octo import owner as O
from octo import settings as ST
from octo.db import connect


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(ST, "_hf_cached", lambda repo: True)
    monkeypatch.setattr(ST, "_ollama_models", lambda: set())
    return connect(tmp_path / "t.db")


def test_the_mac_itself(monkeypatch):
    monkeypatch.setattr(DR.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(DR.platform, "mac_ver", lambda: ("13.6", ("", "", ""), ""))
    monkeypatch.setattr(DR, "_sysctl", lambda name: str(8 * 2**30))
    got = {c.what: c for c in DR.machine()}
    assert got["Apple Silicon"].mark == DR.FAIL and "Apple Silicon" in got["Apple Silicon"].fix
    assert got["Memory"].mark == DR.WARN and got["macOS"].mark == DR.FAIL


def test_missing_packages_and_pages(monkeypatch, tmp_path):
    monkeypatch.setattr(DR, "ROOT", tmp_path)  # no ui/dist here
    got = {c.what: c for c in DR.software(find=lambda p: None if p in ("mlx", "anthropic") else object())}
    assert got["Python packages"].mark == DR.FAIL and got["Python packages"].detail == "mlx, anthropic missing"
    assert got["Web pages built"].mark == DR.FAIL and "npm run build" in got["Web pages built"].fix


def test_login_items_and_the_lab_answering():
    got = {c.what: c for c in DR.running(launchctl=lambda: "123\t0\tcom.octo.lab\n", fetch=lambda: False, lsof=lambda: "",
                                                answers=lambda: False)}
    assert got["The lab starts at login"].mark == DR.OK
    assert got["The recorder relay starts at login"].mark == DR.WARN
    assert got["The lab answers"].mark == DR.FAIL


def test_a_new_lab_is_told_what_to_set_up_and_then_isnt(conn, monkeypatch, tmp_path):
    monkeypatch.delitem(LC.TEST_OVERRIDES, "claude_code")
    from octo import insight as I
    from octo import providers as PV
    from octo import rubric as RB
    monkeypatch.setattr(I, "RUBRIC", tmp_path / "none.md")
    monkeypatch.setattr(DR, "DATA", tmp_path)
    fresh = connect(tmp_path / "new.db")
    got = {c.what: c for c in DR.lab(fresh)}
    assert got["You"].mark == got["Your principles"].mark == got["A model for reviews"].mark == DR.FAIL
    assert "#/setup" in got["You"].fix and got["Recorder"].mark == DR.WARN
    O.save(fresh, {"name": "Asha"})
    RB.save("1. Listen first.", "")
    PV.add("anthropic", api_key="sk-ant-x-1234")
    ST.choose(fresh, "insight", "anthropic:claude-opus-5-5")
    got = {c.what: c for c in DR.lab(fresh)}
    assert got["You"].mark == got["Your principles"].mark == got["A model for reviews"].mark == DR.OK
    assert "Claude Code (this lab's own login)" not in got


def test_the_report_says_how_many_things_to_fix():
    out = DR.report([DR.Check(DR.OK, "A", "fine"), DR.Check(DR.FAIL, "B", "broken", "do this"),
                     DR.Check(DR.WARN, "C", "meh", "maybe that")])
    assert "✗ B: broken\n    → do this" in out and out.rstrip().endswith("1 thing to fix.")
    assert json.loads(DR.as_json([DR.Check(DR.OK, "A")]))[0]["mark"] == DR.OK


def test_just_installed_it_reads_calm_and_says_where_octo_is(conn, monkeypatch):
    """Onboarding review 5 (28 Oct): the first thing a buyer read was a wall of ✗ for models already coming down."""
    monkeypatch.setattr(DR, "running", lambda: [])
    monkeypatch.setattr(DR, "permissions", lambda conn: [])
    what = {c.what for c in DR.run(conn, fresh=True)}
    assert not {"You", "Your principles", "A model for reviews"} & what and not any(w.startswith("Model ") for w in what)
    assert "A copy off this Mac" not in what and "A copy off this Mac" in {c.what for c in DR.run(conn)}  # review 10 Nov
    out = DR.report([DR.Check(DR.WARN, "The Octo app", "not in Applications", "octo launcher install")], fresh=True)
    assert "/bin/octo' launcher install" in out and "coming down in the background" in out


def test_the_models_the_steps_need(conn):
    need = MD.required(conn)
    assert need[:3] == ["Qwen/Qwen3-ASR-1.7B", "Qwen/Qwen3-ForcedAligner-0.6B", "mlx-community/whisper-large-v3-mlx"]
    assert "pyannote/speaker-diarization-3.1" in need and need[-1] == "Wespeaker/wespeaker-voxceleb-resnet293-LM"
    assert [m["gated"] for m in MD.status(conn) if m["repo"].startswith("pyannote/s")] == [True, True]


def test_downloading_runs_online_in_a_child_and_explains_a_gate():
    seen = {}

    def run(args, **kw):
        seen.update(args=args, env=kw["env"])
        return SimpleNamespace(stdout='{"ok": false, "gated": true}\n', stderr="")

    got = MD.download("pyannote/segmentation-3.0", run=run)
    assert got == {"ok": False, "gated": True} and seen["env"]["HF_HUB_OFFLINE"] == "0"
    assert seen["args"][-3:] == ["pyannote/segmentation-3.0", "", MD.PINNED["pyannote/segmentation-3.0"]]  # the tested one
    MD.download("Wespeaker/wespeaker-voxceleb-resnet293-LM", run=run)
    assert seen["args"][-2] == "voxceleb_resnet293_LM.onnx"  # one file, not the whole repo
    assert "hf auth login" in MD.gated_help("pyannote/segmentation-3.0")


def test_a_model_that_isnt_the_tested_version_is_a_warning(conn, tmp_path, monkeypatch):
    """Review 8 Oct, finding 18: the steps load the cache's "main"; doctor says when it isn't the pinned commit."""
    for repo in MD.required(conn):
        ref = tmp_path / ("models--" + repo.replace("/", "--")) / "refs" / "main"
        ref.parent.mkdir(parents=True)
        ref.write_text(MD.PINNED[repo])
    assert MD.drift(conn, hub=tmp_path) == []
    (tmp_path / "models--pyannote--segmentation-3.0" / "refs" / "main").write_text("f" * 40)
    assert MD.drift(conn, hub=tmp_path) == [{"repo": "pyannote/segmentation-3.0", "have": "f" * 40,
                                              "want": MD.PINNED["pyannote/segmentation-3.0"]}]
    monkeypatch.setattr(MD, "cache_dir", lambda: tmp_path)
    monkeypatch.setattr(MD, "cached", lambda repo: True)
    warned = [c for c in DR.models(conn) if c.mark == DR.WARN]
    assert len(warned) == 1 and "tested e66f3d3b" in warned[0].detail
    assert set(MD.PINNED) >= set(MD.required(conn))  # every model a step needs has a tested version


def test_code_or_data_in_a_folder_macos_guards(tmp_path):
    """B7 (13 Oct): the lab hung ~10 h on a privacy question for ~/Downloads; install.sh refuses the same folders."""
    user = tmp_path / "me"
    (user / "Library" / "Mobile Documents" / "x").mkdir(parents=True)
    (user / "Downloads" / "octo").mkdir(parents=True)
    (user / "Developer" / "octo").mkdir(parents=True)
    assert DR.protected(user / "Downloads" / "octo", user) == "Downloads"
    assert DR.protected(user / "Library" / "Mobile Documents" / "x", user) == "iCloud Drive"
    assert DR.protected(user / "Developer" / "octo", user) is None
    assert DR.protected(user / "Downloadsish", user) is None  # a folder, not a prefix
    got = DR.places(root=user / "Downloads" / "octo", home=user / "Developer" / "octo", user=user)
    assert [(c.what, c.mark) for c in got] == [("The code's folder", DR.FAIL), ("The lab's data folder", DR.OK)]
    assert "inside Downloads" in got[0].detail and "Application Support" in got[0].fix
    assert len(DR.places(root=user / "Developer" / "octo", home=user / "Developer" / "octo", user=user)) == 1


def test_apples_command_line_tools():
    """F4: without them /usr/bin/python3, the relay's Python, is a stub; only the recorder needs it, so a warning."""
    assert DR.apple_tools(clt=lambda: "/Library/Developer/CommandLineTools")[0].mark == DR.OK
    missing = DR.apple_tools(clt=lambda: None)[0]
    assert missing.mark == DR.WARN and "xcode-select --install" in missing.fix


def test_another_program_on_the_labs_port():
    logi = "p1101\ncLogiPluginService\nn[::1]:8765\np8415\ncPython\nn127.0.0.1:8765\n"  # The owner's Mac, 19 Oct
    (w,) = DR.port(True, lsof=lambda: logi)
    assert w.mark == DR.WARN and "LogiPluginService on [::1]" in w.detail and "127.0.0.1:8765" in w.fix
    (f,) = DR.port(False, lsof=lambda: "p1\ncSomeApp\nn127.0.0.1:8765\n")
    assert f.mark == DR.FAIL and f.detail == "taken by SomeApp"
    assert DR.port(True, lsof=lambda: "p8415\ncPython\nn127.0.0.1:8765\n") == []
    # Another user's lab (install review, 5 Nov): lsof can't see it, but the port answers
    (o,) = DR.port(False, lsof=lambda: "", answers=lambda: True)
    assert o.mark == DR.FAIL and "another user" in o.detail
    assert DR.port(False, lsof=lambda: "", answers=lambda: False) == []


def test_the_firewall_only_matters_once_the_phone_is_on(tmp_path):
    said = {"--getglobalstate": "Firewall is enabled. (State = 1)", "--getblockall": "block all state set to disabled."}
    fw = lambda *a: said.get(a[0], f"Incoming connection to {a[-1]} is blocked.")
    assert DR.firewall(False, fw=fw) == []
    (c,) = DR.firewall(True, python=tmp_path / "python3.12", fw=fw)
    assert c.mark == DR.FAIL and "python3.12" in c.fix and "Allow incoming connections" in c.fix
    said["--getblockall"] = "Firewall has block all state set to enabled."
    assert "Block all incoming" in DR.firewall(True, fw=fw)[0].fix
    assert DR.firewall(True, fw=lambda *a: "Firewall is disabled. (State = 0)")[0].mark == DR.OK


def test_the_recorder_as_the_lab_last_saw_it():
    assert DR.recorder_reached(None, configured=False) == []
    assert DR.recorder_reached({"seen_at": 1.0, "error": None}, True)[0].mark == DR.OK
    (c,) = DR.recorder_reached({"seen_at": 1.0, "error": "No route to host"}, True)
    assert c.mark == DR.WARN and "Local Network" in c.fix


def test_the_app_goes_where_finder_shows_it_when_it_may(tmp_path, monkeypatch):
    from octo import launcher as LN
    system, user = tmp_path / "Applications" / "Octo.app", tmp_path / "home" / "Applications" / "Octo.app"
    system.parent.mkdir()
    monkeypatch.setattr(LN, "SYSTEM", system)
    monkeypatch.setattr(LN, "USER", user)
    assert LN.place() == system and LN.found() is None  # writable /Applications: there
    system.parent.chmod(0o555)
    try:
        assert LN.place() == user  # not writable without a password: the user's own
    finally:
        system.parent.chmod(0o755)
    user.mkdir(parents=True)
    assert LN.found() == user and LN.remove() and LN.found() is None


def test_away_from_home_only_once_the_phone_is_on_and_never_a_failure(conn, tmp_path, monkeypatch):
    from octo import phone as PH
    monkeypatch.setattr(PH, "DIR", tmp_path / "phone")
    none = {"installed": False, "running": False, "state": None, "ips": [], "phones": []}
    assert DR.away(False, conn, ts=none) == []
    (c,) = DR.away(True, conn, ts=none)
    assert c.mark == DR.WARN and "tailscale.com" in c.fix
    on = {"installed": True, "running": True, "state": "Running", "ips": ["100.101.102.103"], "account": None,
          "phones": [{"name": "iPhone", "online": True, "last_seen": None}]}
    PH.add_device(conn, "iPhone")
    (c,) = DR.away(True, conn, ts=on)
    assert c.mark == DR.OK and "not tried away yet" in c.detail
