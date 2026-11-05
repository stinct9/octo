"""The layers octo/ is built in, and a check that no new import goes up them (architecture review 5 Nov, risk 8).

40 of 97 modules were one import cycle, held apart by imports inside functions; an import error then shows only when that
code runs. The layers, lowest first (docs/architecture.md):

  0 base         the database, files, audio, the model clients: nothing of the lab's own logic
  1 stages       the stage functions (pure, run in a worker) and what they're made of
  2 read models  what the pages read: a conversation, its runs and holds (runs.py), fillers, people, parts …
  3 services     what does work and writes: the pipeline and its planner, the coach, Ask, sync, delete and Forget …
  4 entry        the routes, the CLI, the server

A module may import its own layer and those below. ALLOWED is every upward import there was when the check came in
(inline imports count): it may only shrink. Moving one down, delete its line here, or the test says it's gone."""

import ast
import importlib
from pathlib import Path

import pytest

OCTO = Path(__file__).resolve().parent.parent / "octo"

LAYERS = {
    0: "octo db frame files labconfig audio asr calls devicecrypto mackey launcher owner pages relay watchdog hindi scripted "
       "invite claude_code providers ticktick aside prune marks routes routes.common",
    1: "stages insight review_any rubric insight_eval corrections",
    2: "runs sessions fillers person parts review split questions delivery tone focus solved measures intention events month search "
       "week eta models settings",
    3: "pipeline planning coach ask talk rehearse prepare drills commitments privacy voice notify device filler_loop deploy "
       "login_item backup beta doctor export uninstall iphone phone sample worker",
    4: "api cli phone_api",
}
LAYER = {m: n for n, names in LAYERS.items() for m in names.split()}

# Upward imports on 5 Nov, after the first cycle (sessions <-> pipeline <-> planning) was taken apart. Shrink only.
ALLOWED = {
    ("eta", "planning"),  # the forecast asks the planner whether a review is ready
    ("events", "notify"), ("events", "prepare"),  # the calendar's banner, and the plan it opens
    ("insight", "parts"),  # a stage's helpers reading a discussion's parts
    ("intention", "planning"),
    ("parts", "ask"), ("parts", "coach"), ("parts", "prepare"), ("parts", "talk"),  # deleting a part drops what read it
    ("person", "prepare"),
    ("review", "planning"),
    ("sessions", "commitments"), ("sessions", "prepare"), ("sessions", "privacy"),  # the page's blocks from services
    ("settings", "login_item"),  # choosing a local model starts Ollama's login item, installed only while one is chosen
    ("stages", "commitments"), ("stages", "planning"), ("stages", "voice"),  # the registry names the planner's inputs
    ("week", "commitments"),
}


def _modules() -> dict[str, Path]:
    out = {}
    for f in sorted(OCTO.rglob("*.py")):
        parts = f.relative_to(OCTO.parent).with_suffix("").parts
        name = ".".join(parts[1:-1] if parts[-1] == "__init__" else parts[1:]) or "octo"
        out[name] = f
    return out


def _layer(name: str) -> int:
    return LAYER.get(name, 4 if name.startswith("routes.") else -1)


def _imports(name: str, f: Path, known: set[str]) -> set[str]:
    """Modules of octo/ this one imports, at the top or inside a function."""
    pkg = name.rsplit(".", 1)[0] if "." in name and f.name != "__init__.py" else ("" if f.name != "__init__.py" else name)
    pkg = "" if pkg == "octo" else pkg
    out = set()
    for n in ast.walk(ast.parse(f.read_text())):
        if isinstance(n, ast.ImportFrom) and n.level:
            base = pkg.split(".")[:len(pkg.split(".")) - (n.level - 1)] if pkg else []
            mod = ".".join([*base, *(n.module.split(".") if n.module else [])])
            for a in n.names:
                sub = f"{mod}.{a.name}" if mod else a.name
                out.add(sub if sub in known else mod or "octo")
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in ([n.module] if isinstance(n, ast.ImportFrom) else [a.name for a in n.names]):
                if a and a.startswith("octo."):
                    out.add(a[5:])
    return {m for m in out if m in known and m != name and m != "octo"}


def upward() -> set[tuple[str, str]]:
    mods = _modules()
    return {(m, t) for m, f in mods.items() for t in _imports(m, f, set(mods)) if _layer(m) < _layer(t)}


def test_every_module_has_a_layer():
    assert [m for m in _modules() if _layer(m) < 0] == []


def test_no_new_import_goes_up_a_layer():
    now = upward()
    assert sorted(now - ALLOWED) == [], "an import up a layer: move it down, or (last resort) list it in ALLOWED"
    assert sorted(ALLOWED - now) == [], "gone: delete it from ALLOWED"


@pytest.mark.parametrize("name", sorted(_modules()))
def test_every_module_imports(name):
    """At load time, so an import error in a rarely run path shows here, not in front of the owner."""
    importlib.import_module("octo" if name == "octo" else f"octo.{name}")
