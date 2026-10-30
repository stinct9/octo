"""Decrypting the recorder's chunks on the Mac (review 4 Oct, M41; 5 Oct).

The recorder encrypts every chunk as it writes it (firmware/recorder/octo_crypto.h): each boot
makes a throwaway X25519 key pair, puts its public half in that boot's first manifest line
("epk"), and derives the boot's key from the secret it shares with the Mac's public key. Only the
Mac holds the matching private key (data/device.json, never on the device), so a lost recorder's
card can't be read. Chunks use ChaCha20 with the keystream taken at each byte's file offset.

Written here from RFC 7748 and RFC 8439, separately from the firmware's C, so the two check each
other (tests compile the C and compare). No crypto package needed: X25519 with Python integers,
ChaCha20 vectorised with numpy (a 35 s chunk in a few milliseconds).
"""

from __future__ import annotations

import hashlib
import secrets

import numpy as np

P = 2 ** 255 - 19
A24 = 121665
KDF_LABEL = b"octo-rec-v1"


def _clamp(k: bytes) -> int:
    b = bytearray(k)
    b[0] &= 248
    b[31] = (b[31] & 127) | 64
    return int.from_bytes(b, "little")


def x25519(k: bytes, u: bytes) -> bytes:
    """RFC 7748 §5: the Montgomery ladder, constant structure (not constant time: Mac side only)."""
    x1 = int.from_bytes(u, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    n = _clamp(k)
    for t in reversed(range(255)):
        bit = (n >> t) & 1
        swap ^= bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a, b = (x2 + z2) % P, (x2 - z2) % P
        aa, bb = a * a % P, b * b % P
        e = (aa - bb) % P
        c, d = (x3 + z3) % P, (x3 - z3) % P
        da, cb = d * a % P, c * b % P
        x3, z3 = (da + cb) ** 2 % P, x1 * (da - cb) ** 2 % P
        x2, z2 = aa * bb % P, e * (aa + A24 * e) % P
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, P - 2, P) % P).to_bytes(32, "little")


BASE = (9).to_bytes(32, "little")


def keypair() -> tuple[bytes, bytes]:
    """(private, public) for the Mac."""
    sk = secrets.token_bytes(32)
    return sk, x25519(sk, BASE)


def boot_key(mac_private: bytes, epk: bytes) -> bytes:
    """The boot's chunk key, as the recorder derived it: SHA-256("octo-rec-v1" || shared || epk)."""
    return hashlib.sha256(KDF_LABEL + x25519(mac_private, epk) + epk).digest()


_C = np.array([0x61707865, 0x3320646E, 0x79622D32, 0x6B206574], np.uint32)


def _rotl(v, n):
    return (v << np.uint32(n)) | (v >> np.uint32(32 - n))


def keystream(key: bytes, nonce: bytes, first_block: int, blocks: int) -> bytes:
    """ChaCha20 blocks first_block .. first_block+blocks-1, all at once."""
    s = np.empty((blocks, 16), np.uint32)
    s[:, :4] = _C
    s[:, 4:12] = np.frombuffer(key, "<u4")
    s[:, 12] = np.arange(first_block, first_block + blocks, dtype=np.uint64).astype(np.uint32)
    s[:, 13:16] = np.frombuffer(nonce, "<u4")
    w = s.copy()
    with np.errstate(over="ignore"):
        def qr(a, b, c, d):
            w[:, a] += w[:, b]; w[:, d] ^= w[:, a]; w[:, d] = _rotl(w[:, d], 16)
            w[:, c] += w[:, d]; w[:, b] ^= w[:, c]; w[:, b] = _rotl(w[:, b], 12)
            w[:, a] += w[:, b]; w[:, d] ^= w[:, a]; w[:, d] = _rotl(w[:, d], 8)
            w[:, c] += w[:, d]; w[:, b] ^= w[:, c]; w[:, b] = _rotl(w[:, b], 7)
        for _ in range(10):
            qr(0, 4, 8, 12); qr(1, 5, 9, 13); qr(2, 6, 10, 14); qr(3, 7, 11, 15)
            qr(0, 5, 10, 15); qr(1, 6, 11, 12); qr(2, 7, 8, 13); qr(3, 4, 9, 14)
        w += s
    return w.astype("<u4").tobytes()


def chunk_nonce(boot: int, seq: int) -> bytes:
    return seq.to_bytes(4, "little") + boot.to_bytes(4, "little") + bytes(4)


def crypt_at(key: bytes, boot: int, seq: int, offset: int, data: bytes) -> bytes:
    """XOR `data`, which sits at `offset` in chunk `seq` of `boot`, with its keystream."""
    if not data:
        return b""
    first, last = offset // 64, (offset + len(data) - 1) // 64
    ks = keystream(key, chunk_nonce(boot, seq), first, last - first + 1)
    start = offset % 64
    return (np.frombuffer(data, np.uint8) ^ np.frombuffer(ks[start:start + len(data)], np.uint8)).tobytes()
