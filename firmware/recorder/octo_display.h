// The expansion board's OLED (SSD1306, 128x64): what the recorder shows (5 Oct). Drawing only,
// into a 1 KB frame: plain C, so the tests compile it on the Mac and render every screen to an image
// (tests/test_display.py). recorder.ino sends the frame over I2C.
//
// Apple's habits, as for the light: on only for a few seconds after something happens (a tap shows
// it again), so it's discreet, saves the battery and doesn't burn in; few words; one mascot.
#pragma once
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>

#define OD_W 128
#define OD_H 64

typedef struct { uint8_t fb[OD_W * OD_H / 8]; } od_frame;  // SSD1306 layout: 8 pages of 8 rows

static void od_clear(od_frame *f) { memset(f->fb, 0, sizeof f->fb); }

static void od_px(od_frame *f, int x, int y, int on) {
  if (x < 0 || y < 0 || x >= OD_W || y >= OD_H) return;
  uint8_t *b = &f->fb[(y / 8) * OD_W + x];
  if (on) *b |= 1 << (y & 7); else *b &= ~(1 << (y & 7));
}

static void od_fill(od_frame *f, int x, int y, int w, int h, int on) {
  for (int j = y; j < y + h; j++) for (int i = x; i < x + w; i++) od_px(f, i, j, on);
}

static void od_rect(od_frame *f, int x, int y, int w, int h) {
  for (int i = x; i < x + w; i++) { od_px(f, i, y, 1); od_px(f, i, y + h - 1, 1); }
  for (int j = y; j < y + h; j++) { od_px(f, x, j, 1); od_px(f, x + w - 1, j, 1); }
}

static void od_disc(od_frame *f, int cx, int cy, int r, int on) {
  for (int j = -r; j <= r; j++) for (int i = -r; i <= r; i++) if (i * i + j * j <= r * r + r / 2) od_px(f, cx + i, cy + j, on);
}

// An arc from angle a0 to a1 (degrees, 0 = right, 90 = down).
static void od_arc(od_frame *f, int cx, int cy, int r, int a0, int a1) {
  for (int a = a0; a <= a1; a += 2) od_px(f, cx + (int)lroundf(r * cosf(a * 0.0174533f)), cy + (int)lroundf(r * sinf(a * 0.0174533f)), 1);
}

// 5x8 font (after the classic GLCD font), ASCII 32..122; columns, bit 0 at the top.
static const uint8_t OD_FONT[][5] = {
  {0,0,0,0,0},{0,0,0x5F,0,0},{0,7,0,7,0},{0x14,0x7F,0x14,0x7F,0x14},{0x24,0x2A,0x7F,0x2A,0x12},{0x23,0x13,0x08,0x64,0x62},
  {0x36,0x49,0x55,0x22,0x50},{0,5,3,0,0},{0,0x1C,0x22,0x41,0},{0,0x41,0x22,0x1C,0},{0x14,0x08,0x3E,0x08,0x14},{0x08,0x08,0x3E,0x08,0x08},
  {0,0x50,0x30,0,0},{0x08,0x08,0x08,0x08,0x08},{0,0x60,0x60,0,0},{0x20,0x10,0x08,0x04,0x02},
  {0x3E,0x51,0x49,0x45,0x3E},{0,0x42,0x7F,0x40,0},{0x42,0x61,0x51,0x49,0x46},{0x21,0x41,0x45,0x4B,0x31},{0x18,0x14,0x12,0x7F,0x10},
  {0x27,0x45,0x45,0x45,0x39},{0x3C,0x4A,0x49,0x49,0x30},{0x01,0x71,0x09,0x05,0x03},{0x36,0x49,0x49,0x49,0x36},{0x06,0x49,0x49,0x29,0x1E},
  {0,0x36,0x36,0,0},{0,0x56,0x36,0,0},{0x08,0x14,0x22,0x41,0},{0x14,0x14,0x14,0x14,0x14},{0,0x41,0x22,0x14,0x08},{0x02,0x01,0x51,0x09,0x06},
  {0x32,0x49,0x79,0x41,0x3E},
  {0x7E,0x11,0x11,0x11,0x7E},{0x7F,0x49,0x49,0x49,0x36},{0x3E,0x41,0x41,0x41,0x22},{0x7F,0x41,0x41,0x22,0x1C},{0x7F,0x49,0x49,0x49,0x41},
  {0x7F,0x09,0x09,0x09,0x01},{0x3E,0x41,0x49,0x49,0x7A},{0x7F,0x08,0x08,0x08,0x7F},{0,0x41,0x7F,0x41,0},{0x20,0x40,0x41,0x3F,0x01},
  {0x7F,0x08,0x14,0x22,0x41},{0x7F,0x40,0x40,0x40,0x40},{0x7F,0x02,0x0C,0x02,0x7F},{0x7F,0x04,0x08,0x10,0x7F},{0x3E,0x41,0x41,0x41,0x3E},
  {0x7F,0x09,0x09,0x09,0x06},{0x3E,0x41,0x51,0x21,0x5E},{0x7F,0x09,0x19,0x29,0x46},{0x46,0x49,0x49,0x49,0x31},{0x01,0x01,0x7F,0x01,0x01},
  {0x3F,0x40,0x40,0x40,0x3F},{0x1F,0x20,0x40,0x20,0x1F},{0x3F,0x40,0x38,0x40,0x3F},{0x63,0x14,0x08,0x14,0x63},{0x07,0x08,0x70,0x08,0x07},
  {0x61,0x51,0x49,0x45,0x43},
  {0,0x7F,0x41,0x41,0},{0x02,0x04,0x08,0x10,0x20},{0,0x41,0x41,0x7F,0},{0x04,0x02,0x01,0x02,0x04},{0x40,0x40,0x40,0x40,0x40},{0,1,2,4,0},
  {0x20,0x54,0x54,0x54,0x78},{0x7F,0x48,0x44,0x44,0x38},{0x38,0x44,0x44,0x44,0x20},{0x38,0x44,0x44,0x48,0x7F},{0x38,0x54,0x54,0x54,0x18},
  {0x08,0x7E,0x09,0x01,0x02},{0x18,0xA4,0xA4,0xA4,0x7C},{0x7F,0x08,0x04,0x04,0x78},{0,0x44,0x7D,0x40,0},{0x40,0x80,0x84,0x7D,0},
  {0x7F,0x10,0x28,0x44,0},{0,0x41,0x7F,0x40,0},{0x7C,0x04,0x18,0x04,0x78},{0x7C,0x08,0x04,0x04,0x78},{0x38,0x44,0x44,0x44,0x38},
  {0xFC,0x24,0x24,0x24,0x18},{0x18,0x24,0x24,0x18,0xFC},{0x7C,0x08,0x04,0x04,0x08},{0x48,0x54,0x54,0x54,0x20},{0x04,0x3F,0x44,0x40,0x20},
  {0x3C,0x40,0x40,0x20,0x7C},{0x1C,0x20,0x40,0x20,0x1C},{0x3C,0x40,0x30,0x40,0x3C},{0x44,0x28,0x10,0x28,0x44},{0x1C,0xA0,0xA0,0xA0,0x7C},
  {0x44,0x64,0x54,0x4C,0x44},
};

// Text at (x, y), each font pixel `s` pixels square. Returns the x after it.
static int od_text(od_frame *f, int x, int y, const char *t, int s) {
  for (; *t; t++) {
    int c = *t < 32 || *t > 122 ? '?' : *t;
    const uint8_t *g = OD_FONT[c - 32];
    for (int col = 0; col < 5; col++)
      for (int row = 0; row < 8; row++)
        if (g[col] >> row & 1) od_fill(f, x + col * s, y + row * s, s, s, 1);
    x += 6 * s;
  }
  return x;
}

static int od_text_width(const char *t, int s) { return (int)strlen(t) * 6 * s - s; }

// ---- the octopus ----------------------------------------------------------------------------

enum od_mood { OD_AWAKE, OD_ASLEEP, OD_ALERT };

// A round head with a little body, two eyes, six tentacles that wave with `phase` (radians).
// `open`: 0 closed (asleep) .. 1 wide open; the animations open and close them by degrees.
static void od_octopus_eyes(od_frame *f, int cx, int cy, int r, float open, int alert, float phase);

static void od_octopus(od_frame *f, int cx, int cy, int r, int mood, float phase) {
  od_octopus_eyes(f, cx, cy, r, mood == OD_ASLEEP ? 0.0f : 1.0f, mood == OD_ALERT, phase);
}

static void od_octopus_eyes(od_frame *f, int cx, int cy, int r, float open, int alert, float phase) {
  od_disc(f, cx, cy, r, 1);
  od_fill(f, cx - r, cy, 2 * r + 1, r * 2 / 3, 1);  // the mantle runs down into the tentacles
  int base = cy + r * 2 / 3;
  for (int i = 0; i < 6; i++) {
    float x0 = cx - r + 1 + (2.0f * r - 2) * (i + 0.5f) / 6;
    float out = (i - 2.5f) * 0.45f;  // outer ones splay outwards
    int len = r - (i == 0 || i == 5 ? 2 : 0);
    for (int k = 0; k < len; k++) {
      float x = x0 + out * k + 1.6f * sinf(phase + k * 0.45f + i * 1.1f);
      od_fill(f, (int)lroundf(x) - 1, base + k, 3 - (k > len * 2 / 3), 1, 1);
    }
    od_disc(f, (int)lroundf(x0 + out * len + 1.6f * sinf(phase + len * 0.45f + i * 1.1f)), base + len, 1, 1);  // curled tip
  }
  int ex = r * 2 / 5, ey = cy + r / 6, er = r / 4 + 1;
  for (int s = -1; s <= 1; s += 2) {
    if (open < 0.2f) {  // closed: a smile-shaped line
      for (int i = -er; i <= er; i++) od_px(f, cx + s * ex + i, ey + (i * i) / (er + 1) - 1, 0);
      for (int i = -er; i <= er; i++) od_px(f, cx + s * ex + i, ey + (i * i) / (er + 1), 0);
    } else {  // an ellipse, as tall as it is open
      float ry = er * (open > 1 ? 1 : open);
      for (int j = -er; j <= er; j++)
        for (int i = -er; i <= er; i++)
          if ((float)i * i / (er * er) + (float)j * j / (ry * ry) <= 1.05f) od_px(f, cx + s * ex + i, ey + j, 0);
      if (open > 0.6f) od_disc(f, cx + s * ex + (alert ? 0 : 1), ey + (alert ? -1 : 0), er / 2, 1);
    }
  }
}

// ---- the screens ----------------------------------------------------------------------------

// Recording: the octopus listening (sound rings beside it), REC with a dot that blinks each second,
// and how long it has been recording.
static void od_screen_recording(od_frame *f, uint32_t seconds, uint32_t ms) {
  od_clear(f);
  od_octopus(f, 22, 28, 14, OD_AWAKE, ms * 0.004f);
  int waves = 1 + (int)(ms / 400 % 3);  // listening: sound waves above the head, growing
  for (int k = 0; k < waves; k++) od_arc(f, 22, 28, 18 + 4 * k, 225, 315);
  if (ms % 1000 < 600) od_disc(f, 56, 18, 5, 1);
  od_text(f, 66, 8, "REC", 3);
  char t[12];
  if (seconds < 100 * 60) snprintf(t, sizeof t, "%02lu:%02lu", (unsigned long)(seconds / 60), (unsigned long)(seconds % 60));
  else snprintf(t, sizeof t, "%luh %02lum", (unsigned long)(seconds / 3600), (unsigned long)(seconds / 60 % 60));
  od_text(f, 128 - od_text_width(t, 2) - 4, 40, t, 2);  // 42 until the footer (20 Oct): 2 px between them
}

// Stopped: the octopus asleep, "Stopped", and how to start.
static void od_screen_stopped(od_frame *f, uint32_t ms) {
  od_clear(f);
  od_octopus(f, 20, 24, 14, OD_ASLEEP, ms * 0.0015f);
  od_text(f, 43, 10, "Stopped", 2);
  od_text(f, 50, 34, "hold 1s", 1);
  od_text(f, 50, 44, "to record", 1);
  od_text(f, 34, (int)(ms / 700 % 2) ? 2 : 4, "z", 1);
}

// Something needs the owner: the octopus alarmed, and what (e.g. "No SD card").
static void od_screen_attention(od_frame *f, const char *what, uint32_t ms) {
  od_clear(f);
  od_octopus(f, 24, 24, 15, OD_ALERT, ms * 0.01f);
  od_text(f, 52, 6, "!", 3);
  od_text(f, 52, 36, what, 1);
}

// Holding the button: a bar to 1 s (2 s before rec-19), then what letting go does. Holding longer changes nothing.
static void od_screen_hold(od_frame *f, uint32_t held, uint32_t toggle_ms, int recording) {
  od_clear(f);
  int ready = held >= toggle_ms;
  const char *now = ready ? "Let go to" : "Keep holding to";
  const char *what = recording ? "stop" : "record";
  float p = ready ? 1.0f : (float)held / toggle_ms;
  od_text(f, (128 - od_text_width(now, 1)) / 2, 6, now, 1);
  od_text(f, (128 - od_text_width(what, 2)) / 2, 18, what, 2);
  od_rect(f, 8, 42, 112, 12);
  od_fill(f, 10, 44, (int)(108 * p), 8, 1);
}

static void od_screen_hello(od_frame *f, uint32_t ms) {
  od_clear(f);
  od_octopus(f, 26, 24, 17, OD_AWAKE, ms * 0.006f);
  od_text(f, 52, 18, "octo", 3);
}

// Not paired with a Mac yet (product plan 4.4): it doesn't record or join Wi-Fi until it is. The
// owner plugs it into their Mac and runs `octo device pair`, or uses the lab's Settings.
static void od_screen_setup(od_frame *f, uint32_t ms) {
  od_clear(f);
  od_octopus(f, 20, 26, 13, OD_AWAKE, ms * 0.004f);
  od_text(f, 44, 6, "Set me up", 1);
  od_text(f, 44, 20, "USB", 3);
  od_text(f, 44, 48, "to your Mac", 1);
}

// A phone pairing over Bluetooth: the six digits to type on the phone, large enough to read across a
// table. Shown until the phone answers or gives up.
static void od_screen_passkey(od_frame *f, uint32_t code, uint32_t ms) {
  od_clear(f);
  od_text(f, (128 - od_text_width("Phone code", 1)) / 2, 4, "Phone code", 1);
  char t[8];
  snprintf(t, sizeof t, "%06lu", (unsigned long)(code % 1000000));
  od_text(f, (128 - od_text_width(t, 3)) / 2, 22, t, 3);
  od_text(f, (128 - od_text_width("type it on the phone", 1)) / 2, 52, "type it on the phone", 1);
  if (ms % 1000 < 500) od_disc(f, 122, 7, 2, 1);
}

// Over USB, from a computer that can't sign with this recorder's key (rec-16, review 8 Oct C16): pairing it with
// another Mac, or an update, waits for someone holding it. The bar fills while its button is held; seconds left.
static void od_screen_confirm(od_frame *f, const char *what, uint32_t held, uint32_t hold_ms, uint32_t left_ms) {
  od_clear(f);
  int ready = held >= hold_ms;
  float p = ready ? 1.0f : (float)held / hold_ms;
  od_text(f, (128 - od_text_width("Allow", 1)) / 2, 2, "Allow", 1);
  od_text(f, (128 - od_text_width(what, 2)) / 2, 13, what, 2);
  od_rect(f, 8, 33, 112, 12);
  od_fill(f, 10, 35, (int)(108 * p), 8, 1);
  char t[28];
  snprintf(t, sizeof t, "hold the button %lus", (unsigned long)((left_ms + 999) / 1000));
  od_text(f, (128 - od_text_width(t, 1)) / 2, 52, t, 1);
}


// ---- always on (5 Oct): Apple Watch's way ------------------------------------------------
//
// Between interactions the screen stays on, but dim (recorder.ino lowers the contrast), simplified,
// redrawn once a second, and shifted a few pixels every minute so no pixel stays lit in one place
// (OLEDs burn in). The full screens above come back for a few seconds after any change or press.

// Where the ambient layout sits this minute: a slow walk round a small square.
static void od_shift(uint32_t ms, int *dx, int *dy) {
  static const int8_t walk[8][2] = {{0, 0}, {3, 1}, {6, 2}, {6, 5}, {3, 6}, {0, 5}, {-2, 3}, {-1, 1}};
  int i = (int)(ms / 60000 % 8);
  *dx = walk[i][0];
  *dy = walk[i][1];
}

// REC as tall as the time (20 Oct, review: size 1 couldn't be read across a table under the glass); the octopus
// smaller (9 to 8) to pay for its pixels. The dot blinks once a second, as on the bright screen (20 Oct):
// recorder.ino draws this one every 500 ms, on the half second, so it's on for the first half of each second.
static void od_ambient_recording(od_frame *f, uint32_t seconds, uint32_t ms) {
  int dx, dy;
  od_shift(ms, &dx, &dy);
  od_clear(f);
  od_octopus(f, 20 + dx, 22 + dy, 8, OD_AWAKE, 0);
  if (ms % 1000 < 500) od_disc(f, 46 + dx, 16 + dy, 4, 1);
  od_text(f, 55 + dx, 9 + dy, "REC", 2);
  char t[12];
  if (seconds < 100 * 60) snprintf(t, sizeof t, "%02lu:%02lu", (unsigned long)(seconds / 60), (unsigned long)(seconds % 60));
  else snprintf(t, sizeof t, "%luh %02lum", (unsigned long)(seconds / 3600), (unsigned long)(seconds / 60 % 60));
  od_text(f, 42 + dx, 30 + dy, t, 2);
}

// Stopped, at a glance: the sleeping octopus and the word (5 Oct: the octopus alone didn't say it).
static void od_ambient_stopped(od_frame *f, uint32_t ms) {
  int dx, dy;
  od_shift(ms, &dx, &dy);
  od_clear(f);
  od_octopus(f, 20 + dx, 24 + dy, 9, OD_ASLEEP, 0);
  od_text(f, 36 + dx, 18 + dy, "Stopped", 2);
}


// ---- start and stop (5 Oct) ------------------------------------------------------------
//
// About a second each, then the recording or stopped screen takes over. Eased, not linear, the way
// Apple moves things: quick out of the gate, settling at the end.

#define OD_ANIM_MS 1200

static float od_clamp01(float x) { return x < 0 ? 0 : x > 1 ? 1 : x; }
static float od_ease_out(float t) { t = od_clamp01(t); return 1 - (1 - t) * (1 - t) * (1 - t); }
static float od_ease_back(float t) {  // a small overshoot: a pop
  t = od_clamp01(t);
  float c = 1.7f;
  return 1 + (c + 1) * powf(t - 1, 3) + c * powf(t - 1, 2);
}

// Start: a ripple goes out, the octopus pops up and opens its eyes, the waves come one by one, REC slides in.
static void od_anim_start(od_frame *f, uint32_t ms) {
  float t = (float)ms;  // signed from here: t - 250 must go negative, not wrap round
  od_clear(f);
  int cx = 22, cy = 28;
  if (t < 650) od_arc(f, cx, cy, 4 + (int)(80 * od_ease_out(t / 650.0f)), 0, 358);
  int r = 8 + (int)lroundf(6 * od_ease_back(t / 450.0f));
  od_octopus_eyes(f, cx, cy, r, od_clamp01((t - 250) / 250.0f), 0, t * 0.004f);
  int waves = t < 500 ? 0 : 1 + (int)((t - 500) / 150);
  if (waves > 3) waves = 3;
  for (int k = 0; k < waves && k < 3; k++) od_arc(f, cx, cy, 18 + 4 * k, 225, 315);
  int x = 128 - (int)((128 - 66) * od_ease_out((t - 550) / 450.0f));
  if (t > 550) od_text(f, x, 8, "REC", 3);
  if (t > 950) od_disc(f, 56, 18, 5, 1);
  if (t > 950) od_text(f, 128 - od_text_width("00:00", 2) - 4, 40, "00:00", 2);
}

// Stop: REC slides away, the waves go, the eyes close, "Stopped" slides in, a z drifts up.
static void od_anim_stop(od_frame *f, uint32_t ms) {
  float t = (float)ms;  // signed, as above
  od_clear(f);
  float move = od_ease_out(t / 900.0f);  // the octopus drifts to where the stopped screen has it
  int cx = 22 - (int)lroundf(2 * move), cy = 28 - (int)lroundf(4 * move);
  od_octopus_eyes(f, cx, cy, 14, 1 - od_clamp01((t - 300) / 350.0f), 0, t * 0.003f);
  int waves = t < 300 ? 3 - (int)(t / 100) : 0;
  for (int k = 0; k < waves; k++) od_arc(f, cx, cy, 18 + 4 * k, 225, 315);
  if (t < 450) {
    int x = 66 + (int)(70 * od_ease_out(t / 450.0f));
    od_text(f, x, 8, "REC", 3);
  }
  if (t > 650) od_text(f, 128 - (int)((128 - 43) * od_ease_out((t - 650) / 450.0f)), 10, "Stopped", 2);
  if (t > 1000) od_text(f, 34, 8 - (int)((t - 1000) / 60), "z", 1);
  if (t > 950) { od_text(f, 50, 34, "hold 1s", 1); od_text(f, 50, 44, "to record", 1); }  // as the stopped screen
}

// Updating the firmware (over Wi-Fi, or USB since rec-13): the octopus busy, how far, then "Restarting".
static void od_screen_updating(od_frame *f, uint32_t done, uint32_t size, uint32_t ms) {
  od_clear(f);
  od_octopus(f, 20, 22, 12, OD_AWAKE, ms * 0.012f);  // tentacles going fast: working
  int restarting = size && done >= size;
  od_text(f, 42, 6, restarting ? "Restarting" : "Updating", 1);
  char pct[8];
  snprintf(pct, sizeof pct, "%lu%%", (unsigned long)(size ? (uint64_t)done * 100 / size : 0));
  od_text(f, 42, 18, pct, 2);
  od_rect(f, 8, 46, 112, 10);
  od_fill(f, 10, 48, (int)(108 * (size ? (float)done / size : 0)), 6, 1);
  for (int i = 0; i < 3; i++)  // a spinner of dots under the text, so it's clearly alive
    od_disc(f, 100 + i * 7, 24, (int)(ms / 200 % 3) == i ? 2 : 1, 1);
}

// After an update (5 Oct): did it take? The new firmware shows "Checking" until the Mac reaches
// it (it's on trial until then), then "Updated" with a tick that draws itself. If the update didn't
// arrive whole, "Update failed"; if the new firmware didn't start properly and the recorder went back
// to the one before, "Rolled back". `fw` is the firmware running now.
enum od_update_result { OD_UPD_CHECKING, OD_UPD_DONE, OD_UPD_FAILED, OD_UPD_UNDONE };

static void od_line(od_frame *f, int x0, int y0, int x1, int y1, int thick) {
  int n = abs(x1 - x0) > abs(y1 - y0) ? abs(x1 - x0) : abs(y1 - y0);
  for (int i = 0; i <= n; i++) {
    int x = x0 + (n ? (x1 - x0) * i / n : 0), y = y0 + (n ? (y1 - y0) * i / n : 0);
    od_fill(f, x - thick / 2, y - thick / 2, thick, thick, 1);
  }
}

static void od_screen_update_result(od_frame *f, int kind, const char *fw, uint32_t ms) {
  od_clear(f);
  int bad = kind == OD_UPD_FAILED || kind == OD_UPD_UNDONE;
  od_octopus(f, 20, 24, 13, bad ? OD_ALERT : OD_AWAKE, ms * (kind == OD_UPD_CHECKING ? 0.012f : 0.005f));
  static const char *title[] = {"Checking", "Updated", "Update failed", "Rolled back"};
  od_text(f, 44, 6, title[kind], 1);
  od_text(f, 44, 20, fw, 2);
  if (kind == OD_UPD_CHECKING) {
    od_text(f, 50, 46, "with the Mac", 1);
    for (int i = 0; i < 3; i++) od_disc(f, 104 + i * 7, 9, (int)(ms / 200 % 3) == i ? 2 : 1, 1);
  } else if (kind == OD_UPD_DONE) {  // the tick draws itself over half a second: down stroke, then up
    float t = od_clamp01(ms / 500.0f);
    float a = od_clamp01(t / 0.35f), b = od_clamp01((t - 0.35f) / 0.65f);
    od_line(f, 54, 47, 54 + (int)(7 * a), 47 + (int)(7 * a), 3);
    if (b > 0) od_line(f, 61, 54, 61 + (int)(15 * b), 54 - (int)(15 * b), 3);
    if (t >= 1) od_text(f, 80, 46, "all good", 1);
  } else {
    od_text(f, 50, 46, kind == OD_UPD_FAILED ? "no change" : "update failed", 1);
  }
}


// ---- sending to the Mac (20 Oct: "syncing, syncing + recording, just recording") ----------------
//
// A line of small words, never in REC's place: whether it's recording stays the one thing anyone at the table
// reads. Reviewed with two designers before building (20 Oct): no up arrow (beside REC it reads as "uploading
// to the cloud" to the people across the table), the destination named, nothing that moves but REC's dot.
//   copying  the Mac is taking audio now: "Copying to Mac" (dim: "To Mac")
//   waiting  that much audio on the card that the Mac hasn't taken for 15 min (away from home, or the lab
//            off): "42 min waiting"; not "to send", which reads as a countdown
//   all on   nothing left on the card: "All on your Mac", on the bright screens only (after a tap, say), so
//            the owner can check; the dim screen shows nothing when all is well
// Bright screens: a footer under the octopus. Dim: under the main line, walking with it.
enum od_sync { OD_SYNC_NONE, OD_SYNC_COPYING, OD_SYNC_WAITING, OD_SYNC_ALL_ON };
enum od_layout { OD_ON_RECORDING, OD_ON_STOPPED, OD_ON_AMBIENT_RECORDING, OD_ON_AMBIENT_STOPPED };

static void od_sync_status(od_frame *f, int layout, int kind, uint32_t minutes, uint32_t ms) {
  int ambient = layout == OD_ON_AMBIENT_RECORDING || layout == OD_ON_AMBIENT_STOPPED;
  if (kind == OD_SYNC_NONE || (ambient && kind == OD_SYNC_ALL_ON)) return;
  int dx = 0, dy = 0, x = 2, y = 56;                           // bright: the footer, below the time's ink
  if (ambient) od_shift(ms, &dx, &dy);
  if (layout == OD_ON_AMBIENT_RECORDING) x = 42 + dx, y = 49 + dy;  // under the time
  if (layout == OD_ON_AMBIENT_STOPPED) x = 36 + dx, y = 40 + dy;    // under "Stopped"
  char t[24];
  if (kind == OD_SYNC_COPYING) snprintf(t, sizeof t, ambient ? "To Mac" : "Copying to Mac");
  else if (kind == OD_SYNC_ALL_ON) snprintf(t, sizeof t, "All on your Mac");
  else if (minutes < 100) snprintf(t, sizeof t, ambient ? "%lum waiting" : "%lu min waiting", (unsigned long)minutes);
  else if (minutes < 48 * 60) snprintf(t, sizeof t, "%luh waiting", (unsigned long)(minutes / 60));
  else snprintf(t, sizeof t, "%lud waiting", (unsigned long)(minutes / 1440));
  od_text(f, x, y, t, 1);
}

// ---- the filler cue (B20, rec-24) -----------------------------------------------------------------
//
// When the recorder hears the owner say their filler (octo_filler.h; "you know" until rec-27): its name, big, for a
// moment on a bright screen, and how many so far in this recording. Then a small count stays: under the time on the
// dim screen, in the footer on the bright one, but only where the Mac's copy line isn't (that one matters more).

static void od_screen_filler(od_frame *f, const char *name, uint32_t count, uint32_t ms) {
  (void)ms;
  od_clear(f);
  int s = od_text_width(name, 2) <= 124 ? 2 : 1;  // 10 letters at size 2; longer ("essentially") at size 1
  od_text(f, (128 - od_text_width(name, s)) / 2, s == 2 ? 14 : 18, name, s);
  char t[24];
  snprintf(t, sizeof t, "%lu so far", (unsigned long)count);
  od_text(f, (128 - od_text_width(t, 1)) / 2, 40, t, 1);
}

// A double tap marked this moment (B16, rec-26): a small flag and "Marked", for about a second.
static void od_screen_mark(od_frame *f, uint32_t ms) {
  (void)ms;
  od_clear(f);
  int w = 22 + od_text_width("Marked", 2), x = (128 - w) / 2;
  od_fill(f, x, 18, 2, 28, 1);  // the pole
  for (int r = 0; r < 14; r++) od_fill(f, x + 2, 18 + r, 14 - (r > 7 ? r - 7 : 7 - r), 1, 1);  // the pennant, pointed
  od_text(f, x + 22, 25, "Marked", 2);
}

static void od_filler_count(od_frame *f, int layout, const char *name, uint32_t count, uint32_t ms) {
  if (!count || (layout != OD_ON_RECORDING && layout != OD_ON_AMBIENT_RECORDING)) return;
  int dx = 0, dy = 0, x = 2, y = 56;
  if (layout == OD_ON_AMBIENT_RECORDING) {
    od_shift(ms, &dx, &dy);
    x = 42 + dx, y = 49 + dy;  // where the copy line goes
  }
  char t[40];
  snprintf(t, sizeof t, "%s %lu", name, (unsigned long)count);
  if (x + od_text_width(t, 1) > 128) snprintf(t, sizeof t, "%lu", (unsigned long)count);  // a long name: the count alone
  od_text(f, x, y, t, 1);
}
