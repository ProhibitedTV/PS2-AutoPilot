import numpy as np

from ps2_autopilot.vision import motion_score


def test_motion_score_identical_is_zero():
    frame = np.zeros((180, 320, 3), dtype=np.uint8)
    assert motion_score(frame, frame) == 0.0


def test_motion_score_detects_change():
    a = np.zeros((180, 320, 3), dtype=np.uint8)
    b = np.full((180, 320, 3), 255, dtype=np.uint8)
    assert motion_score(a, b) > 0.9
