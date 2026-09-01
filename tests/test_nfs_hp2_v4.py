from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.nfs_hp2_hazards import estimate_near_hazard
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.nfs_hot_pursuit_2 import NfsPhase
from ps2_autopilot.profiles.nfs_hot_pursuit_2_v4 import NfsHotPursuit2V4Profile
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


def _road_frame(*, obstacle: bool = False) -> np.ndarray:
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    frame[:] = (45, 125, 45)
    polygon = np.array([[250, 155], [390, 155], [610, 350], [25, 350]], dtype=np.int32)
    cv2.fillConvexPoly(frame, polygon, (112, 112, 112))
    cv2.polylines(frame, [polygon], True, (195, 195, 195), 4)
    if obstacle:
        cv2.rectangle(frame, (285, 215), (355, 258), (40, 40, 210), -1)
    return frame


def _flat() -> np.ndarray:
    return np.full((360, 640, 3), 90, dtype=np.uint8)


def _ctx(
    label: str | None,
    *,
    now: float = 10.0,
    frame: np.ndarray | None = None,
    motion: float = 0.02,
) -> ProfileContext:
    template = None if label is None else TemplateMatch(label, 0.96)
    return ProfileContext(
        frame=_flat() if frame is None else frame,
        motion=motion,
        template=template,
        now=now,
    )


def _taps(controller: FakeController) -> list[str]:
    return [event[1] for event in controller.events if event and event[0] == "tap"]


def test_near_hazard_estimator_finds_compact_object_in_road_funnel():
    hazard = estimate_near_hazard(_road_frame(obstacle=True))
    assert hazard.confidence > 0.35
    assert abs(hazard.center_x) < 0.25
    assert hazard.proximity > 0.30
    assert hazard.width > 0.04


def test_countdown_preloads_throttle_without_waiting_for_race_takeover():
    profile = NfsHotPursuit2V4Profile({"countdown_preload_throttle": True})
    controller = FakeController()

    action = profile.tick(controller, _ctx("nfs_countdown", now=10.0, motion=0.0))

    assert profile.phase is NfsPhase.COUNTDOWN
    assert action == "countdown: preload throttle"
    assert ("hold", "cross") in controller.events
    assert profile.countdown_preload_ticks == 1


def test_directional_roadblock_template_owns_bounded_avoidance_bias():
    profile = NfsHotPursuit2V4Profile(
        {
            "drive_confidence": 0.20,
            "corner_brake_threshold": 1.0,
            "template_hazard_strength": 0.75,
        }
    )
    controller = FakeController()

    action = profile.tick(
        controller,
        _ctx(
            "nfs_race_hud_roadblock_avoid_left",
            now=20.0,
            frame=_road_frame(),
            motion=0.02,
        ),
    )

    assert profile.phase is NfsPhase.RACING
    assert profile.template_hazard_kind == "roadblock"
    assert controller.left_stick[0] < -0.20
    assert "hazard=roadblock" in action
    assert profile.hazard_avoid_ticks == 1


def test_image_only_hazard_avoidance_stays_disabled_by_default():
    profile = NfsHotPursuit2V4Profile(
        {
            "race_enter_frames": 1,
            "race_enter_confidence": 0.20,
            "drive_confidence": 0.20,
            "corner_brake_threshold": 1.0,
        }
    )
    controller = FakeController()

    action = profile.tick(
        controller,
        _ctx(None, now=30.0, frame=_road_frame(obstacle=True), motion=0.02),
    )

    assert profile.hazard.confidence > 0.0
    assert profile.obstacle_avoid_enabled is False
    assert "hazard=vision" not in action
    assert profile.hazard_avoid_ticks == 0


def test_repeated_recovery_alternates_side_and_scales_duration():
    profile = NfsHotPursuit2V4Profile({"recovery_duration_step": 0.25})
    controller = FakeController()
    profile.last_steer = 0.6

    profile._start_recovery(controller, _ctx(None, now=40.0, frame=_road_frame()), "wall")
    first_direction = profile.recovery_direction
    first_scale = profile._recovery_scale()

    profile.phase = NfsPhase.RACING
    profile._start_recovery(controller, _ctx(None, now=45.0, frame=_road_frame()), "wall")

    assert profile.recovery_streak == 2
    assert profile.recovery_direction == -first_direction
    assert profile._recovery_scale() > first_scale
    assert profile.recovery_escalations == 1


def test_busted_only_confirms_when_continue_template_owns_the_action():
    profile = NfsHotPursuit2V4Profile({"busted_hold_seconds": 2.0})
    controller = FakeController()

    first = profile.tick(controller, _ctx("nfs_busted_continue", now=50.0, motion=0.0))
    assert first == "busted: preserve presentation before continue"
    assert not _taps(controller)

    controller.events.clear()
    second = profile.tick(controller, _ctx("nfs_busted_continue", now=52.2, motion=0.0))
    assert "tap confirm" in second
    assert _taps(controller) == ["confirm"]

    controller.events.clear()
    third = profile.tick(controller, _ctx("nfs_busted_continue", now=53.5, motion=0.0))
    assert third == "busted: continue sent; awaiting visual progress"
    assert not _taps(controller)


def test_cop_target_can_refresh_after_arrest_when_hud_requests_new_target():
    profile = NfsHotPursuit2V4Profile(
        {
            "drive_confidence": 0.20,
            "corner_brake_threshold": 1.0,
            "cop_target_refresh_seconds": 1.0,
            "cop_boost_interval_seconds": 100.0,
        }
    )
    controller = FakeController()

    profile.tick(
        controller,
        _ctx("nfs_cop_hud_target_needed", now=60.0, frame=_road_frame(), motion=0.02),
    )
    assert "circle" in _taps(controller)
    assert profile.cop_target_refreshes == 0

    controller.events.clear()
    action = profile.tick(
        controller,
        _ctx("nfs_cop_hud_target_needed", now=61.2, frame=_road_frame(), motion=0.02),
    )
    assert "retarget" in action
    assert "circle" in _taps(controller)
    assert profile.cop_target_refreshes == 1


def test_cop_support_call_requires_positive_ready_template_by_default():
    profile = NfsHotPursuit2V4Profile(
        {
            "drive_confidence": 0.20,
            "corner_brake_threshold": 1.0,
            "cop_support_enabled": True,
            "cop_support_interval_seconds": 8.0,
            "cop_boost_interval_seconds": 100.0,
        }
    )
    controller = FakeController()

    profile.tick(
        controller,
        _ctx("nfs_cop_hud", now=70.0, frame=_road_frame(), motion=0.02),
    )
    assert "r2" not in _taps(controller)
    assert "l2" not in _taps(controller)

    controller.events.clear()
    action = profile.tick(
        controller,
        _ctx("nfs_cop_hud_roadblock_ready", now=79.0, frame=_road_frame(), motion=0.02),
    )
    assert "roadblock" in action
    assert "r2" in _taps(controller)
    assert profile.cop_support_calls == 1


def test_extreme_handling_preset_is_more_conservative_than_classic_defaults():
    classic = NfsHotPursuit2V4Profile({"handling_mode": "classic"})
    extreme = NfsHotPursuit2V4Profile({"handling_mode": "extreme"})

    assert extreme.steering_gain < classic.steering_gain
    assert extreme.curvature_gain < classic.curvature_gain
    assert extreme.steering_smoothing >= classic.steering_smoothing
