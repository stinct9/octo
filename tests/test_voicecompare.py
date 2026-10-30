import numpy as np

from bench.voicecompare import asnorm, dprime, eer, identify_all, naming, slices, todays_rule, trials


def test_eer_perfect_and_overlapping():
    assert eer([0.9, 0.8], [0.1, 0.2]) == 0.0
    assert eer([0.5, 0.5], [0.5, 0.5]) == 0.5
    assert np.isclose(eer([0.9, 0.8, 0.3], [0.4, 0.2, 0.1]), 1 / 3)  # one of three wrong on each side


def test_dprime_orders_separation():
    assert dprime([0.9, 0.8], [0.1, 0.2]) > dprime([0.6, 0.5], [0.4, 0.3]) > 0


def test_asnorm_cancels_a_shift_shared_by_score_and_cohort():
    cohort = np.array([0.1, 0.2, 0.3])
    assert np.isclose(asnorm(0.5, cohort, cohort), asnorm(0.7, cohort + 0.2, cohort + 0.2))


def test_slices_spread_and_bounded():
    speech = np.arange(16000 * 95, dtype=np.float32)
    got = slices(speech, size=10, most=3)
    assert [len(s) for s in got] == [160000] * 3
    assert got[0][0] == 0 and got[-1][0] == 8 * 160000  # first and last whole slice
    assert slices(speech[:16000 * 9]) == []


def _people():
    """Three people with clear directions; A and B in two conversations, C once (no print elsewhere)."""
    rng = np.random.default_rng(0)
    axes = {p: np.eye(8)[i] for i, p in enumerate("ABC")}
    items, E = [], {}
    for n, (person, session) in enumerate([("A", 1), ("B", 1), ("A", 2), ("B", 2), ("C", 3), ("A", 3)]):
        key = f"f{n}"
        items.append({"key": key, "kind": "long", "person": person, "session": session, "mic": "m", "seconds": 30})
        E[key] = axes[person] + 0.1 * rng.standard_normal(8)
    return items, E


def test_trials_cross_conversation_only():
    items, E = _people()
    tr = trials(items, E, "long", False)
    assert len(tr) == 15 - 3  # every pair, minus the 3 within-conversation ones
    assert min(t["score"] for t in tr if t["same"]) > max(t["score"] for t in tr if not t["same"])


def test_identify_leaves_out_own_conversation_and_flags_unknowns():
    items, E = _people()
    ids = identify_all(items, E, "long", False)
    assert {(r["person"], r["known"]) for r in ids} >= {("A", True), ("B", True), ("C", False)}
    nm = naming(ids)
    assert nm["right"] == nm["known"] == 5 and nm["unknown"] == 1
    assert nm["named"] == 5  # C scores low against everyone, so the bar sits under the right names
    assert todays_rule(ids)["wrong"] == 0


def test_wespeaker_fbank_is_80_bins_every_10_ms_and_centred():
    from octo.voice import wespeaker_fbank

    f = wespeaker_fbank(np.random.default_rng(1).standard_normal(16000).astype(np.float32) * 0.1)
    assert f.shape == (1, 98, 80)  # 1 s: (16000 - 400) // 160 + 1 frames
    assert np.allclose(f[0].mean(0), 0, atol=1e-4)
