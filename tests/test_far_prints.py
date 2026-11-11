"""A voiceprint far from its own person's others (a blend) is left out of naming (pipeline review 11 Nov, F2)."""

import numpy as np

from octo import voice as V
from octo.db import connect


def _vec(*xs):
    v = np.zeros(8, dtype=np.float32)
    v[:len(xs)] = xs
    return v


def test_a_print_far_from_its_persons_others_is_left_out(tmp_path):
    conn = connect(tmp_path / "v.db")
    a, b = V.ensure_person(conn, "A"), V.ensure_person(conn, "B")
    for i, e in enumerate([_vec(1, 0.1), _vec(1, 0.2), _vec(1, 0.15)]):
        V.add_voiceprint(conn, a["id"], e, 20.0, f"label:s{i}:A")
    before = V.voiceprints_version(conn)
    V.add_voiceprint(conn, a["id"], _vec(0, 0, 1), 60.0, "label:s9:A")  # A's label on someone else
    V.add_voiceprint(conn, b["id"], _vec(0, 1), 20.0, "label:s1:B")
    V.add_voiceprint(conn, b["id"], _vec(0, 0, 0, 1), 20.0, "label:s2:B")  # B has one other only: not judged
    got = V.all_voiceprints(conn)
    assert sum(p["person_id"] == a["id"] for p in got) == 3 and sum(p["person_id"] == b["id"] for p in got) == 2
    assert not any(np.array_equal(p["embedding"], _vec(0, 0, 1)) for p in got)
    assert V.voiceprints_version(conn) != before  # B's prints still count


def test_the_version_ignores_a_far_print(tmp_path):
    conn = connect(tmp_path / "v.db")
    a = V.ensure_person(conn, "A")
    for i, e in enumerate([_vec(1, 0.1), _vec(1, 0.2), _vec(1, 0.15)]):
        V.add_voiceprint(conn, a["id"], e, 20.0, f"label:s{i}:A")
    before = V.voiceprints_version(conn)
    V.add_voiceprint(conn, a["id"], _vec(0, 0, 1), 60.0, "label:s9:A")
    assert V.voiceprints_version(conn) == before  # identify doesn't re-run for a print naming can't use
