from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.nfs_hot_pursuit_2 import NfsPhase
from ps2_autopilot.profiles.nfs_hot_pursuit_2_v6 import NfsHotPursuit2V6Profile


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


def _menu_frame(value: int = 40) -> np.ndarray:
    frame = np.full((360, 640, 3), value, dtype=np.uint8)
    cv2.rectangle(frame, (55, 250), (585, 292), (value + 35, value + 35, value + 35), -1)
    return frame


def _road_frame() -> np.ndarray:
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    frame[:] = (45, 125, 45)
    polygon = np.array([[250, 155], [390, 155], [610, 350], [25, 350]], dtype=np.int32)
    cv2.fillConvexPoly(frame, polygon, (112, 112, 112))
    cv2.polylines(frame, [polygon], True, (195, 195, 195), 4)
    return frame


def _ctx(frame: np.ndarray, *, now: float, motion: float = 0.0) -> ProfileContext:
    return ProfileContext(frame=frame, motion=motion, template=None, now=now)


def test_unknown_screen_eventually_probes_start_instead_of_deadlocking():
    profile = NfsHotPursuit2V6Profile(
        {
            "bootstrap_stable_seconds": 1.0,
            "menu_action_seconds": 0.35,
            "race_enter_frames": 5,
        }
    )
    controller = FakeController()
    frame = _menu_frame()

    first = profile.tick(controller, _ctx(frame, now=10.0))
    second = profile.tick(controller, _ctx(frame, now=11.1))

    assert "establishing stable" in first
    assert "probe start" in second
    assert ("tap", "start", 0.08) in controller.events
    assert profile.bootstrap_actions == 1
    assert profile.phase is NfsPhase.CALIBRATION


def test_bootstrap_waits_for_visual_progress_then_advances_one_step():
    profile = NfsHotPursuit2V6Profile(
        {
            "bootstrap_stable_seconds": 0.35,
            "bootstrap_progress_delta": 0.01,
            "menu_action_seconds": 0.35,
        }
    )
    controller = FakeController()
    frame = _menu_frame(35)

    profile.tick(controller, _ctx(frame, now=20.0))
    profile.tick(controller, _ctx(frame, now=20.4))
    action = profile.tick(controller, _ctx(_menu_frame(120), now=20.6))

    assert "visual progress after start" in action
    assert profile.bootstrap_progress_acks == 1
    assert profile.bootstrap_step == 1
    taps = [event for event in controller.events if event[0] == "tap"]
    assert [event[1] for event in taps] == ["start"]


def test_bootstrap_noop_times_out_and_moves_to_next_probe_without_spam():
    profile = NfsHotPursuit2V6Profile(
        {
            "bootstrap_stable_seconds": 0.35,
            "bootstrap_progress_timeout_seconds": 0.75,
            "menu_action_seconds": 0.35,
        }
    )
    controller = FakeController()
    frame = _menu_frame()

    profile.tick(controller, _ctx(frame, now=30.0))
    profile.tick(controller, _ctx(frame, now=30.4))
    waiting = profile.tick(controller, _ctx(frame, now=30.8))
    timeout = profile.tick(controller, _ctx(frame, now=31.2))

    assert "awaiting progress after start" in waiting
    assert "no-op timeout" in timeout
    assert profile.bootstrap_timeouts == 1
    assert profile.bootstrap_step == 1
    taps = [event for event in controller.events if event[0] == "tap"]
    assert len(taps) == 1


def test_moving_unknown_is_observed_before_probe_ladder_takes_control():
    profile = NfsHotPursuit2V6Profile(
        {
            "bootstrap_stable_seconds": 0.35,
            "bootstrap_motion_guard": 0.03,
            "bootstrap_force_after_seconds": 5.0,
        }
    )
    controller = FakeController()
    frame = _menu_frame()

    action = profile.tick(controller, _ctx(frame, now=40.0, motion=0.20))
    action = profile.tick(controller, _ctx(frame, now=42.0, motion=0.20))

    assert "observe moving unknown" in action
    assert profile.bootstrap_motion_holds >= 2
    assert not any(event[0] == "tap" for event in controller.events)


def test_strong_two_frame_road_lock_takes_racing_ownership_without_template():
    profile = NfsHotPursuit2V6Profile(
        {
            "race_enter_confidence": 0.20,
            "drive_confidence": 0.20,
            "race_enter_frames": 5,
            "strong_road_confidence": 0.50,
            "strong_road_enter_frames": 2,
            "corner_brake_threshold": 1.0,
        }
    )
    controller = FakeController()
    frame = _road_frame()

    profile.tick(controller, _ctx(frame, now=50.0, motion=0.02))
    action = profile.tick(controller, _ctx(frame, now=50.08, motion=0.02))

    assert profile.phase is NfsPhase.RACING
    assert profile.bootstrap_fast_race_entries == 1
    assert "fast road takeover" in action
    assert any(event[:2] == ("hold", "cross") for event in controller.events)


def test_shared_watchdog_on_unknown_menu_kicks_start_not_racing_reverse():
    profile = NfsHotPursuit2V6Profile({})
    controller = FakeController()

    result = profile.recover(controller)

    assert result == "nfs unattended bootstrap watchdog: tap start"
    assert profile.phase is NfsPhase.CALIBRATION
    assert profile.bootstrap_watchdog_kicks == 1
    assert ("tap", "start", 0.08) in controller.events
    assert not any(event[:2] == ("hold", "square") for event in controller.events)
