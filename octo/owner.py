"""The lab's owner: who the reviews and the coach are about (product plan 4.2, 7 Oct).

Kept in the database (`app_config`, key "owner"), set on the first-run page or with `octo owner set`:

- name: what the review calls them. The speaker they confirm as themselves is still who "you" is.
- pronoun: "he", "she" or "they" (the default), for the prompts' sentences about them.
- about: one line on who they are ("a nurse who leads a ward team").
- principles_from: where their principles come from, if anywhere ("a teaching", "my own experience").
- never: advice they never want, whatever it would gain them ("taking credit for others' work"). Optional:
  their principles already rule out what they rule out.

Nothing here writes or suggests principles: those are the owner's own (CLAUDE.md, the rubric decision).
"""

from __future__ import annotations

import json
import time

FIELDS = ("name", "pronoun", "about", "principles_from", "never")
PRONOUNS = {
    "he": {"they": "he", "them": "him", "their": "his", "themself": "himself", "s": "s", "es": "es", "were": "was",
           "have": "has", "hasnt": "hasn't", "theyre": "he's"},
    "she": {"they": "she", "them": "her", "their": "her", "themself": "herself", "s": "s", "es": "es", "were": "was",
            "have": "has", "hasnt": "hasn't", "theyre": "she's"},
    "they": {"they": "they", "them": "them", "their": "their", "themself": "themself", "s": "", "es": "", "were": "were",
             "have": "have", "hasnt": "haven't", "theyre": "they're"},
}


def load(conn) -> dict:
    r = conn.execute("SELECT value FROM app_config WHERE key='owner'").fetchone()
    return json.loads(r["value"]) if r else {}


def save(conn, profile: dict) -> dict:
    """Keep the known fields, trimmed; an empty one is left out. Raises ValueError on a bad pronoun."""
    clean = {k: str(v).strip() for k, v in profile.items() if k in FIELDS and v is not None and str(v).strip()}
    if clean.get("pronoun", "they") not in PRONOUNS:
        raise ValueError("pronoun must be he, she or they")
    conn.execute("INSERT INTO app_config (key, value, updated_at) VALUES ('owner', ?, ?) "
                 "ON CONFLICT (key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                 (json.dumps(clean), time.time()))
    return clean


def forms(profile: dict) -> dict:
    """The pronoun's forms, with capitalised ones ("They", "Their") for the start of a sentence."""
    p = dict(PRONOUNS.get(profile.get("pronoun") or "they", PRONOUNS["they"]))
    p.update({k.capitalize(): v.capitalize() for k, v in list(p.items()) if k in ("they", "their")})
    return p
