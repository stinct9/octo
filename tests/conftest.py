import os
import tempfile

# Before anything imports octo: the lab's home (octo/__init__.py) is a folder of the test run's own, never the
# owner's lab, whichever copy the tests run in (14 Oct). OCTO_NEW_LAB lets a database start there.
os.environ["OCTO_HOME"] = tempfile.mkdtemp(prefix="octo-test-home-")
os.environ["OCTO_NEW_LAB"] = "1"

import pytest


@pytest.fixture(autouse=True)
def no_real_ticktick(tmp_path, monkeypatch):
    """Tests never see the owner's TickTick token (data/ticktick.json), so nothing can reach their account."""
    from octo import ticktick as TT
    monkeypatch.setattr(TT, "CONFIG", tmp_path / "ticktick-test.json")


@pytest.fixture(autouse=True)
def no_real_clips(tmp_path, monkeypatch):
    """Deletes clear clips by conversation id and the 60-day sweep walks the bench folder: in tests,
    both point into the test's own folder, never at the owner's audio."""
    from octo import privacy as PV
    monkeypatch.setattr(PV, "CLIPS", tmp_path / "clips")
    monkeypatch.setattr(PV, "BENCH", tmp_path / "bench")


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch):
    """No test can reach Claude (review 4 Oct, M25): `claude` is never found; tests that need an
    answer replace claude_code.ask themselves."""
    from octo import claude_code as CC
    monkeypatch.setattr(CC, "claude_bin", lambda: None)


@pytest.fixture(autouse=True)
def no_real_fetch(monkeypatch):
    """No test fetches this checkout's origin: the menu's "Octo update ready" asks git (octo/deploy.py)."""
    from octo import deploy as DP
    monkeypatch.setattr(DP, "update_ready", lambda repo, now=None: 0)


@pytest.fixture(autouse=True)
def no_real_iphone(tmp_path, monkeypatch):
    """No test asks this Mac's Xcode or a real iPhone (octo/iphone.py); tests that need them fake these themselves."""
    from octo import iphone as IP
    monkeypatch.setattr(IP, "INSTALLED", tmp_path / "iphone.json")
    monkeypatch.setattr(IP, "_ok", lambda cmd, timeout=30: None)
    monkeypatch.setattr(IP, "_teams", lambda: [])
    monkeypatch.setattr(IP, "_phones", lambda: [])


@pytest.fixture(autouse=True)
def no_real_app(monkeypatch):
    """No test quits the owner's Octo app: launcher.remove() and install() SIGTERM every running one
    (20 Oct: test_doctor's remove() quit Jay's app on each test run)."""
    from octo import launcher as LN
    monkeypatch.setattr(LN, "stop", lambda pids, wait_s=5: None)


@pytest.fixture(autouse=True)
def no_real_keys(tmp_path, monkeypatch):
    """Tests never see the owner's API keys (data/providers.json, octo/providers.py)."""
    from octo import providers as PV
    monkeypatch.setattr(PV, "CONFIG", tmp_path / "providers-test.json")


@pytest.fixture(autouse=True)
def as_jays_lab(monkeypatch):
    """Tests run as the owner's own lab, which may use Claude Code (product plan P5). A test of a new
    buyer's lab removes the override: monkeypatch.delitem(LC.TEST_OVERRIDES, "claude_code") (octo/labconfig.py)."""
    from octo import labconfig as LC
    monkeypatch.setattr(LC, "TEST_OVERRIDES", {"claude_code": True,
                                                "voiceprints_need_consent": False,  # every labelled voiceprint (D1)
                                                "keep_recordings": False})  # D5's 60 days, as before B29


@pytest.fixture(autouse=True)
def no_real_rubric_edits(tmp_path, monkeypatch):
    """Tests never read or write a buyer's data/rubric.md (octo/rubric.py); the owner's pipeline/rubric.md
    stays what labs without one read, as before."""
    from octo import rubric as RB
    monkeypatch.setattr(RB, "OWN", tmp_path / "data-rubric.md")


@pytest.fixture(autouse=True)
def no_real_pipeline(monkeypatch):
    """No test starts a real `octo process` (review 8 Oct, finding 3): its child runs on the lab's data (HOME/data), which in
    the live copy is the owner's lab, with the tests' environment (OCTO_CLAUDE_CODE and the rest)."""
    from octo import api as OA
    monkeypatch.setattr(OA.Processor, "_loop", lambda self: None)


@pytest.fixture(autouse=True)
def models_downloaded(monkeypatch):
    """Tests run as if every speech model were in, whatever this machine's cache holds: the runner holds steps whose
    models are still downloading (octo/models.py, missing_for). tests/test_model_fetch.py puts the real one back."""
    from octo import models as MD
    monkeypatch.setattr(MD, "missing_for", lambda conn, stage: [])


@pytest.fixture(autouse=True)
def no_mac_key(monkeypatch):
    """Tests call the Mac's routes without the Mac's key (octo/mackey.py); tests/test_mac_key.py turns it on.
    Never the real data/mac.key either."""
    from octo import phone_api as PA
    monkeypatch.setattr(PA, "REQUIRE_MAC_KEY", False)


@pytest.fixture(autouse=True)
def no_real_tailscale(monkeypatch):
    """No test asks this Mac's Tailscale (octo/phone.py tailscale_status): its peers are the owner's devices."""
    from octo import phone as PH
    monkeypatch.setattr(PH, "_ask_tailscale", lambda cli: (1, ""))


@pytest.fixture(autouse=True)
def no_real_voice_numbers(tmp_path, monkeypatch):
    """The voice numbers kept by octo/tone.py (B27) go in the test's own folder."""
    from octo import tone as T
    monkeypatch.setattr(T, "CACHE", tmp_path / "tone.json")
