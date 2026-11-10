from bench.splitreplay import _around


def test_around_is_the_silence_either_side_of_the_cut():
    turns = [{"speaker": "x", "start": 0, "end": 10}, {"speaker": "y", "start": 25, "end": 30}]
    assert _around(turns, 15) == 15
