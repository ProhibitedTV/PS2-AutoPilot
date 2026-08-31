from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.nfs_hot_pursuit_2 import NfsPhase
from ps2_autopilot.profiles.nfs_hot_pursuit_2_v2 import (
    NfsHotPursuit2V2Profile,
    NfsRoute,
    NfsScreen,
)
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


def _road_frame() -> np.ndarray:
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    frame[:] = (45, 125, 45)
    polygon = np.array([[250, 155], [390, 155], [610, 350], [25, 350]], dtype=np.int32)
    cv2.fillConvexPoly(frame, polygon, (112, 112, 112))
    cv2.polylines(frame, [polygon], True, (195, 195, 195), 4)
    return frame


def _flat() -> np.ndarray:
    return np.full((360, 640, 3), 90, dtype=np.uint8)


def _ctx(label: str | None, *, now: float = 10.0, frame: np.ndarray | None = None, motion: float = 0.0):
    template = None if label is None else TemplateMatch(label, 0.96)
    return ProfileContext(frame=_flat() if frame is None else frame, motion=motion, template=template, now=now)


def _taps(controller: FakeController) -> list[str]:
    return [event[1] for event in controller.events if event and event[0] == "tap"]


def test_default_route_moves_root_selection_to_world_racing_then_confirms():
    profile = NfsHotPursuit2V2Profile({})
    controller = FakeController()

    action = profile.tick(controller, _ctx("nfs_main_menu_hot_pursuit_selected", now=10.0))
    assert profile.route is NfsRoute.WORLD_RACING_QUICK_RACE
    assert profile.screen is NfsScreen.MAIN_HOT_PURSUIT
    assert "tap down" in action
    assert _taps(controller) == ["down"]

    controller.events.clear()
    action = profile.tick(controller, _ctx("nfs_main_menu_world_racing_selected", now=11.1))
    assert "tap confirm" in action
    assert _taps(controller) == ["confirm"]


def test_world_quick_race_route_rejects_generic_menu_but_confirms_selected_quick_race():
    profile = NfsHotPursuit2V2Profile({})
    controller = FakeController()

    action = profile.tick(controller, _ctx("nfs_world_racing_menu", now=20.0))
    assert "fail-closed" in action
    assert not _taps(controller)

    controller.events.clear()
    action = profile.tick(controller, _ctx("nfs_world_racing_quick_race_selected", now=21.1))
    assert "tap confirm" in action
    assert _taps(controller) == ["confirm"]


def test_championship_route_walks_down_from_quick_race():
    profile = NfsHotPursuit2V2Profile({"menu_route": "championship"})
    controller = FakeController()

    action = profile.tick(controller, _ctx("nfs_world_racing_quick_race_selected", now=30.0))
    assert "tap down" in action
    assert _taps(controller) == ["down"]


def test_replay_owns_controller_before_racing_phase_can_leak_inputs():
    profile = NfsHotPursuit2V2Profile({"replay_hold_seconds": 2.0, "race_enter_frames": 1})
    profile.phase = NfsPhase.RACING
    controller = FakeController()

    action = profile.tick(controller, _ctx("nfs_replay", now=40.0, frame=_road_frame(), motion=0.02))
    assert action == "replay: preserve broadcast"
    assert "cross" not in _taps(controller)
    assert not any(event[:2] == ("hold", "cross") for event in controller.events)

    controller.events.clear()
    action = profile.tick(controller, _ctx("nfs_replay", now=42.2, frame=_road_frame(), motion=0.02))
    assert "tap start" in action
    assert _taps(controller) == ["start"]


def test_cop_hud_uses_ps2_siren_target_and_r3_boost_only_in_cop_mode():
    profile = NfsHotPursuit2V2Profile(
        {
            "drive_confidence": 0.20,
            "corner_brake_threshold": 1.0,
            "cop_boost_interval_seconds": 1.5,
        }
    )
    controller = FakeController()

    action = profile.tick(
        controller,
        _ctx("nfs_cop_hud", now=50.0, frame=_road_frame(), motion=0.02),
    )
    assert profile.drive_mode == "cop"
    assert "siren/target" in action
    assert "boost" in action
    assert "circle" in _taps(controller)
    assert "r3" in _taps(controller)

    controller.events.clear()
    action = profile.tick(
        controller,
        _ctx("nfs_race_hud", now=52.0, frame=_road_frame(), motion=0.02),
    )
    assert profile.drive_mode == "racer"
    assert "r3" not in _taps(controller)
    assert "circle" not in _taps(controller)


def test_paused_screen_resumes_with_start_after_bounded_hold():
    profile = NfsHotPursuit2V2Profile({"pause_resume_seconds": 1.0})
    controller = FakeController()

    first = profile.tick(controller, _ctx("nfs_pause_menu", now=60.0))
    assert first == "pause: bounded hold before resume"

    controller.events.clear()
    later = profile.tick(controller, _ctx("nfs_pause_menu", now=61.2))
    assert "tap start" in later
    assert _taps(controller) == ["start"]
