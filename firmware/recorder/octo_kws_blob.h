// The filler cue's model as data (B29, rec-27): what the Mac sends to /filler/model/* and the recorder keeps in a slot
// of its spare flash. A header, then the TFLite model, then the mel filterbank:
//
//   0  "OKWS"            4  u16 version (1)       6  u16 header size (80)
//   8  name[24], NUL-padded: the filler, as the screen and the manifest line say it
//   32 f32 threshold     36 u32 model bytes       40 u32 filterbank bytes (257 x 40 floats)
//   44 sha256[32] of everything after the header  76 zeros to 80, so the model starts 16-byte aligned
//
// The model is followed by zeros to a multiple of 4, then the filterbank. Little-endian, as the chip is.
// octo/filler_loop.py `pack` writes it. Plain C, no Arduino: the Mac compiles this in tests/test_filler_blob.py.
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define OC_KWS_HEAD 80
#define OC_KWS_FB_BYTES (257 * 40 * 4)

typedef struct {
  char name[24];
  float threshold;
  uint32_t model_len, payload_len;  // payload: everything after the header (what sha covers)
  uint8_t sha[32];
} oc_kws_head;

static uint32_t oc_u32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }

// A name the manifest's JSON and the screen can carry as it is: printable ASCII, no quote or backslash.
static int oc_kws_name_ok(const char *s) {
  size_t n = strnlen(s, 24);
  if (!n || n == 24) return 0;
  for (size_t i = 0; i < n; i++)
    if (s[i] < 0x20 || s[i] > 0x7e || s[i] == '"' || s[i] == '\\') return 0;
  return 1;
}

// The blob's whole size from its header (at most `cap`, the slot), or 0 if it isn't one.
static size_t oc_kws_parse(const uint8_t *h, size_t cap, oc_kws_head *out) {
  if (memcmp(h, "OKWS", 4) || (h[4] | h[5] << 8) != 1 || (h[6] | h[7] << 8) != OC_KWS_HEAD) return 0;
  memcpy(out->name, h + 8, 24);
  if (!oc_kws_name_ok(out->name)) return 0;
  uint32_t t = oc_u32(h + 32);
  memcpy(&out->threshold, &t, 4);
  if (!(out->threshold >= 0.5f && out->threshold < 1.0f)) return 0;
  out->model_len = oc_u32(h + 36);
  if (oc_u32(h + 40) != OC_KWS_FB_BYTES || !out->model_len || out->model_len > cap) return 0;
  out->payload_len = ((out->model_len + 3) & ~3u) + OC_KWS_FB_BYTES;
  if (OC_KWS_HEAD + (size_t)out->payload_len > cap) return 0;
  memcpy(out->sha, h + 44, 32);
  return OC_KWS_HEAD + out->payload_len;
}
