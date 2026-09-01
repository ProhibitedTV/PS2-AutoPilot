from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.nfs_hp2_hud import estimate_gameplay_hud
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.nfs_hot_pursuit_2 import NfsPhase
from ps2_autopilot.profiles.nfs_hot_pursuit_2_v7 import NfsHotPursuit2V7Profile


class FakeController(Controller):
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def tap(self, action: str, duration: float = 0.08) -> None:
        self.events.append(("tap", action, duration))

    def hold(self, action: str) -> None:
        self.events.append(("hold", action))

    def release(self, action: str) -> None:
        self.events.append(("release", action))

    def release_all(self) -> None:
        self.events.append(("release_all",))

    def set_left_stick(self, x: float, y: float) -> None:
        self.events.append(("left_stick", x, y))

    def set_right_stick(self, x: float, y: float) -> None:
        self.events.append(("right_stick", x, y))


def _hud_frame() -> np.ndarray:
    frame = np.full((360, 640, 3), (55, 70, 45), dtype=np.uint8)

    cv2.rectangle(frame, (5, 5), (175, 125), (10, 10, 10), -1)
    cv2.rectangle(frame, (20, 20), (115, 85), (45, 150, 235), -1)
    cv2.line(frame, (10, 100), (170, 100), (220, 220, 220), 4)
    cv2.line(frame, (20, 116), (150, 116), (220, 220, 220), 3)

    cv2.rectangle(frame, (435, 10), (625, 62), (28, 28, 28), -1)
    for y in (18, 34, 50):
        cv2.line(frame, (450, y), (605, y), (45, 145, 225), 5)
        cv2.line(frame, (520, y + 5), (610, y + 5), (210, 210, 210), 2)

    cv2.rectangle(frame, (10, 130), (175, 220), (10, 10, 10), -1)
    pts = np.array([[25, 205], [55, 165], [95, 190], [150, 145]], dtype=np.int32)
    cv2.polylines(frame, [pts], False, (225, 225, 225), 5)

    cv2.rectangle(frame, (485, 200), (630, 330), (8, 8, 8), -1)
    cv2.ellipse(frame, (555, 282), (62, 62), 0, 205, 340, (40, 135, 225), 7)
    cv2.ellipse(frame, (555, 282), (58, 58), 0, 320, 350, (40, 40, 220), 6)
    for x in range(500, 620, 20):
        cv2.line(frame, (x, 260), (x + 8, 250), (220, 220, 220), 2)
    return frame


def _menu_frame() -> np.ndarray:
    frame = np.full((360, 640, 3), 38, dtype=np.uint8)
    cv2.rectangle(frame, (70, 120), (570, 250), (62, 62, 62), -1)
    cv2.line(frame, (120, 180), (520, 180), (100, 100, 100), 3)
    return frame


def _ctx(frame: np.ndarray, *, now: float = 10.0, motion: float = 0.0) -> ProfileContext:
    return ProfileContext(frame=frame, motion=motion, template=None, now=now)


def test_fixed_hud_detector_separates_gameplay_from_plain_menu():
    hud = estimate_gameplay_hud(_hud_frame())
    menu = estimate_gameplay_hud(_menu_frame())

    assert hud.confidence >= 0.82
    assert hud.rank_score >= 0.72
    assert hud.status_score >= 0.72
    assert hud.tach_score >= 0.72
    assert menu.confidence < 0.55


def test_hud_claims_racing_even_when_road_and_motion_are_zero():
    profile = NfsHotPursuit2V7Profile(
        {
            "hud_gameplay_threshold": 0.82,
            "drive_confidence": 0.95,
            "road_loss_recovery_seconds": 2.2,
        }
    )
    controller = FakeController()

    action = profile.tick(controller, _ctx(_hud_frame(), now=20.0, motion=0.0))

    assert profile.phase is NfsPhase.RACING
    assert profile.hud_gameplay_claims == 1
    assert "hud-owned gameplay" in action
    assert not any(event[:2] == ("tap", "start") for event in controller.events)
    assert not any(event[:2] == ("tap", "confirm") for event in controller.events)


def test_persistent_hud_road_loss_enters_racing_recovery_not_bootstrap():
    profile = NfsHotPursuit2V7Profile(
        {
            "hud_gameplay_threshold": 0.82,
            # Road confidence is bounded to <= 1.0; force the road-loss branch so
            # this regression tests ownership rather than the synthetic pavement.
            "drive_confidence": 1.01,
            "road_loss_recovery_seconds": 0.5,
        }
    )
    controller = FakeController()
    frame = _hud_frame()

    profile.tick(controller, _ctx(frame, now=30.0, motion=0.0))
    action = profile.tick(controller, _ctx(frame, now=30.6, motion=0.0))

    assert profile.phase is NfsPhase.RECOVERY
    assert "recovery start" in action
    assert "unattended bootstrap" not in action


def test_watchdog_uses_racing_recovery_when_last_frame_had_gameplay_hud():
    profile = NfsHotPursuit2V7Profile({"hud_gameplay_threshold": 0.82})
    controller = FakeController()

    profile.tick(controller, _ctx(_hud_frame(), now=40.0, motion=0.0))
    profile.phase = NfsPhase.CALIBRATION
    controller.events.clear()
    result = profile.recover(controller)

    assert profile.phase is NfsPhase.RECOVERY
    assert profile.hud_watchdog_promotions == 1
    assert "shared motion watchdog" in result
    assert not any(event[:2] == ("tap", "start") for event in controller.events)
