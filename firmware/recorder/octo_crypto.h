// Chunk encryption for the recorder (review 4 Oct, M41; 5 Oct: build it now).
//
// Each boot makes a throwaway X25519 key pair, agrees a secret with the Mac's public key (in
// secrets.h; only the Mac has the private half), and derives that boot's chunk key. The private
// half is wiped at once, so the card can't be read even by someone who has the device and its
// flash: only the Mac can decrypt. Chunks are encrypted with ChaCha20 as they're written, the
// keystream taken at the byte's offset in the file, so any part can be written or patched alone.
//
// Plain C, no ESP-IDF: the same file is compiled on the Mac by the tests and checked against the
// Mac's own Python implementation (octo/devicecrypto.py) and the RFC test vectors.
// X25519 after TweetNaCl (public domain, Bernstein et al.); ChaCha20 after RFC 8439.
#pragma once
#include <stdint.h>
#include <string.h>

typedef int64_t oc_gf[16];

static void oc_car(oc_gf o) {
  for (int i = 0; i < 16; i++) {
    o[i] += (1LL << 16);
    int64_t c = o[i] >> 16;
    o[(i + 1) * (i < 15)] += c - 1 + 37 * (c - 1) * (i == 15);
    o[i] -= c << 16;
  }
}
static void oc_sel(oc_gf p, oc_gf q, int b) {
  int64_t c = ~(b - 1);
  for (int i = 0; i < 16; i++) { int64_t t = c & (p[i] ^ q[i]); p[i] ^= t; q[i] ^= t; }
}
static void oc_pack(uint8_t *o, const oc_gf n) {
  oc_gf m, t;
  for (int i = 0; i < 16; i++) t[i] = n[i];
  oc_car(t); oc_car(t); oc_car(t);
  for (int j = 0; j < 2; j++) {
    m[0] = t[0] - 0xffed;
    for (int i = 1; i < 15; i++) { m[i] = t[i] - 0xffff - ((m[i - 1] >> 16) & 1); m[i - 1] &= 0xffff; }
    m[15] = t[15] - 0x7fff - ((m[14] >> 16) & 1);
    int b = (m[15] >> 16) & 1;
    m[14] &= 0xffff;
    oc_sel(t, m, 1 - b);
  }
  for (int i = 0; i < 16; i++) { o[2 * i] = t[i] & 0xff; o[2 * i + 1] = t[i] >> 8; }
}
static void oc_unpack(oc_gf o, const uint8_t *n) {
  for (int i = 0; i < 16; i++) o[i] = n[2 * i] + ((int64_t)n[2 * i + 1] << 8);
  o[15] &= 0x7fff;
}
static void oc_add(oc_gf o, const oc_gf a, const oc_gf b) { for (int i = 0; i < 16; i++) o[i] = a[i] + b[i]; }
static void oc_sub(oc_gf o, const oc_gf a, const oc_gf b) { for (int i = 0; i < 16; i++) o[i] = a[i] - b[i]; }
static void oc_mul(oc_gf o, const oc_gf a, const oc_gf b) {
  int64_t t[31];
  for (int i = 0; i < 31; i++) t[i] = 0;
  for (int i = 0; i < 16; i++) for (int j = 0; j < 16; j++) t[i + j] += a[i] * b[j];
  for (int i = 0; i < 15; i++) t[i] += 38 * t[i + 16];
  for (int i = 0; i < 16; i++) o[i] = t[i];
  oc_car(o); oc_car(o);
}
static void oc_inv(oc_gf o, const oc_gf i) {
  oc_gf c;
  for (int a = 0; a < 16; a++) c[a] = i[a];
  for (int a = 253; a >= 0; a--) { oc_mul(c, c, c); if (a != 2 && a != 4) oc_mul(c, c, i); }
  for (int a = 0; a < 16; a++) o[a] = c[a];
}

// q = n * p on Curve25519 (RFC 7748 X25519). All 32-byte little-endian.
static void oc_x25519(uint8_t *q, const uint8_t *n, const uint8_t *p) {
  static const oc_gf k121665 = {0xDB41, 1};
  uint8_t z[32];
  int64_t x[80];
  oc_gf a, b, c, d, e, f;
  for (int i = 0; i < 31; i++) z[i] = n[i];
  z[31] = (n[31] & 127) | 64;
  z[0] &= 248;
  oc_unpack(x, p);
  for (int i = 0; i < 16; i++) { b[i] = x[i]; d[i] = a[i] = c[i] = 0; }
  a[0] = d[0] = 1;
  for (int i = 254; i >= 0; --i) {
    int r = (z[i >> 3] >> (i & 7)) & 1;
    oc_sel(a, b, r); oc_sel(c, d, r);
    oc_add(e, a, c); oc_sub(a, a, c); oc_add(c, b, d); oc_sub(b, b, d);
    oc_mul(d, e, e); oc_mul(f, a, a); oc_mul(a, c, a); oc_mul(c, b, e);
    oc_add(e, a, c); oc_sub(a, a, c); oc_mul(b, a, a); oc_sub(c, d, f);
    oc_mul(a, c, k121665); oc_add(a, a, d); oc_mul(c, c, a); oc_mul(a, d, f);
    oc_mul(d, b, x); oc_mul(b, e, e);
    oc_sel(a, b, r); oc_sel(c, d, r);
  }
  for (int i = 0; i < 16; i++) { x[i + 16] = a[i]; x[i + 32] = c[i]; x[i + 48] = b[i]; x[i + 64] = d[i]; }
  oc_inv(x + 32, x + 32);
  oc_mul(x + 16, x + 16, x + 32);
  oc_pack(q, x + 16);
  memset(z, 0, sizeof z);
}

static const uint8_t OC_BASE[32] = {9};

// ---- ChaCha20 (RFC 8439) --------------------------------------------------------------------

#define OC_ROTL(v, n) (((v) << (n)) | ((v) >> (32 - (n))))
#define OC_QR(a, b, c, d) \
  a += b; d ^= a; d = OC_ROTL(d, 16); c += d; b ^= c; b = OC_ROTL(b, 12); \
  a += b; d ^= a; d = OC_ROTL(d, 8);  c += d; b ^= c; b = OC_ROTL(b, 7);

static uint32_t oc_le32(const uint8_t *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24); }

// One 64-byte keystream block.
static void oc_chacha_block(uint8_t out[64], const uint8_t key[32], uint32_t counter, const uint8_t nonce[12]) {
  uint32_t s[16] = {0x61707865, 0x3320646e, 0x79622d32, 0x6b206574};
  for (int i = 0; i < 8; i++) s[4 + i] = oc_le32(key + 4 * i);
  s[12] = counter;
  for (int i = 0; i < 3; i++) s[13 + i] = oc_le32(nonce + 4 * i);
  uint32_t w[16];
  memcpy(w, s, sizeof w);
  for (int i = 0; i < 10; i++) {
    OC_QR(w[0], w[4], w[8], w[12]) OC_QR(w[1], w[5], w[9], w[13]) OC_QR(w[2], w[6], w[10], w[14]) OC_QR(w[3], w[7], w[11], w[15])
    OC_QR(w[0], w[5], w[10], w[15]) OC_QR(w[1], w[6], w[11], w[12]) OC_QR(w[2], w[7], w[8], w[13]) OC_QR(w[3], w[4], w[9], w[14])
  }
  for (int i = 0; i < 16; i++) {
    uint32_t v = w[i] + s[i];
    out[4 * i] = v; out[4 * i + 1] = v >> 8; out[4 * i + 2] = v >> 16; out[4 * i + 3] = v >> 24;
  }
}

// The nonce for chunk `seq` of boot `boot`: each chunk has its own keystream.
static void oc_chunk_nonce(uint8_t nonce[12], uint32_t boot, uint32_t seq) {
  memset(nonce, 0, 12);
  for (int i = 0; i < 4; i++) { nonce[i] = seq >> (8 * i); nonce[4 + i] = boot >> (8 * i); }
}

// XOR `n` bytes at file offset `offset` with the chunk's keystream (encrypts and decrypts).
static void oc_crypt_at(const uint8_t key[32], uint32_t boot, uint32_t seq, uint32_t offset, uint8_t *buf, size_t n) {
  uint8_t nonce[12], ks[64];
  oc_chunk_nonce(nonce, boot, seq);
  size_t done = 0;
  while (done < n) {
    uint32_t pos = offset + done;
    oc_chacha_block(ks, key, pos / 64, nonce);
    size_t k = 64 - pos % 64;
    if (k > n - done) k = n - done;
    for (size_t j = 0; j < k; j++) buf[done + j] ^= ks[pos % 64 + j];
    done += k;
  }
}
