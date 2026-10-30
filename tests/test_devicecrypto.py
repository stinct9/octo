"""The recorder's chunk encryption: the firmware's C (compiled here on the Mac) and the Mac's
Python (octo/devicecrypto.py), written separately, against each other and the RFC vectors."""

import shutil
import subprocess

import pytest

from octo import devicecrypto as DC

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


def test_x25519_rfc7748_vectors_and_agreement():
    alice = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
    bob = bytes.fromhex("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
    assert DC.x25519(alice, DC.BASE).hex() == "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"
    assert DC.x25519(bob, DC.BASE).hex() == "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f"
    shared = DC.x25519(alice, DC.x25519(bob, DC.BASE))
    assert shared == DC.x25519(bob, DC.x25519(alice, DC.BASE))
    assert shared.hex() == "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742"


def test_chacha20_rfc8439_vector():
    key = bytes(range(32))
    nonce = bytes.fromhex("000000000000004a00000000")
    text = (b"Ladies and Gentlemen of the class of '99: If I could offer you only one tip for the future, "
            b"sunscreen would be it.")
    ks = DC.keystream(key, nonce, 1, 2)
    out = bytes(a ^ b for a, b in zip(text, ks))
    assert out[:16].hex() == "6e2e359a2568f98041ba0728dd0d6981"


def test_any_offset_decrypts_what_was_encrypted_in_pieces():
    key, data = bytes(range(32)), bytes(range(256)) * 20
    enc = DC.crypt_at(key, 7, 3, 0, data[:44]) + DC.crypt_at(key, 7, 3, 44, data[44:1000]) + DC.crypt_at(key, 7, 3, 1000, data[1000:])
    assert DC.crypt_at(key, 7, 3, 0, enc) == data
    assert DC.crypt_at(key, 7, 3, 44, enc[44:200]) == data[44:200]
    assert DC.crypt_at(key, 7, 4, 0, enc) != data  # another chunk, another keystream


HARNESS = r"""
#include <stdio.h>
#include "octo_crypto.h"
static void unhex(const char *s, uint8_t *o, int n) { for (int i = 0; i < n; i++) sscanf(s + 2 * i, "%2hhx", &o[i]); }
static void put(const uint8_t *b, int n) { for (int i = 0; i < n; i++) printf("%02x", b[i]); printf("\n"); }
int main(int argc, char **argv) {
  uint8_t sk[32], pk[32], q[32], key[32], buf[300];
  unhex(argv[1], sk, 32); unhex(argv[2], pk, 32);
  oc_x25519(q, sk, OC_BASE); put(q, 32);          /* public key */
  oc_x25519(q, sk, pk); put(q, 32);               /* shared secret */
  unhex(argv[3], key, 32);
  for (int i = 0; i < 300; i++) buf[i] = i;
  oc_crypt_at(key, 5, 9, 44, buf, 100);           /* two pieces, like the writer */
  oc_crypt_at(key, 5, 9, 144, buf + 100, 200);
  put(buf, 300);
  return 0;
}
"""


@pytest.mark.skipif(not shutil.which("cc"), reason="no C compiler")
def test_the_firmware_c_and_the_mac_python_agree(tmp_path):
    (tmp_path / "h.c").write_text(HARNESS)
    exe = tmp_path / "h"
    subprocess.run(["cc", "-O2", "-I", str(ROOT / "firmware" / "recorder"), str(tmp_path / "h.c"), "-o", str(exe)], check=True)
    sk, _ = DC.keypair()
    mac_sk, mac_pk = DC.keypair()
    key = bytes(range(32, 64))
    out = subprocess.run([str(exe), sk.hex(), mac_pk.hex(), key.hex()], capture_output=True, text=True, check=True).stdout.split()
    assert out[0] == DC.x25519(sk, DC.BASE).hex()
    assert out[1] == DC.x25519(mac_sk, bytes.fromhex(out[0])).hex()  # both ends of the agreement
    assert bytes.fromhex(out[2]) == DC.crypt_at(key, 5, 9, 44, bytes(range(256)) + bytes(range(44)))
