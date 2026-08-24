import cv2
import numpy as np

from ps2_autopilot.profiles.jak_and_daxter_v20 import JakAndDaxterV20Profile


def profile(**extra):
    cfg = {
        "mode": "production",
        "ocr_enabled": False,
        "progress_probe_initial_delay_seconds": 999.0,
    }
    cfg.update(extra)
    return JakAndDaxterV20Profile(cfg)


def blue_frame(height=576, width=1024, value=180):
    hsv = np.zeros((height, width, 3), dtype=np.uint8)
    hsv[:, :, 0] = 100
    hsv[:, :, 1] = 200
    hsv[:, :, 2] = value
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def paint_textured_lower_blue(p, frame):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    h, w = frame.shape[:2]
    x0, x1, y0, y1 = p.WATER_ROI
    xa, xb = int(x0 * w), int(x1 * w)
    ya, yb = int(y0 * h), int(y1 * h)
    for y in range(ya, yb, 4):
        hsv[y:min(y + 4, yb), xa:xb, 2] = 90 if ((y - ya) // 4) % 2 == 0 else 230
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def test_uniform_full_view_blue_is_vetoed_before_new_water_ownership():
    p = profile()
    frame = blue_frame()

    mask = p._water_candidate_mask(frame)

    assert mask.size > 0
    assert np.count_nonzero(mask) == 0
    assert p.v20_sky_blue_upper_ratio > 0.95
    assert p.v20_sky_blue_lower_ratio > 0.95
    assert p.v20_sky_water_suppressions == 1


def test_uniform_blue_veto_does_not_interrupt_existing_swim_recovery():
    p = profile()
    p.water_escape_active = True
    frame = blue_frame()

    mask = p._water_candidate_mask(frame)

    assert np.mean(mask > 0) > 0.95
    assert p.v20_sky_water_suppressions == 0


def test_textured_lower_water_survives_sky_guard():
    p = profile()
    frame = paint_textured_lower_blue(p, blue_frame())

    mask = p._water_candidate_mask(frame)

    assert np.mean(mask > 0) > 0.95
    assert p.v20_sky_lower_edge > p.v20_sky_edge_max
    assert p.v20_sky_water_suppressions == 0


def test_side_only_blue_does_not_steer_when_center_corridor_is_dry(monkeypatch):
    p = profile()
    p.water_geometry_confirmed = False
    mask = np.zeros((100, 300), dtype=np.uint8)
    # V16 samples the bottom 24%. Fill half of only the right third: parent total
    # exceeds the old 0.11 threshold, but the center path is completely dry.
    mask[-12:, 200:300] = 1
    monkeypatch.setattr(p, "_water_candidate_mask", lambda frame: mask)

    risk = p._shoreline_metrics(np.zeros((100, 300, 3), dtype=np.uint8))

    assert risk.total > p.shoreline_guard_total
    assert risk.center == 0.0
    assert risk.right > 0.0
    assert risk.active is False
    assert p.v20_shoreline_side_only_suppressions == 1


def test_center_blue_still_activates_shoreline_guard(monkeypatch):
    p = profile()
    p.water_geometry_confirmed = False
    mask = np.zeros((100, 300), dtype=np.uint8)
    mask[-12:, 100:200] = 1
    monkeypatch.setattr(p, "_water_candidate_mask", lambda frame: mask)

    risk = p._shoreline_metrics(np.zeros((100, 300, 3), dtype=np.uint8))

    assert risk.center >= p.shoreline_guard_center
    assert risk.active is True
    assert p.v20_shoreline_side_only_suppressions == 0
