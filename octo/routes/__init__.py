"""The lab's routes, one module per feature, each with `add(router, lab)`; octo/api.py's make_router imports and calls
them in this order (order matters where a literal path and a {param} one could both match, e.g. /sessions/changed).
Named, not imported here: the modules import octo.api, which imports octo.routes.common."""

ROUTES: tuple[str, ...] = ("conversations", "speakers", "conversation", "people", "prepare", "home", "commitments", "coach", "ask",
                          "talk", "rehearse", "drills", "fillers", "calendar", "device", "settings", "setup", "pipeline", "prefs")
