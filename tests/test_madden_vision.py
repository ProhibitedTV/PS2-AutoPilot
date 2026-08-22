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
    assert abs(obs.field_center_x) < 0.02


def test_green_field_live():
    vision = MaddenVision(field_green_threshold=0.2, live_motion_threshold=0.02)
    obs = vision.observe(green_frame(), motion=0.05, template=None)
    assert obs.state == MaddenVisualState.LIVE_PLAY


def test_field_centroid_tracks_green_side():
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    hsv_green = np.uint8([[[55, 180, 130]]])
    bgr_green = cv2.cvtColor(hsv_green, cv2.COLOR_HSV2BGR)[0, 0]
    frame[65:330, 40:280] = bgr_green

    vision = MaddenVision(field_green_threshold=0.1)
    obs = vision.observe(frame, motion=0.0, template=None)
    assert obs.green_ratio > 0.1
    assert obs.field_center_x < -0.1


def test_motion_centroid_follows_changed_region():
    previous = green_frame()
    current = previous.copy()
    cv2.rectangle(current, (430, 150), (560, 270), (255, 255, 255), -1)

    x = MaddenVision.motion_centroid_x(previous, current)
    assert x > 0.2


def test_numbered_playcall_template_still_maps_state():
    from ps2_autopilot.vision import TemplateMatch

    vision = MaddenVision(template_threshold=0.8)
    obs = vision.observe(green_frame(), 0.0, TemplateMatch("playcall_offense_01", 0.95))
    assert obs.state == MaddenVisualState.PLAYCALL
