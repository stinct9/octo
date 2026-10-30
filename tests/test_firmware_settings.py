"""The recorder's own settings (firmware/recorder/octo_settings.h, product plan 4.4), compiled on the Mac:
the network list, its stored form, and the pairing and network messages from USB and from the phone."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

HARNESS = r"""
#include <stdio.h>
#include "octo_settings.h"
static void show(const char *name, const oc_nets *ns) {
  printf("%s %d", name, ns->n);
  for (int i = 0; i < ns->n; i++) printf(" [%s|%s]", ns->nets[i].ssid, ns->nets[i].pass);
  printf("\n");
}
static int cmd(oc_nets *ns, const char *m, int hex) { return oc_net_command(ns, m, strlen(m), hex); }
int main(void) {
  oc_nets ns = {0};
  printf("add_home %d\n", oc_nets_add(&ns, "home", "password1"));
  printf("add_short_pass %d\n", oc_nets_add(&ns, "x", "short"));
  printf("add_open %d\n", oc_nets_add(&ns, "cafe", ""));
  printf("add_empty_ssid %d\n", oc_nets_add(&ns, "", "password1"));
  show("two", &ns);
  oc_nets_add(&ns, "home", "password2");
  show("updated_moves_first", &ns);
  oc_nets_add(&ns, "a", "aaaaaaaa"); oc_nets_add(&ns, "b", "bbbbbbbb"); oc_nets_add(&ns, "c", "cccccccc");
  oc_nets_add(&ns, "d", "dddddddd");
  show("five_oldest_dropped", &ns);
  uint8_t buf[512]; size_t n = oc_nets_pack(&ns, buf, sizeof buf);
  oc_nets back; printf("unpack %d\n", oc_nets_unpack(buf, n, &back)); show("roundtrip", &back);
  printf("unpack_truncated %d\n", oc_nets_unpack(buf, n - 1, &back));
  printf("del_c %d\n", oc_nets_del(&ns, "c"));
  printf("del_missing %d\n", oc_nets_del(&ns, "zzz"));
  show("after_del", &ns);
  oc_nets ble = {0};
  printf("ble_add %d\n", cmd(&ble, "add\nMy Home 5G\nsecret pass!", 0));
  printf("ble_add_crlf %d\n", cmd(&ble, "add\nOffice\nofficepass\r", 0));
  printf("ble_bad %d\n", cmd(&ble, "add My Home", 0));
  show("ble", &ble);
  printf("ble_del %d\n", cmd(&ble, "del\nMy Home 5G", 0));
  show("ble_after_del", &ble);
  oc_nets usb = {0};
  /* "Sam's Wi-Fi" / "p@ss word 9" as hex */
  printf("usb_add %d\n", cmd(&usb, "add 53616d2773205769 704073732077 6f7264203920\n", 1));
  printf("usb_add_ok %d\n", cmd(&usb, "add 53616d2773205769 7040737320776f72642039\n", 1));
  printf("usb_bad_hex %d\n", cmd(&usb, "add 4a6g 70407373", 1));
  show("usb", &usb);
  printf("usb_del %d\n", cmd(&usb, "del 53616d2773205769\r\n", 1));
  printf("usb_open %d\n", cmd(&usb, "add 43616665 \n", 1));  /* "Cafe", no password */
  show("usb_open_net", &usb);
  uint8_t key[32], pub[32];
  const char *k = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f";
  const char *p = "ffeeddccbbaa99887766554433221100ffeeddccbbaa99887766554433221100";
  char line[200]; snprintf(line, sizeof line, "%s %s\r\n", k, p);
  printf("pair %d %02x %02x %02x %02x\n", oc_parse_pair(line, key, pub), key[0], key[31], pub[0], pub[31]);
  printf("pair_short %d\n", oc_parse_pair("0011 2233", key, pub));
  printf("pair_one %d\n", oc_parse_pair(k, key, pub));
  return 0;
}
"""


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    if not shutil.which("cc"):
        pytest.skip("no C compiler")
    tmp = tmp_path_factory.mktemp("fw")
    (tmp / "s.c").write_text(HARNESS)
    subprocess.run(["cc", "-O1", "-Wall", "-Werror", "-Wno-unused-function", "-I", str(ROOT / "firmware" / "recorder"),
                    str(tmp / "s.c"), "-o", str(tmp / "s")], check=True)
    lines = subprocess.run([str(tmp / "s")], capture_output=True, text=True, check=True).stdout.splitlines()
    return {ln.split(" ", 1)[0]: ln.split(" ", 1)[1] for ln in lines}


def test_the_network_list(out):
    assert out["add_home"] == "1" and out["add_short_pass"] == "0" and out["add_open"] == "1" and out["add_empty_ssid"] == "0"
    assert out["two"] == "2 [cafe|] [home|password1]"  # the newest first: it's tried first
    assert out["updated_moves_first"] == "2 [home|password2] [cafe|]"
    assert out["five_oldest_dropped"] == "5 [d|dddddddd] [c|cccccccc] [b|bbbbbbbb] [a|aaaaaaaa] [home|password2]"


def test_stored_form_round_trips_and_refuses_a_cut_one(out):
    assert out["unpack"] == "1" and out["roundtrip"] == out["five_oldest_dropped"]
    assert out["unpack_truncated"] == "0"
    assert out["del_c"] == "1" and out["del_missing"] == "0"
    assert out["after_del"] == "4 [d|dddddddd] [b|bbbbbbbb] [a|aaaaaaaa] [home|password2]"


def test_from_the_phone(out):
    assert out["ble_add"] == "1" and out["ble_add_crlf"] == "1" and out["ble_bad"] == "0"
    assert out["ble"] == "2 [Office|officepass] [My Home 5G|secret pass!]"
    assert out["ble_del"] == "1" and out["ble_after_del"] == "1 [Office|officepass]"


def test_from_usb_in_hex(out):
    assert out["usb_add"] == "0"  # three fields: refused, nothing guessed
    assert out["usb_add_ok"] == "1" and out["usb_bad_hex"] == "0"
    assert out["usb"] == "1 [Sam's Wi|p@ss word 9]"
    assert out["usb_del"] == "1"
    assert out["usb_open"] == "1" and out["usb_open_net"] == "1 [Cafe|]"  # an open network


def test_pairing_line(out):
    assert out["pair"] == "1 00 1f ff 00"
    assert out["pair_short"] == "0" and out["pair_one"] == "0"
