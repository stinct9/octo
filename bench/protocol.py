"""Parse the `@@ {json}` lines the bench sketches print (see firmware/bench/libraries/BenchProto)."""

from __future__ import annotations

import json

PREFIX = "@@ "
VERSION = 1

# Keys that mark a protocol line as something other than a test's final result.
_NOT_RESULT = {"ready", "level", "confirm", "none"}


def parse(line: str) -> dict | None:
    """Return the JSON object from an `@@` line, or None for human-readable output."""
    if not line.startswith(PREFIX):
        return None
    try:
        data = json.loads(line[len(PREFIX):])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("v") != VERSION:
        return None
    return data


def is_identity(data: dict) -> bool:
    """Reply to `i`: exactly {"v":1,"sketch":...}."""
    return set(data) == {"v", "sketch"}


def is_result(data: dict) -> bool:
    """The final line of a `g` (or `r`) command: raw measurements."""
    return "sketch" in data and not (_NOT_RESULT & data.keys()) and not is_identity(data)
