"""The recorder's OLED screens (firmware/recorder/octo_display.h), compiled on the Mac and drawn
into frames: every screen draws, stays inside 128x64, and shows what it should. `render()` writes
them as PNGs to look at (python -m tests.test_display OUT_DIR)."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = r"""
#include "octo_display.h"
static void dump(const char *name, od_frame *f) { printf("%s ", name); for (int i = 0; i < 1024; i++) printf("%02x", f->fb[i]); printf("\n"); }
int main(void) {
  od_frame f;
  od_screen_recording(&f, 754, 300); dump("recording", &f);
  od_screen_recording(&f, 0, 1200); dump("recording_start", &f);
  od_screen_recording(&f, 7380, 800); dump("recording_long", &f);
  od_screen_stopped(&f, 0); dump("stopped", &f);
  od_screen_attention(&f, "No SD card", 0); dump("attention", &f);
  od_screen_hold(&f, 600, 1000, 0); dump("hold_start", &f);
  od_screen_hold(&f, 1250, 1000, 1); dump("hold_stop", &f);
  od_screen_hold(&f, 9000, 1000, 1); dump("hold_long", &f);
  od_screen_hello(&f, 0); dump("hello", &f);
  od_screen_setup(&f, 0); dump("setup", &f);
  od_screen_passkey(&f, 42519, 0); dump("passkey", &f);
  od_ambient_recording(&f, 754, 0); dump("ambient_recording", &f);
  od_ambient_recording(&f, 754, 3 * 60000); dump("ambient_recording_shifted", &f);
  od_ambient_recording(&f, 754, 500); dump("ambient_recording_dot_off", &f);
  od_screen_filler(&f, "you know", 1, 0); dump("filler_1", &f);
  od_screen_filler(&f, "you know", 128, 0); dump("filler_128", &f);
  od_screen_filler(&f, "uh", 3, 0); dump("filler_uh", &f);
  od_screen_filler(&f, "essentially", 3, 0); dump("filler_long", &f);
  od_screen_recording(&f, 754, 300); od_filler_count(&f, OD_ON_RECORDING, "you know", 7, 300); dump("filler_count_recording", &f);
  od_ambient_recording(&f, 754, 0); od_filler_count(&f, OD_ON_AMBIENT_RECORDING, "you know", 7, 0); dump("ambient_filler_count", &f);
  od_ambient_recording(&f, 754, 3 * 60000); od_filler_count(&f, OD_ON_AMBIENT_RECORDING, "you know", 1234, 3 * 60000); dump("ambient_filler_count_shifted", &f);
  od_ambient_recording(&f, 754, 0); od_filler_count(&f, OD_ON_AMBIENT_RECORDING, "you know", 0, 0); dump("ambient_filler_none", &f);
  od_screen_mark(&f, 0); dump("mark", &f);
  od_ambient_stopped(&f, 0); dump("ambient_stopped", &f);
  for (int t = 0; t <= OD_ANIM_MS; t += 50) { char n[32]; snprintf(n, sizeof n, "start_%04d", t); od_anim_start(&f, t); dump(n, &f); }
  for (int t = 0; t <= OD_ANIM_MS; t += 50) { char n[32]; snprintf(n, sizeof n, "stop_%04d", t); od_anim_stop(&f, t); dump(n, &f); }
  od_screen_updating(&f, 412000, 1100000, 0); dump("updating", &f);
  od_screen_updating(&f, 1100000, 1100000, 300); dump("restarting", &f);
  od_screen_update_result(&f, OD_UPD_CHECKING, "rec-10", 400); dump("update_checking", &f);
  od_screen_update_result(&f, OD_UPD_DONE, "rec-10", 100); dump("update_done_0100", &f);
  od_screen_update_result(&f, OD_UPD_DONE, "rec-10", 2000); dump("update_done", &f);
  od_screen_update_result(&f, OD_UPD_FAILED, "rec-10", 0); dump("update_failed", &f);
  od_screen_update_result(&f, OD_UPD_UNDONE, "rec-10", 0); dump("update_undone", &f);
  static const char *kinds[] = {"none", "copying", "waiting", "all_on"};
  for (int k = 0; k < 4; k++) for (int m = 0; m < 2; m++) {
    uint32_t ms = m ? 2 * 60000 + 300 : 300, minutes = m ? 3000 : 42;  // walked, the widest words
    char n[48];
    od_screen_recording(&f, 6000, ms); od_sync_status(&f, OD_ON_RECORDING, k, minutes, ms); snprintf(n, sizeof n, "sync_recording_%s_%d", kinds[k], m); dump(n, &f);
    od_screen_stopped(&f, ms); od_sync_status(&f, OD_ON_STOPPED, k, minutes, ms); snprintf(n, sizeof n, "sync_stopped_%s_%d", kinds[k], m); dump(n, &f);
    od_ambient_recording(&f, 6000, ms); od_sync_status(&f, OD_ON_AMBIENT_RECORDING, k, minutes, ms); snprintf(n, sizeof n, "sync_ambient_recording_%s_%d", kinds[k], m); dump(n, &f);
    od_ambient_stopped(&f, ms); od_sync_status(&f, OD_ON_AMBIENT_STOPPED, k, minutes, ms); snprintf(n, sizeof n, "sync_ambient_stopped_%s_%d", kinds[k], m); dump(n, &f);
  }
  return 0;
}
"""


def frames(tmp: Path) -> dict[str, list[list[int]]]:
    (tmp / "d.c").write_text(HARNESS)
    exe = tmp / "d"
    subprocess.run(["cc", "-O1", "-I", str(ROOT / "firmware" / "recorder"), str(tmp / "d.c"), "-o", str(exe), "-lm"], check=True)
    out = {}
    for line in subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.split("\n"):
        if not line:
            continue
        name, hexs = line.split()
        fb = bytes.fromhex(hexs)
        out[name] = [[fb[(y // 8) * 128 + x] >> (y % 8) & 1 for x in range(128)] for y in range(64)]
    return out


@pytest.mark.skipif(not shutil.which("cc"), reason="no C compiler")
def test_every_screen_draws_something_and_says_what_it_should(tmp_path):
    f = frames(tmp_path)
    assert set(f) >= {"recording", "recording_long", "stopped", "attention", "hold_start", "hold_stop", "hold_long", "hello"}
    for name, px in f.items():
        lit = sum(map(sum, px))
        low = 20 if name.startswith(("start_", "stop_")) else 60 if name.startswith("ambient") else 150
        assert low < lit < 128 * 64 * 0.6, name  # something on it, not a white screen
    # Always on: far fewer pixels lit than the full screens, and moved after a few minutes (burn-in).
    lit = lambda name: sum(map(sum, f[name]))
    assert lit("ambient_recording") < lit("recording") / 2 and lit("ambient_stopped") < lit("stopped") * 0.8
    assert f["ambient_recording"] != f["ambient_recording_shifted"]
    # REC's dot blinks on the dim screen too (20 Oct): only the dot goes, and REC is size 2 (taller than 7 px).
    on, off = f["ambient_recording"], f["ambient_recording_dot_off"]
    assert all(b <= a for ra, rb in zip(on, off) for a, b in zip(ra, rb)) and 40 < lit("ambient_recording") - lit("ambient_recording_dot_off") < 80
    assert any(off[9][55:90]) and any(off[22][55:90]) and not any(off[23][55:90])  # rows 9-22: 14 px tall
    # Start and stop animations: every frame draws, and they end where their screens begin.
    for k in [n for n in f if n.startswith(("start_", "stop_"))]:
        assert sum(map(sum, f[k])) > 20, k
    # The words and their places on the right; the tentacles on the left are always moving anyway.
    same = lambda a, b: sum(x != y for ra, rb in zip(f[a], f[b]) for x, y in zip(ra[48:], rb[48:]))
    assert same("start_1200", "recording_start") < 20
    assert same("stop_1200", "stopped") < 20
    # The bar fills to 2 s, then stays full however long the hold: one press, one action (no power off).
    bar = lambda px: sum(px[47][x] for x in range(10, 118)) / 108
    assert abs(bar(f["hold_start"]) - 0.6) < 0.03 and bar(f["hold_stop"]) == 1 and f["hold_long"] == f["hold_stop"]
    # After an update: the four outcomes look different, the tick draws itself, and nothing runs off the
    # right edge (the last column stays dark: the longest words end before it).
    outcomes = ["update_checking", "update_done", "update_failed", "update_undone"]
    assert len({str(f[n]) for n in outcomes}) == 4
    assert lit("update_done_0100") < lit("update_done")
    for n in outcomes:
        assert not any(row[127] for row in f[n]), n
    # Not paired yet, and a phone's pairing code (product plan 4.4): text fits; the code is big.
    assert not any(row[127] for row in f["setup"])
    assert sum(sum(row[20:108]) for row in f["passkey"][22:43]) > 250  # the six digits, at size 3
    # The Mac's copy (20 Oct): a line that only adds to its screen, never REC's place, never past the edge, 2 px
    # clear of the time above it, and nothing on the dim screens when all is on the Mac.
    for layout in ("recording", "stopped", "ambient_recording", "ambient_stopped"):
        for m in (0, 1):
            base = f[f"sync_{layout}_none_{m}"]
            for kind in ("copying", "waiting", "all_on"):
                px = f[f"sync_{layout}_{kind}_{m}"]
                assert all(b <= p for rb, rp in zip(base, px) for b, p in zip(rb, rp)), (layout, kind, m)
                assert not any(row[127] for row in px), (layout, kind, m)
                if layout.startswith("ambient") and kind == "all_on":
                    assert px == base
                else:
                    assert px != base, (layout, kind, m)
    for kind in ("copying", "waiting", "all_on"):
        px = f[f"sync_recording_{kind}_0"]
        assert not any(px[54]) and not any(px[55]), kind  # under the time, over the footer
        assert px[:32] == f["sync_recording_none_0"][:32], kind  # REC untouched


def test_the_filler_cue_fits_and_only_adds(tmp_path):
    """B20 (rec-24): the flash says it big and fits; the count only adds to its screen, never REC's place, stays on
    the screen when the dim layout walks, and a count of 0 draws nothing."""
    f = frames(tmp_path)
    for n in ("filler_1", "filler_128", "filler_uh", "filler_long"):
        assert not any(row[127] for row in f[n]) and not any(row[0] for row in f[n]), n
    for n in ("filler_1", "filler_128", "filler_uh"):
        assert sum(sum(row) for row in f[n][14:28]) > 40, n  # the name at size 2 (rec-27: any filler's)
    assert sum(sum(row) for row in f["filler_long"][18:26]) > 40 and not any(any(r) for r in f["filler_long"][27:39])
    for name, base in (("filler_count_recording", "recording"), ("ambient_filler_count", "ambient_recording"),
                       ("ambient_filler_count_shifted", "ambient_recording_shifted")):
        px, b = f[name], f[base]
        assert all(x <= y for rb, rp in zip(b, px) for x, y in zip(rb, rp)), name
        assert px != b and not any(row[127] for row in px), name
        assert px[:32] == b[:32], name  # REC and the octopus untouched
    assert any(f["ambient_filler_count_shifted"][60]) or any(f["ambient_filler_count_shifted"][59]), "walked down, still drawn"
    assert f["ambient_filler_none"] == f["ambient_recording"]


def test_the_mark_flag_fits(tmp_path):
    """B16 (rec-26): a double tap's flag and "Marked" fit the screen and say it at size 2."""
    px = frames(tmp_path)["mark"]
    assert not any(row[127] for row in px) and not any(row[0] for row in px)
    assert sum(sum(row) for row in px[25:39]) > 150  # "Marked" at size 2
    assert sum(sum(row) for row in px[18:46]) > sum(sum(row) for row in px[25:39])  # and the flag beside it


def render(out: Path) -> None:
    from PIL import Image
    import tempfile
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        for name, px in frames(Path(tmp)).items():
            img = Image.new("RGB", (128, 64))
            img.putdata([(150, 220, 255) if v else (8, 10, 14) for row in px for v in row])
            img.resize((512, 256), Image.NEAREST).save(out / f"{name}.png")


if __name__ == "__main__":
    render(Path(sys.argv[1]))
