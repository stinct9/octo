// The lines of an update over USB (rec-13): first "<size> <sha256 hex>", then each piece of the image as
// "<offset> <base64>". recorder.ino answers each line before the Mac sends the next, so a line always fits
// the USB receive buffer (the driver drops what doesn't fit).
//
// Plain C, no Arduino: the Mac compiles this file in the tests (tests/test_firmware_update.py).
#pragma once
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "octo_settings.h"  // oc_unhex

static int oc_b64val(char c) {
  if (c >= 'A' && c <= 'Z') return c - 'A';
  if (c >= 'a' && c <= 'z') return c - 'a' + 26;
  if (c >= '0' && c <= '9') return c - '0' + 52;
  if (c == '+') return 62;
  if (c == '/') return 63;
  return -1;
}

// Standard base64 with its padding, into at most `cap` bytes. 0 if it isn't that or doesn't fit.
static int oc_b64_decode(const char *s, size_t len, uint8_t *out, size_t cap, size_t *n) {
  size_t o = 0;
  if (len % 4) return 0;
  for (size_t i = 0; i < len; i += 4) {
    int last = i + 4 == len;
    int pad = last && s[i + 3] == '=' ? (s[i + 2] == '=' ? 2 : 1) : 0;
    int a = oc_b64val(s[i]), b = oc_b64val(s[i + 1]), c = pad < 2 ? oc_b64val(s[i + 2]) : 0,
        d = pad < 1 ? oc_b64val(s[i + 3]) : 0;
    if (a < 0 || b < 0 || c < 0 || d < 0) return 0;
    uint32_t w = (uint32_t)a << 18 | (uint32_t)b << 12 | (uint32_t)c << 6 | (uint32_t)d;
    size_t m = 3 - (size_t)pad;
    if (o + m > cap) return 0;
    out[o++] = (uint8_t)(w >> 16);
    if (m > 1) out[o++] = (uint8_t)(w >> 8);
    if (m > 2) out[o++] = (uint8_t)w;
  }
  *n = o;
  return 1;
}

// A decimal number at the start of a line, then one space. Its length (with the space), or 0.
static size_t oc_number_then_space(const char *s, size_t len, uint32_t *v) {
  uint64_t x = 0;
  size_t i = 0;
  while (i < len && s[i] >= '0' && s[i] <= '9' && i < 10) x = x * 10 + (uint64_t)(s[i++] - '0');
  if (!i || i >= len || s[i] != ' ' || x > 0xffffffffu) return 0;
  *v = (uint32_t)x;
  return i + 1;
}

// The first line: "<size> <sha256 hex>", a size from 1 to max bytes. 1 if it's that.
static int oc_parse_update_head(const char *line, size_t len, uint32_t max, uint32_t *size, uint8_t sha[32]) {
  size_t at = oc_number_then_space(line, len, size);
  return at && *size >= 1 && *size <= max && oc_unhex(line + at, len - at, sha, 32);
}

// A piece: "<offset> <base64>", at least one byte and at most `cap`. 1 if it's that.
static int oc_parse_piece(const char *line, size_t len, uint32_t *offset, uint8_t *out, size_t cap, size_t *n) {
  size_t at = oc_number_then_space(line, len, offset);
  return at && oc_b64_decode(line + at, len - at, out, cap, n) && *n > 0;
}
