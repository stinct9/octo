"""The Octo Mac app (docs/mac-app-plan.md): building it (octo/launcher.py), the doctor's checks, the lab proving it
holds the key before the app sends it, the menu's lines and handing banners to the app (octo/notify.py).
The Swift side is checked by building and opening it on a Mac (docs/learnings.md, 19 Oct)."""

import hashlib
import hmac
import plistlib
import threading
import time

import pytest
from fastapi.testclient import TestClient

from octo import doctor as DR
from octo import launcher as LN
from octo import mackey as MK
from octo import notify as N
from tests.test_mac_key import app  # noqa: F401  the Mac's key, required


@pytest.fixture(autouse=True)
def fresh_notify(monkeypatch):
    for name, value in (("_app_texts", []), ("_app_seq", 0), ("_app_got", 0), ("_app_waiting", 0), ("_app_left", 0.0),
                        ("_app_mode", "on")):
        monkeypatch.setattr(N, name, value)
    sent = []
    monkeypatch.setattr(N, "_osascript", sent.append)
    return sent


def test_the_compile_command_goes_through_xcrun_and_names_the_source(tmp_path):
    cmd = LN.compile_command(tmp_path / "a b" / "Octo")
    assert cmd[:4] == ["xcrun", "--sdk", "macosx", "swiftc"]  # bare swiftc can't find its standard library
    assert cmd[-2:] == [str(LN.SOURCE / "Octo.swift"), str(LN.SOURCE / "FillerCue.swift")]
    assert cmd[cmd.index("-o") + 1] == str(tmp_path / "a b" / "Octo")
    assert "-target" in cmd and LN.TARGET.endswith(f"{DR.MIN_MACOS}.0")


def test_the_source_hash_follows_the_source(tmp_path, monkeypatch):
    src = tmp_path / "macapp"
    src.mkdir()
    (src / "Octo.swift").write_text("// 1")
    monkeypatch.setattr(LN, "SOURCE", src)
    monkeypatch.setattr(LN, "ICON", tmp_path / "none.png")
    one = LN.source_hash()
    (src / "Octo.swift").write_text("// 2")
    assert LN.source_hash() != one


def test_nothing_about_the_owner_goes_into_the_bundle():
    plist = plistlib.loads((LN.SOURCE / "Info.plist").read_bytes())
    assert plist["CFBundleIdentifier"] == "online.octolab.Octo" and plist["LSUIElement"] is True
    text = (LN.SOURCE / "Info.plist").read_text() + (LN.SOURCE / "Octo.swift").read_text()
    assert "mac.key\"" not in (LN.SOURCE / "Info.plist").read_text() and "/Users/" not in text


def test_without_swiftc_it_falls_back_to_the_applescript_app(tmp_path, monkeypatch):
    made = []
    monkeypatch.setattr(LN, "SYSTEM", tmp_path / "A" / "Octo.app")
    monkeypatch.setattr(LN, "USER", tmp_path / "U" / "Octo.app")
    monkeypatch.setattr(LN.subprocess, "run", lambda cmd, **kw: made.append(cmd))
    LN.install(app=LN.SYSTEM, octo=tmp_path / "octo", compiler=lambda: None)
    assert made[0][0] == "osacompile" and made[0][-1] == LN.script(tmp_path / "octo")


def test_a_failed_build_keeps_the_app_that_is_there(tmp_path, monkeypatch):
    app = tmp_path / "Octo.app"
    (app / "Contents").mkdir(parents=True)
    monkeypatch.setattr(LN, "SYSTEM", app)
    monkeypatch.setattr(LN, "USER", tmp_path / "U" / "Octo.app")
    monkeypatch.setattr(LN, "build", lambda parent: (_ for _ in ()).throw(OSError("SDK not supported")))
    said = []
    assert LN.install(app=app, compiler=lambda: "swiftc", say=said.append) == app and app.exists()
    assert "couldn't build" in said[0]


def test_a_new_build_is_swapped_in_and_the_old_one_is_gone(tmp_path):
    app, new = tmp_path / "Octo.app", tmp_path / ".Octo-1.app"
    (app / "old").mkdir(parents=True)
    (new / "new").mkdir(parents=True)
    LN.swap(new, app)
    assert (app / "new").exists() and not (app / "old").exists() and sorted(p.name for p in tmp_path.iterdir()) == ["Octo.app"]


def test_the_doctor_on_the_octo_app(tmp_path):
    octo, app = tmp_path / "octo", tmp_path / "Octo.app"
    assert "octo launcher install" in DR.launcher(app=False, octo=octo)[0].fix
    script = dict(app=app, native=lambda a: False, octo=octo, compiler=lambda: None)
    assert DR.launcher(runs=lambda a: LN.script(tmp_path / "other"), **script)[0].detail == "opens another copy of Octo"
    assert DR.launcher(runs=lambda a: LN.script(octo), **script)[0].mark == DR.OK  # no swiftc: the simple one is fine
    script["compiler"] = lambda: "swiftc"
    assert DR.launcher(runs=lambda a: LN.script(octo), **script)[0].mark == DR.WARN  # it could build the full app
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"OctoSource": "abc"}))
    native = dict(app=app, native=lambda a: True)
    assert DR.launcher(verified=lambda a: False, **native)[0].detail == "its signature doesn't match its files"
    assert DR.launcher(verified=lambda a: True, stamp=lambda: "new", **native)[0].detail == "built from older source"
    assert DR.launcher(verified=lambda a: True, stamp=lambda: "abc", **native)[0].mark == DR.OK


def test_the_lab_proves_it_holds_the_key_before_the_app_sends_it(app):  # noqa: F811
    mac = TestClient(app)
    c = "0123456789abcdef0123"
    proof = mac.get("/mac-hello", params={"c": c}).json()["proof"]
    assert proof == hmac.new(MK.key().encode(), b"octo-mac-hello:" + c.encode(), hashlib.sha256).hexdigest()
    assert MK.key() not in proof and mac.get("/mac-hello", params={"c": "short"}).status_code == 404
    from octo import phone as PH
    phone = TestClient(app, base_url=f"https://192.168.1.20:{PH.PORT}")
    assert "proof" not in phone.get("/mac-hello", params={"c": c}).text  # the phone listener never answers it
    token = PH.add_device(app.state.db(), "iPhone")
    assert phone.get("/mac-hello", params={"c": c}, headers={"Cookie": f"{PH.COOKIE}={token}"}).status_code == 404


def test_the_menu_and_the_long_poll_need_the_key(app):  # noqa: F811
    mac = TestClient(app)
    assert mac.get("/api/octo/mac-app").status_code == 401
    assert mac.get("/api/octo/mac-app/notice").status_code == 401
    mac.cookies.set(MK.COOKIE, MK.key())
    body = mac.get("/api/octo/mac-app").json()
    assert body["icon"] in ("awake", "asleep", "needs") and isinstance(body["lines"], list)
    assert mac.get("/api/octo/mac-app/notice").json() == {"seq": 0, "text": None, "page": None}  # the first call says where to start
    assert mac.get("/api/octo/mac-app/notice", params={"notify": "loud"}).status_code == 400
    assert mac.post("/api/octo/settings/notify-test", json={}).json()["via"] == "app"  # it asked a moment ago


def test_an_update_is_offered_in_a_new_lab_only(app, monkeypatch):  # noqa: F811
    from octo import deploy as DP
    from octo import labconfig as LC
    mac = TestClient(app)
    mac.cookies.set(MK.COOKIE, MK.key())
    monkeypatch.setattr(DP, "update_ready", lambda repo, now=None: 3)
    texts = lambda: [x for x in mac.get("/api/octo/mac-app").json()["lines"] if x.get("action") == "update"]  # noqa: E731
    monkeypatch.setattr(LC, "origin", lambda conn: LC.PRODUCT)
    assert texts() == [{"text": "Octo update ready", "action": "update"}]
    monkeypatch.setattr(LC, "origin", lambda conn: LC.JAY)  # the first lab updates with `octo deploy`
    assert texts() == []


def test_the_menu_lines_use_notify_words_and_open_the_page():
    lines = N.menu({"review:ses_a", "confirm:ses_b", "confirm:ses_c", "coach:n1", "quiet:1000"}, now=1000 + 20 * 3600)
    assert [x["text"] for x in lines] == ["1 conversation ready to review", "2 conversations with speakers to confirm",
                                          "The coach's note is ready", "Recorder not seen for 20 h"]
    assert lines[0]["page"] == "/c/ses_a" and lines[1]["page"] == "/" and all(x["needs"] for x in lines)
    assert N.menu(set()) == []


def test_with_no_app_listening_osascript_posts(fresh_notify):
    assert N.post("2 conversations ready to review") == "osascript" and fresh_notify == ["2 conversations ready to review"]


def test_the_app_gets_the_text_and_says_so_with_its_next_request(fresh_notify):
    late = []
    assert N.app_wait("on", None) == (0, None)  # where to start
    got = []
    t = threading.Thread(target=lambda: got.append(N.app_wait("on", 0, timeout=5)))
    t.start()
    while not N._app_waiting:
        time.sleep(0.01)
    assert N.post("1 conversation ready to review", late=lambda *a: late.append(a)) == "app"
    t.join()
    assert got == [(1, "1 conversation ready to review")] and fresh_notify == []
    assert N.app_wait("on", 1, timeout=0) == (1, None)  # asking past it is the app saying it got it
    assert N.unclaimed(*late[0], send=fresh_notify.append) is False and fresh_notify == []


def test_a_request_left_by_an_app_that_quit_cant_lose_a_text(fresh_notify):
    late = []
    N.app_wait("on", None)
    dead = threading.Thread(target=lambda: N.app_wait("on", 0, timeout=1))  # nobody reads its answer
    dead.start()
    while not N._app_waiting:
        time.sleep(0.01)
    N.post("1 failed", late=lambda *a: late.append(a))
    dead.join()
    assert N.unclaimed(*late[0], send=fresh_notify.append) is True and fresh_notify == ["1 failed"]
    assert N.app_wait("on", 0, timeout=0) == (0, None)  # and the app, back, doesn't post it a second time


def test_banners_turned_off_by_the_owner_are_off_for_osascript_too(fresh_notify):
    N.app_wait("off", None)
    assert N.post("1 failed") == "off" and fresh_notify == []


def test_a_lab_that_restarted_starts_the_app_from_its_own_numbers():
    assert N.app_wait("on", 41) == (0, None)


def test_a_running_app_is_found_with_or_without_arguments(monkeypatch):
    import re
    seen = []
    monkeypatch.setattr(LN.subprocess, "run", lambda cmd, **kw: seen.append(cmd) or type("R", (), {"stdout": "12 34\n"})())
    assert LN.running() == [12, 34]
    pattern = re.compile(seen[0][-1])  # pgrep -f's pattern, an extended regex Python reads the same way
    assert pattern.search("/Applications/Octo.app/Contents/MacOS/Octo")
    assert pattern.search("/Applications/Octo.app/Contents/MacOS/Octo --background")  # reopened after an update
    assert not pattern.search("/Applications/Octo.app/Contents/MacOS/OctoHelper")
