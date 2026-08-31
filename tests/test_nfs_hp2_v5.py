from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.nfs_hot_pursuit_2 import NfsPhase
from ps2_autopilot.profiles.nfs_hot_pursuit_2_v5 import NfsHotPursuit2V5Profile
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


def _road_frame(*, obstacle_x: int | None = None) -> np.ndarray:
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    frame[:] = (45, 125, 45)
    polygon = np.array([[250, 155], [390, 155], [610, 350], [25, 350]], dtype=np.int32)
    cv2.fillConvexPoly(frame, polygon, (112, 112, 112))
    cv2.polylines(frame, [polygon], True, (195, 195, 195), 4)
    if obstacle_x is not None:
        cv2.rectangle(frame, (obstacle_x - 30, 215), (obstacle_x + 30, 265), (40, 40, 210), -1)
    return frame


def _ctx(
    label: str | None,
    *,
    now: float,
    frame: np.ndarray | None = None,
    motion: float = 0.02,
) -> ProfileContext:
    template = None if label is None else TemplateMatch(label, 0.96)
    return ProfileContext(
        frame=_road_frame() if frame is None else frame,
        motion=motion,
        template=template,
        now=now,
    )


def test_single_vision_candidate_cannot_steer_even_when_opted_in():
    profile = NfsHotPursuit2V5Profile(
        {
            "obstacle_avoid_enabled": True,
            "hazard_confirm_frames": 3,
            "hazard_confidence_threshold": 0.25,
            "hazard_proximity_threshold": 0.25,
            "drive_confidence": 0.20,
            "race_enter_frames": 1,
            "race_enter_confidence": 0.20,
            "corner_brake_threshold": 1.0,
        }
    )
    controller = FakeController()

    action = profile.tick(
        controller,
        _ctx(None, now=10.0, frame=_road_frame(obstacle_x=320)),
    )

    assert profile.hazard_track_streak == 1
    assert profile.hazard_track_confirmed is False
    assert "hazard=vision" not in action


def test_persistent_traffic_candidate_confirms_and_latches_pass_side():
    profile = NfsHotPursuit2V5Profile(
        {
            "obstacle_avoid_enabled": True,
            "hazard_confirm_frames": 3,
            "hazard_confidence_threshold": 0.25,
            "hazard_proximity_threshold": 0.25,
            "hazard_center_tolerance": 0.35,
            "drive_confidence": 0.20,
            "race_enter_frames": 1,
            "race_enter_confidence": 0.20,
            "corner_brake_threshold": 1.0,
        }
    )
    controller = FakeController()

    for i, x in enumerate((360, 364, 358)):
        action = profile.tick(
            controller,
            _ctx(None, now=20.0 + i * 0.08, frame=_road_frame(obstacle_x=x)),
        )

    assert profile.hazard_track_confirmed is True
    assert profile.hazard_track_confirmations == 1
    assert profile.hazard_track_direction < 0.0
    assert "hazard=vision" in action
    assert controller.left_stick[0] < 0.0


def test_large_candidate_jump_restarts_temporal_track_instead_of_flipping_immediately():
    profile = NfsHotPursuit2V5Profile(
        {
            "hazard_confirm_frames": 3,
            "hazard_confidence_threshold": 0.25,
            "hazard_proximity_threshold": 0.25,
            "hazard_center_tolerance": 0.12,
        }
    )

    for i in range(2):
        profile._observe_hazard(
            _ctx(None, now=30.0 + i * 0.08, frame=_road_frame(obstacle_x=380))
        )
    profile._observe_hazard(_ctx(None, now=30.16, frame=_road_frame(obstacle_x=260)))

    assert profile.hazard_track_streak == 1
    assert profile.hazard_track_restarts >= 1
    assert profile.hazard_track_confirmed is False


def test_shortcut_template_latches_bounded_directional_line_bias():
    profile = NfsHotPursuit2V5Profile(
        {
            "drive_confidence": 0.20,
            "corner_brake_threshold": 1.0,
            "shortcut_strength": 0.65,
            "shortcut_blend": 1.0,
        }
    )
    controller = FakeController()

    action = profile.tick(
        controller,
        _ctx("nfs_race_hud_shortcut_enter_left", now=40.0, frame=_road_frame()),
    )

    assert profile.phase is NfsPhase.RACING
    assert profile.shortcut_events == 1
    assert profile.shortcut_bias < 0.0
    assert profile.shortcut_ticks == 1
    assert controller.left_stick[0] < 0.0
    assert "shortcut=" in action


def test_shortcut_is_suppressed_by_higher_priority_roadblock_avoidance():
    profile = NfsHotPursuit2V5Profile(
        {
            "drive_confidence": 0.20,
            "corner_brake_threshold": 1.0,
            "shortcut_strength": 0.80,
        }
    )
    controller = FakeController()

    profile.tick(
        controller,
        _ctx("nfs_race_hud_shortcut_enter_left", now=50.0, frame=_road_frame()),
    )
    controller.events.clear()

    action = profile.tick(
        controller,
        _ctx("nfs_race_hud_roadblock_avoid_right", now=50.2, frame=_road_frame()),
    )

    assert "hazard=roadblock" in action
    assert controller.left_stick[0] > 0.0
    assert profile.shortcut_suppressed_ticks >= 1


def test_police_ram_left_template_evades_right_and_coasts():
    profile = NfsHotPursuit2V5Profile(
        {
            "drive_confidence": 0.20,
            "corner_brake_threshold": 1.0,
            "pursuit_evasion_strength": 0.80,
        }
    )
    controller = FakeController()

    action = profile.tick(
        controller,
        _ctx("nfs_hot_pursuit_hud_police_ram_left", now=60.0, frame=_road_frame()),
    )

    assert profile.pursuit_threat_kind == "police_ram"
    assert profile.pursuit_threat_bias > 0.0
    assert controller.left_stick[0] > 0.0
    assert "hazard=police_ram" in action
    assert ("release", "cross") in controller.events


def test_shortcut_and_police_attack_labels_claim_racing_semantics():
    assert (
        NfsHotPursuit2V5Profile._screen_from_template("nfs_shortcut_take_right")
        .value
        == "racing"
    )
    assert (
        NfsHotPursuit2V5Profile._screen_from_template("nfs_police_attack_left")
        .value
        == "racing"
    )
