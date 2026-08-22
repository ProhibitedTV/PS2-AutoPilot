import cv2
import numpy as np

from ps2_autopilot.madden_vision import MaddenVision, MaddenVisualState


def green_frame() -> np.ndarray:
    hsv = np.zeros((360, 640, 3), dtype=np.uint8)
    hsv[:, :, 0] = 55
    hsv[:, :, 1] = 180
    hsv[:, :, 2] = 130
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def test_green_field_idle():
    vision = MaddenVision(field_green_threshold=0.2, live_motion_threshold=0.02)
    obs = vision.observe(green_frame(), motion=0.002, template=None)
    assert obs.state == MaddenVisualState.FIELD_IDLE
    assert obs.green_ratio > 0.9


def test_green_field_live():
    vision = MaddenVision(field_green_threshold=0.2, live_motion_threshold=0.02)
    obs = vision.observe(green_frame(), motion=0.05, template=None)
    assert obs.state == MaddenVisualState.LIVE_PLAY
