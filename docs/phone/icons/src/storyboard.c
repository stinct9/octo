// Storyboard frames for the phone app, drawn with the recorder's own primitives (octo_display.h).
// "scene_*" are proposed app compositions (octopus only, centred, no words); the rest are the recorder's
// exact screens, shown in the app as "Your recorder shows:".
#include "octo_display.h"
static void dump(const char *name, od_frame *f) { printf("%s ", name); for (int i = 0; i < 1024; i++) printf("%02x", f->fb[i]); printf("\n"); }
#define CX 64
#define CY 30
static void scene_asleep(od_frame *f, uint32_t ms) {
  od_clear(f); od_octopus(f, CX, CY, 14, OD_ASLEEP, ms * 0.0015f);
  od_text(f, CX + 16, (int)(ms / 700 % 2) ? 6 : 8, "z", 1);
}
static void scene_search(od_frame *f, uint32_t ms) {  // asleep, a sonar ripple every 1600 ms
  od_clear(f);
  uint32_t t = ms % 1600;
  if (t < 900) od_arc(f, CX, CY, 16 + (int)(52 * od_ease_out(t / 900.0f)), 0, 358);
  od_octopus(f, CX, CY, 14, OD_ASLEEP, ms * 0.0015f);
}
static void scene_found(od_frame *f, uint32_t ms) {  // wakes: pops and opens its eyes (od_anim_start's first 500 ms)
  od_clear(f);
  float t = (float)ms;
  int r = 8 + (int)lroundf(6 * od_ease_back(t / 450.0f));
  od_octopus_eyes(f, CX, CY, r, od_clamp01((t - 150) / 250.0f), 0, t * 0.004f);
}
static void scene_listening(od_frame *f, uint32_t ms) {  // recording: as on the recorder, without the words
  od_clear(f); od_octopus(f, CX, CY, 14, OD_AWAKE, ms * 0.004f);
  int waves = 1 + (int)(ms / 400 % 3);
  for (int k = 0; k < waves; k++) od_arc(f, CX, CY, 18 + 4 * k, 225, 315);
}
static void scene_connected(od_frame *f, uint32_t ms) {  // od_anim_start's octopus part: ripple, pop, eyes, waves
  od_clear(f);
  float t = (float)ms;
  if (t < 650) od_arc(f, CX, CY, 4 + (int)(80 * od_ease_out(t / 650.0f)), 0, 358);
  int r = 8 + (int)lroundf(6 * od_ease_back(t / 450.0f));
  od_octopus_eyes(f, CX, CY, r, od_clamp01((t - 250) / 250.0f), 0, t * 0.004f);
  int waves = t < 500 ? 0 : 1 + (int)((t - 500) / 150); if (waves > 3) waves = 3;
  for (int k = 0; k < waves; k++) od_arc(f, CX, CY, 18 + 4 * k, 225, 315);
}
static void scene_alert(od_frame *f, uint32_t ms) { od_clear(f); od_octopus(f, CX, CY, 14, OD_ALERT, ms * 0.01f); od_text(f, CX + 20, 4, "!", 2); }
static void mirror_code(od_frame *f, uint32_t ms) {  // od_screen_passkey with blanks: the app can't know the digits
  od_clear(f);
  od_text(f, (128 - od_text_width("Phone code", 1)) / 2, 4, "Phone code", 1);
  od_text(f, (128 - od_text_width("______", 3)) / 2, 22, "______", 3);
  od_text(f, (128 - od_text_width("type it on the phone", 1)) / 2, 52, "type it on the phone", 1);
  if (ms % 1000 < 500) od_disc(f, 122, 7, 2, 1);
}
int main(void) {
  od_frame f;
  scene_asleep(&f, 0); dump("1_first_run__asleep", &f);
  scene_search(&f, 250); dump("2_searching__ripple_250ms", &f);
  scene_search(&f, 700); dump("2_searching__ripple_700ms", &f);
  scene_found(&f, 120); dump("3_found__pop_120ms", &f);
  scene_found(&f, 450); dump("3_found__awake_450ms", &f);
  mirror_code(&f, 100); dump("4_code__recorder_shows", &f);
  scene_connected(&f, 300); dump("5_connected__300ms", &f);
  scene_connected(&f, 1100); dump("5_connected__1100ms", &f);
  scene_listening(&f, 900); dump("6_live__recording", &f);
  scene_asleep(&f, 900); dump("6_live__stopped", &f);
  scene_alert(&f, 300); dump("6_live__needs_you", &f);
  od_screen_updating(&f, 462000, 1100000, 130); dump("7_update__recorder_shows_42pct", &f);
  od_screen_updating(&f, 1100000, 1100000, 130); dump("7_update__restarting", &f);
  od_screen_update_result(&f, OD_UPD_CHECKING, "rec-15", 260); dump("7_update__checking", &f);
  od_screen_update_result(&f, OD_UPD_DONE, "rec-15", 2000); dump("7_update__updated", &f);
  od_screen_update_result(&f, OD_UPD_UNDONE, "rec-14", 2000); dump("7_update__rolled_back", &f);
  return 0;
}
