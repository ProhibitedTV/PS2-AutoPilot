from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.nfs_hp2_vision import estimate_road
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.nfs_hot_pursuit_2 import NfsHotPursuit2V1Profile, NfsPhase
from ps2_autopilot.vision import TemplateMatch


class FakeController(Controller):
    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.left_stick = (0.0, 0.0)

    def tap(self, action: str, duration: float = 0.08) -> None:
        self.events.append(("tap", action, duration))

    def hold(self, action: str) -> None:
        self.events.append(("hold", action))

    def release(self, action: str) -> None:
        self.events.append(("release", action))

    def release_all(self) -> None:
        self.events.append(("release_all",))

    def set_left_stick(self, x: float, y: float) -> None:
        self.left_stick = (x, y)
        self.events.append(("left_stick", x, y))

    def set_right_stick(self, x: float, y: float) -> None:
        self.events.append(("right_stick", x, y))


def _road_frame(*, curve_right: bool = False) -> np.ndarray:
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    frame[:] = (45, 125, 45)
    if curve_right:
        polygon = np.array([[345, 155], [465, 155], [610, 350], [25, 350]], dtype=np.int32)
    else:
        polygon = np.array([[250, 155], [390, 155], [610, 350], [25, 350]], dtype=np.int32)
    cv2.fillConvexPoly(frame, polygon, (112, 112, 112))
    # Add lane-edge variation so the synthetic scene is not unrealistically flat.
    cv2.polylines(frame, [polygon], True, (195, 195, 195), 4)
    return frame


def _ctx(
    frame: np.ndarray,
    *,
    now: float = 1.0,
    motion: float = 0.02,
    template: TemplateMatch | None = None,
) -> ProfileContext:
    return ProfileContext(frame=frame, motion=motion, template=template, now=now)


def test_road_estimator_finds_centered_corridor():
    road = estimate_road(_road_frame())
    assert road.confidence > 0.50
    assert abs(road.center_x) < 0.15
    assert road.width > 0.40


def test_road_estimator_sees_right_bend():
    road = estimate_road(_road_frame(curve_right=True))
    assert road.confidence > 0.45
    assert road.curvature > 0.10
    assert road.center_x > 0.02


def test_road_estimator_rejects_flat_menu_like_frame():
    frame = np.full((360, 640, 3), 90, dtype=np.uint8)
    road = estimate_road(frame)
    assert road.confidence == 0.0


def test_profile_can_take_over_after_manual_race_entry():
    profile = NfsHotPursuit2V1Profile(
        {
            "race_enter_frames": 1,
            "race_enter_confidence": 0.35,
            "drive_confidence": 0.25,
            "corner_brake_threshold": 1.0,
        }
    )
    controller = FakeController()

    action = profile.tick(controller, _ctx(_road_frame(), now=10.0))

    assert profile.phase is NfsPhase.RACING
    assert action.startswith("racing:")
    assert ("hold", "cross") in controller.events
    assert abs(controller.left_stick[0]) < 0.25


def test_unknown_non_race_screen_fails_closed():
    profile = NfsHotPursuit2V1Profile({"race_enter_frames": 1})
    controller = FakeController()
    frame = np.full((360, 640, 3), 90, dtype=np.uint8)

    action = profile.tick(controller, _ctx(frame, now=5.0, motion=0.0))

    assert profile.phase is NfsPhase.CALIBRATION
    assert "fail-closed" in action
    assert not any(event[:2] == ("tap", "confirm") for event in controller.events)
    assert not any(event[:2] == ("tap", "start") for event in controller.events)


def test_recognized_press_start_template_owns_title_input():
    profile = NfsHotPursuit2V1Profile({"template_threshold": 0.80})
    controller = FakeController()
    template = TemplateMatch("nfs_press_start", 0.95)

    action = profile.tick(
        controller,
        _ctx(np.full((360, 640, 3), 90, dtype=np.uint8), now=7.0, motion=0.0, template=template),
    )

    assert profile.phase is NfsPhase.TITLE
    assert "tap start" in action
    assert any(event[:2] == ("tap", "start") for event in controller.events)


def test_results_screen_is_preserved_before_confirming():
    profile = NfsHotPursuit2V1Profile({"results_hold_seconds": 3.0})
    controller = FakeController()
    frame = np.full((360, 640, 3), 90, dtype=np.uint8)
    template = TemplateMatch("nfs_results", 0.95)

    first = profile.tick(controller, _ctx(frame, now=20.0, motion=0.0, template=template))
    assert first == "results: preserve presentation"

    controller.events.clear()
    later = profile.tick(controller, _ctx(frame, now=23.2, motion=0.0, template=template))
    assert "tap confirm" in later
    assert any(event[:2] == ("tap", "confirm") for event in controller.events)
