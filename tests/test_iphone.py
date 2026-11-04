"""`octo phone install` (octo/iphone.py) picks the team and the iPhone."""

import json
import time

import pytest

from octo import iphone as IP

PREFS = {"IDEProvisioningTeamByIdentifier": {"acct-1": [
    {"teamID": "ABCDE12345", "teamName": "A Tester (Personal Team)", "isFreeProvisioningTeam": 1}]}}


def device(name="Her iPhone", platform="iOS", pairing="paired", tunnel="connected", dev_mode="enabled", ident="CD-1"):
    return {"identifier": ident, "hardwareProperties": {"platform": platform, "udid": f"udid-{ident}"},
            "deviceProperties": {"name": name, "developerModeStatus": dev_mode},
            "connectionProperties": {"pairingState": pairing, "tunnelState": tunnel}}


def test_the_team_signed_in_to_xcode_and_its_own_bundle_id():
    t = IP.pick_team(IP.teams(PREFS), None)
    assert t == {"id": "ABCDE12345", "name": "A Tester (Personal Team)", "free": True}
    assert IP.bundle_id(t["id"]) == "app.octo.recorder.abcde12345"
    with pytest.raises(IP.PhoneError, match="Settings → Accounts"):
        IP.pick_team(IP.teams({}), None)
    two = {"IDEProvisioningTeamByIdentifier": {**PREFS["IDEProvisioningTeamByIdentifier"],
                                               "acct-2": [{"teamID": "ZZZ", "teamName": "Other"}]}}
    with pytest.raises(IP.PhoneError, match="--team"):
        IP.pick_team(IP.teams(two), None)
    assert IP.pick_team(IP.teams(two), "ZZZ")["name"] == "Other"


def test_the_iphone_paired_with_developer_mode_on():
    listed = {"result": {"devices": [device(), device("A Watch", platform="watchOS", ident="W")]}}
    found = IP.iphones(listed)
    assert [d["name"] for d in found] == ["Her iPhone"]
    assert IP.pick_iphone(found, None)["udid"] == "udid-CD-1"
    with pytest.raises(IP.PhoneError, match="Developer Mode"):
        IP.pick_iphone(IP.iphones({"result": {"devices": [device(dev_mode="disabled")]}}), None)
    with pytest.raises(IP.PhoneError, match="Trust"):
        IP.pick_iphone(IP.iphones({"result": {"devices": [device(pairing="unpaired")]}}), None)
    with pytest.raises(IP.PhoneError, match="No iPhone"):
        IP.pick_iphone([], None)
    # two known, one here: the one here; neither here: say which
    two = IP.iphones({"result": {"devices": [device("Old", tunnel="disconnected", ident="A"), device("New", ident="B")]}})
    assert IP.pick_iphone(two, None)["name"] == "New"
    assert IP.pick_iphone(two, "Old")["name"] == "Old"
    with pytest.raises(IP.PhoneError, match="--device"):
        IP.pick_iphone([dict(d, reachable=False) for d in two], None)


# ---- Set up Octo's iPhone step: each part checked, in order; the first not done stops the checks ----------------------

class Done:
    def __init__(self, code=0, out=""):
        self.returncode, self.stdout, self.stderr = code, out, ""


@pytest.fixture
def mac(tmp_path, monkeypatch):
    """A Mac with Xcode set up, an Apple ID in it and the iPhone plugged in; each test takes one part away."""
    xcode = tmp_path / "Xcode.app/Contents/Developer"
    (xcode / "usr/bin").mkdir(parents=True)
    (xcode / "usr/bin/xcodebuild").write_text("")
    monkeypatch.setattr(IP, "XCODE", xcode)
    monkeypatch.setattr(IP, "INSTALLED", tmp_path / "iphone.json")
    state = {"first_launch": 0, "sdks": "iOS 27.1 -sdk iphoneos27.1", "teams": IP.teams(PREFS), "phones": [device()]}
    monkeypatch.setattr(IP, "_ok", lambda cmd, timeout=30: Done(state["first_launch"]) if "-checkFirstLaunchStatus" in cmd
                        else Done(0, state["sdks"]))
    monkeypatch.setattr(IP, "_teams", lambda: state["teams"])
    monkeypatch.setattr(IP, "_phones", lambda: IP.iphones({"result": {"devices": state["phones"]}}))
    return state


def first_not_done(**kw):
    return next((p["id"] for p in IP.steps(**kw) if not p["done"]), None)


def test_each_part_names_the_next_thing_to_do(mac, monkeypatch):
    assert first_not_done() == "install"
    IP.INSTALLED.write_text(json.dumps({"device": "CD-1", "app_id": "a.b", "expires": time.time() + 86400}))
    assert first_not_done() == "trust"
    assert first_not_done(trusted=True) is None
    for part, change in [("developer_mode", {"phones": [device(dev_mode="disabled")]}),
                         ("phone", {"phones": [device(pairing="unpaired")]}), ("phone", {"phones": []}),
                         ("apple_id", {"teams": []}), ("ios", {"sdks": "macOS 27 -sdk macosx27"}),
                         ("first_launch", {"first_launch": 1})]:
        mac.update(change)
        assert first_not_done() == part, part
    monkeypatch.setattr(IP, "XCODE", IP.XCODE.parent / "none")
    steps = IP.steps()
    assert steps[0]["id"] == "xcode" and not any(p["done"] for p in steps)  # nothing after it is asked
    assert all(p["do"] for p in steps)


def test_an_expired_install_is_put_on_again(mac):
    IP.INSTALLED.write_text(json.dumps({"device": "CD-1", "app_id": "a.b", "expires": time.time() - 60, "trusted": True}))
    assert first_not_done() == "install"


def test_trust_is_read_from_opening_the_app(mac, monkeypatch):
    IP.INSTALLED.write_text(json.dumps({"device": "CD-1", "app_id": "a.b", "expires": time.time() + 86400}))
    said = Done(1, "")
    said.stderr = "The application could not be launched: its profile has not been explicitly trusted by the user."
    monkeypatch.setattr(IP, "_ok", lambda cmd, timeout=30: said)
    assert IP.open_on_phone() == (False, "your iPhone doesn't trust it yet") and not IP.installed()["trusted"]
    said.stderr = "Error 4016: device not reachable"
    assert "didn't answer" in IP.open_on_phone()[1]
    monkeypatch.setattr(IP, "_ok", lambda cmd, timeout=30: Done(0))
    assert IP.open_on_phone() == (True, "") and IP.installed()["trusted"]


def test_renewed_two_days_before_it_stops_opening_and_reminded_when_it_cant(mac, monkeypatch):
    now = 1_000_000.0
    started = []
    job = type("J", (), {"running": False, "error": None, "start": lambda self, **kw: started.append(kw) or True})()
    IP.INSTALLED.write_text(json.dumps({"device": "CD-1", "app_id": "a.b", "team": {"id": "T"}, "expires": now + 3 * 86400}))
    assert not IP.renew(job, now) and not started  # 3 days left: not yet
    assert IP.renew(job, now + 86400 + 1) and started == [{"device": "CD-1", "team": "T", "bundle": "a.b"}]
    assert not IP.renew(job, now + 86400 + 3600)  # tried an hour ago: not again for 6 hours
    posted = []
    monkeypatch.setattr("octo.notify.post", lambda text, page=None: posted.append(text))
    job.error = "No iPhone found"
    IP.renew(job, now + 2.5 * 86400)
    assert posted and "within a day" in posted[0] and len(started) == 2
    IP.renew(job, now + 2.6 * 86400)
    assert len(posted) == 1  # once a day


def test_step_six_is_done_from_evidence_however_the_app_got_there():
    now = 10 * 86400.0
    assert IP.setup_step(None, [], now) == {"done": False, "note": None}
    # Installed by hand from Xcode: the lab has no record of it, but the paired app reached the lab today.
    by_hand = [{"id": "dev_1", "paired_at": now - 3 * 86400, "last_used_at": now - 3600}]
    assert IP.setup_step(None, by_hand, now) == {"done": True, "note": None}
    # Installed by the lab and opened once: done before it's even paired.
    have = {"phone": "Her iPhone", "trusted": True, "expires": now + 5 * 86400}
    assert IP.setup_step(have, [], now) == {"done": True, "note": None}
    # Stale evidence stays ticked and says why, in plain words.
    gone = IP.setup_step({**have, "expires": now - 86400}, [], now)
    assert gone["done"] and "stopped opening" in gone["note"]
    quiet = IP.setup_step(None, [{"id": "dev_1", "paired_at": 0.0, "last_used_at": 86400.0}], now)
    assert quiet["done"] and "hasn't reached this lab since" in quiet["note"]
    # An expired lab install, but the app (put on again by hand) reached the lab since: fresh.
    assert IP.setup_step({**have, "expires": now - 86400}, by_hand, now)["note"] is None
