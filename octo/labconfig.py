"""Which lab this is, and the settings that follow from it (review 8 Oct, finding 8).

Decided once per lab, as `lab_origin` in app_config: "jay" for a lab that had reviews before the product (7 Oct:
the first lab), "product" for any other. A setting that differs between the two takes its value from the table below,
one row per setting with its value for each. Before 8 Oct each setting was decided the first time the code met it,
by "has this lab had reviews?": a setting added later would have treated every buyer's lab with a review as the first lab.

A lab can still hold its own value for a setting (an app_config row of the same name), which wins: the three
decided on 7 Oct stay as they were. Nothing here reads the environment: a variable left set in a shell can't
choose Claude Code for a buyer's lab. Tests say which lab they are through TEST_OVERRIDES.
"""

from __future__ import annotations

import json

ORIGIN = "lab_origin"
JAY, PRODUCT = "jay", "product"

FLAGS: dict[str, dict[str, bool]] = {
    # Claude Code on the Mac's own login is what runs when nothing's chosen: the first lab, as before. Any lab may choose
    # it since 31 Oct (Jay, product plan P5); a new lab's owner picks it, nothing picks it for them.
    "claude_code": {JAY: True, PRODUCT: False},
    "voiceprints_need_consent": {JAY: False, PRODUCT: True},  # 4.6; the first lab keeps its 30 Sep decision (D1)
    # B29 (Jay, 23 Oct): recordings are kept, not deleted at 60 days, so the filler cue can keep learning from them.
    # A new lab keeps D5's 60 days, as the landing page says.
    "keep_recordings": {JAY: True, PRODUCT: False},
}

# Set only by tests (tests/conftest.py runs them as the first lab); empty in the lab itself.
TEST_OVERRIDES: dict[str, bool] = {}


def _get(conn, key: str):
    r = conn.execute("SELECT value FROM app_config WHERE key=?", (key,)).fetchone()
    return json.loads(r[0]) if r else None


def origin(conn) -> str:
    """"jay" or "product". A database the migration hasn't reached reads as a new lab: the safe side."""
    return _get(conn, ORIGIN) or PRODUCT


def flag(conn, name: str) -> bool:
    if name in TEST_OVERRIDES:
        return TEST_OVERRIDES[name]
    own = _get(conn, name)
    return bool(own) if own is not None else FLAGS[name][origin(conn)]


def decide(conn) -> None:
    """Once, from octo/db.py's migration (inside its transaction). A lab whose settings were decided on 7 Oct
    keeps what they said (its Claude Code row: the first lab had it on); otherwise reviews before now mean the first lab."""
    if _get(conn, ORIGIN) is not None:
        return
    cc = _get(conn, "claude_code")
    if cc is not None:
        lab = JAY if cc else PRODUCT
    else:
        lab = JAY if conn.execute("SELECT 1 FROM stage_runs WHERE stage='insight' LIMIT 1").fetchone() else PRODUCT
    conn.execute("INSERT OR IGNORE INTO app_config (key, value, updated_at) VALUES (?, ?, strftime('%s','now'))",
                 (ORIGIN, json.dumps(lab)))
