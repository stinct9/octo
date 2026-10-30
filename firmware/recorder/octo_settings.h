// The recorder's own settings (product plan 4.4, 7 Oct): the key it shares with its Mac, the Mac's
// public key for encrypting chunks, and up to five Wi-Fi networks. Kept in the board's flash (NVS) so
// one firmware build serves every unit; written when the recorder is paired with a Mac over USB, and
// the networks also from the phone app (Bluetooth) or the Mac (Wi-Fi API).
//
// A build with secrets.h (the first lab's, until it's dropped) still takes its key, public key and network from
// there: the recorder.ino merges them in at boot, so it behaves as before.
//
// Plain C, no Arduino: the Mac compiles this file in the tests (tests/test_firmware_settings.py).
#pragma once
#include <stdint.h>
#include <string.h>

#define OC_MAX_NETS 5
#define OC_SSID_MAX 32
#define OC_PASS_MAX 64

typedef struct { char ssid[OC_SSID_MAX + 1]; char pass[OC_PASS_MAX + 1]; } oc_net;
typedef struct { oc_net nets[OC_MAX_NETS]; int n; } oc_nets;

static int oc_hexval(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

// n bytes from exactly 2n hex characters; 0 if anything else.
static int oc_unhex(const char *s, size_t len, uint8_t *out, size_t n) {
  if (len != 2 * n) return 0;
  for (size_t i = 0; i < n; i++) {
    int hi = oc_hexval(s[2 * i]), lo = oc_hexval(s[2 * i + 1]);
    if (hi < 0 || lo < 0) return 0;
    out[i] = (uint8_t)(hi << 4 | lo);
  }
  return 1;
}

// A hex-encoded string of at most `max` bytes, into a C string; 0 if it isn't one. An SSID or a
// password travels as hex over USB, so spaces and any punctuation in it need no escaping.
static int oc_unhex_text(const char *s, size_t len, char *out, size_t max) {
  if (len % 2 || len / 2 > max) return 0;
  for (size_t i = 0; i < len / 2; i++) {
    int hi = oc_hexval(s[2 * i]), lo = oc_hexval(s[2 * i + 1]);
    if (hi < 0 || lo < 0) return 0;
    out[i] = (char)(hi << 4 | lo);
    if (!out[i]) return 0;
  }
  out[len / 2] = 0;
  return 1;
}

static int oc_nets_find(const oc_nets *ns, const char *ssid) {
  for (int i = 0; i < ns->n; i++) if (!strcmp(ns->nets[i].ssid, ssid)) return i;
  return -1;
}

// Add or update a network; the newest goes first (it's tried first). With five already, the oldest
// goes. 0 if the SSID or password is too long or the SSID is empty. A password of 8-63 characters
// (WPA2) or none (an open network).
static int oc_nets_add(oc_nets *ns, const char *ssid, const char *pass) {
  size_t sl = strlen(ssid), pl = strlen(pass);
  if (!sl || sl > OC_SSID_MAX || pl > OC_PASS_MAX || (pl && pl < 8)) return 0;
  int at = oc_nets_find(ns, ssid);
  if (at < 0) at = ns->n < OC_MAX_NETS ? ns->n++ : OC_MAX_NETS - 1;
  for (int i = at; i > 0; i--) ns->nets[i] = ns->nets[i - 1];
  memset(&ns->nets[0], 0, sizeof ns->nets[0]);
  memcpy(ns->nets[0].ssid, ssid, sl);
  memcpy(ns->nets[0].pass, pass, pl);
  return 1;
}

static int oc_nets_del(oc_nets *ns, const char *ssid) {
  int at = oc_nets_find(ns, ssid);
  if (at < 0) return 0;
  for (int i = at; i < ns->n - 1; i++) ns->nets[i] = ns->nets[i + 1];
  ns->n--;
  memset(&ns->nets[ns->n], 0, sizeof ns->nets[0]);
  return 1;
}

// Stored form: count, then per network its SSID length, SSID, password length, password.
static size_t oc_nets_pack(const oc_nets *ns, uint8_t *buf, size_t cap) {
  size_t o = 0;
  if (cap < 1) return 0;
  buf[o++] = (uint8_t)ns->n;
  for (int i = 0; i < ns->n; i++) {
    size_t sl = strlen(ns->nets[i].ssid), pl = strlen(ns->nets[i].pass);
    if (o + 2 + sl + pl > cap) return 0;
    buf[o++] = (uint8_t)sl; memcpy(buf + o, ns->nets[i].ssid, sl); o += sl;
    buf[o++] = (uint8_t)pl; memcpy(buf + o, ns->nets[i].pass, pl); o += pl;
  }
  return o;
}

static int oc_nets_unpack(const uint8_t *buf, size_t len, oc_nets *ns) {
  memset(ns, 0, sizeof *ns);
  if (len < 1 || buf[0] > OC_MAX_NETS) return 0;
  size_t o = 1;
  for (int i = 0; i < buf[0]; i++) {
    if (o >= len) return 0;
    size_t sl = buf[o++];
    if (!sl || sl > OC_SSID_MAX || o + sl >= len) return 0;
    memcpy(ns->nets[i].ssid, buf + o, sl); o += sl;
    size_t pl = buf[o++];
    if (pl > OC_PASS_MAX || o + pl > len) return 0;
    memcpy(ns->nets[i].pass, buf + o, pl); o += pl;
    ns->n++;
  }
  return o == len;
}

// USB pairing line: "<key: 64 hex> <the Mac's public key: 64 hex>". 1 if both parse.
static int oc_parse_pair(const char *line, uint8_t key[32], uint8_t pub[32]) {
  const char *sp = strchr(line, ' ');
  if (!sp) return 0;
  size_t klen = (size_t)(sp - line), plen = strlen(sp + 1);
  while (plen && (sp[plen] == '\r' || sp[plen] == '\n' || sp[plen] == ' ')) plen--;
  return oc_unhex(line, klen, key, 32) && oc_unhex(sp + 1, plen, pub, 32);
}

// A network change, from USB ("add <ssid hex> <password hex>", "del <ssid hex>") or from the phone over
// Bluetooth ("add\n<ssid>\n<password>", "del\n<ssid>"). Applies it; 1 if it was valid and changed something.
static int oc_net_command(oc_nets *ns, const char *msg, size_t len, int hex) {
  char ssid[OC_SSID_MAX + 1] = {0}, pass[OC_PASS_MAX + 1] = {0};
  char sep = hex ? ' ' : '\n';
  int add = len > 4 && !memcmp(msg, "add", 3) && msg[3] == sep;
  int del = len > 4 && !memcmp(msg, "del", 3) && msg[3] == sep;
  if (!add && !del) return 0;
  const char *a = msg + 4, *end = msg + len;
  while (end > a && (end[-1] == '\r' || (hex && (end[-1] == '\n' || end[-1] == ' ')))) end--;
  const char *b = (const char *)memchr(a, sep, (size_t)(end - a));
  if (del) {
    if (b) return 0;
    if (hex ? !oc_unhex_text(a, (size_t)(end - a), ssid, OC_SSID_MAX)
            : ((size_t)(end - a) > OC_SSID_MAX || end == a)) return 0;
    if (!hex) memcpy(ssid, a, (size_t)(end - a));
    return oc_nets_del(ns, ssid);
  }
  if (!b && !hex) return 0;
  if (hex) {  // an open network has no password: "add <ssid hex>" (the trailing space was trimmed)
    if (!b) b = end;
    if (!oc_unhex_text(a, (size_t)(b - a), ssid, OC_SSID_MAX)) return 0;
    if (b < end && !oc_unhex_text(b + 1, (size_t)(end - b - 1), pass, OC_PASS_MAX)) return 0;
  } else {
    if ((size_t)(b - a) > OC_SSID_MAX || (size_t)(end - b - 1) > OC_PASS_MAX) return 0;
    memcpy(ssid, a, (size_t)(b - a));
    memcpy(pass, b + 1, (size_t)(end - b - 1));
  }
  return oc_nets_add(ns, ssid, pass);
}
