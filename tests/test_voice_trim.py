"""A speaker's embedding takes at most MAX_SPEECH_S of their talk (B3, 13 Oct): one long turn used to go in whole,
up to 223 s, which CoreML refuses and the CPU took 22 s to embed."""

import numpy as np

from octo import RATE
from octo import voice as V


class Lengths(V.Embedder):
    def __init__(self):  # no model: the "embedding" is how many seconds it was given
        pass

    def embed(self, samples):
        return np.array([len(samples) / RATE], dtype=np.float32)


def test_one_long_turn_is_cut_to_the_cap():
    samples = np.zeros(300 * RATE, dtype=np.float32)
    emb, total = Lengths().embed_turns(samples, [(0.0, 223.0), (230.0, 240.0)])
    assert float(emb[0]) == V.MAX_SPEECH_S and total == V.MAX_SPEECH_S


def test_turns_under_the_cap_are_all_used_longest_first():
    samples = np.zeros(100 * RATE, dtype=np.float32)
    emb, total = Lengths().embed_turns(samples, [(0.0, 20.0), (30.0, 60.0), (70.0, 75.0)])
    assert float(emb[0]) == 55.0 and total == 55.0


def test_the_turn_that_crosses_the_cap_is_cut_where_it_crosses():
    samples = np.zeros(200 * RATE, dtype=np.float32)
    emb, total = Lengths().embed_turns(samples, [(0.0, 50.0), (60.0, 90.0)])
    assert float(emb[0]) == V.MAX_SPEECH_S and total == V.MAX_SPEECH_S  # 50 s, then 10 of the next 30
