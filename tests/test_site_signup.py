"""The sign-up endpoint's checks (security review L7), run by node when it's here; and signups.py's output as text."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parents[1] / "site"
# A public copy leaves site/ out (public-copy.json), and its tests must pass to release (review 10 Nov).
if not SITE.is_dir():
    pytest.skip("no site/ (a public copy)", allow_module_level=True)


@pytest.mark.skipif(not shutil.which("node"), reason="no node")
def test_the_endpoint_keeps_only_well_formed_sign_ups():
    r = subprocess.run(["node", "--test", "tests/signup.test.mjs"], cwd=SITE, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_signups_print_as_text():
    sys.path.insert(0, str(SITE))
    try:
        from signups import plain
    finally:
        sys.path.remove(str(SITE))
    assert plain("Sam\x1b[31m") == "Sam [31m"
    assert plain('=HYPERLINK("x")') == "'=HYPERLINK(\"x\")" and plain("@SUM(1)") == "'@SUM(1)"
    assert plain("Sam Lee") == "Sam Lee"
