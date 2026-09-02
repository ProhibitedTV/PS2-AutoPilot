import time

import cv2
import numpy as np

from ps2_autopilot.vision import TemplateDetector, TemplateMatch, motion_score


def test_motion_score_identical_is_zero():
    frame = np.zeros((180, 320, 3), dtype=np.uint8)
    assert motion_score(frame, frame) == 0.0


def test_motion_score_detects_change():
    a = np.zeros((180, 320, 3), dtype=np.uint8)
    b = np.full((180, 320, 3), 255, dtype=np.uint8)
    assert motion_score(a, b) > 0.9


def test_async_template_detection_does_not_block_control_loop(tmp_path):
    template = np.arange(32 * 32, dtype=np.uint8).reshape(32, 32)
    cv2.imwrite(str(tmp_path / "known.png"), template)
    detector = TemplateDetector(
        tmp_path,
        asynchronous=True,
        scan_interval_seconds=0.0,
        result_max_age_seconds=1.0,
    )

    def slow_scan(_frame):
        time.sleep(0.15)
        return TemplateMatch("known", 0.99), 150.0

    detector._scan_frame = slow_scan
    started = time.perf_counter()
    assert detector.best_match(np.zeros((64, 64, 3), dtype=np.uint8)) is None
    assert time.perf_counter() - started < 0.08

    result = None
    deadline = time.monotonic() + 1.0
    while result is None and time.monotonic() < deadline:
        time.sleep(0.02)
        result = detector.best_match(np.zeros((64, 64, 3), dtype=np.uint8))

    assert result == TemplateMatch("known", 0.99)
    stats = detector.telemetry()
    assert stats["template_scans_completed"] >= 1
    assert stats["template_scan_ms"] == 150.0
    detector.close()


def test_sync_template_detection_remains_available_for_tools_and_tests(tmp_path):
    rng = np.random.default_rng(7)
    template = rng.integers(0, 255, size=(24, 24), dtype=np.uint8)
    cv2.imwrite(str(tmp_path / "target.png"), template)
    frame = cv2.cvtColor(template, cv2.COLOR_GRAY2BGR)

    detector = TemplateDetector(tmp_path, asynchronous=False)
    result = detector.best_match(frame)

    assert result is not None
    assert result.name == "target"
    assert result.score > 0.99
    assert detector.telemetry()["template_scans_completed"] == 1
