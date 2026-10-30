"""The public copy's check (scripts/public_copy.py, review 8 Oct finding 16): every text file whatever its name,
file paths too, and whole words in any case."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("public_copy", Path(__file__).resolve().parents[1] / "scripts" / "public_copy.py")
PC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(PC)


def test_every_text_file_and_every_path_in_any_case(tmp_path):
    (tmp_path / "App.xcodeproj").mkdir()
    (tmp_path / "App.xcodeproj" / "project.pbxproj").write_text("DEVELOPMENT_TEAM = ZZZ;\nwho = ASHA;\n")
    (tmp_path / ".gitignore").write_text("notes/asha/\n")
    (tmp_path / "local.xcconfig").write_text("x = 1\n")
    (tmp_path / "people").mkdir()
    (tmp_path / "people" / "asha_notes.md").write_text("nothing here\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\x00asha")  # binary: not read
    (tmp_path / "ok.py").write_text("ashanti = 1  # not the word\n")
    found = PC.scan(tmp_path, ["Asha"])
    assert sorted(found["Asha"]) == [".gitignore:1", "App.xcodeproj/project.pbxproj:2", "people/asha_notes.md:path"]
