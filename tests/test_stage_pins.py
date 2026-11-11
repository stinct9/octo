"""Every stage's version and parameters, pinned: a change here re-runs every conversation, which is the owner's call
(pipeline review 11 Nov). The split tiers, the voiceprint filter and the ETA change live outside the stages."""

import hashlib
import json

from octo.stages import STAGES


def test_no_stage_version_or_parameter_moved():
    got = hashlib.sha256(json.dumps([(n, s.version, s.params) for n, s in STAGES.items()], sort_keys=True).encode())
    assert got.hexdigest()[:16] == "43b8f9fbd1ee0df9"
