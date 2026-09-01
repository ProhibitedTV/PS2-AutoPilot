from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.nfs_hp2_hud import GameplayHudObservation
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.nfs_hot_pursuit_2 import NfsPhase
from ps2_autopilot.profiles.nfs_hot_pursuit_2_v8 import NfsHotPursuit2V8Profile


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


def _hud_frame(value: int = 55) -> np.ndarray:
    frame = np.full((360, 640, 3), (value, value + 15, value - 10), dtype=np.uint8)
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


def _ctx(frame: np.ndarray, *, now: float, motion: float = 0.0) -> ProfileContext:
    return ProfileContext(frame=frame, motion=motion, template=None, now=now)


def _force_hud(profile: NfsHotPursuit2V8Profile) -> None:
    profile.hud = GameplayHudObservation(1.0, 1.0, 1.0, 1.0, 1.0)


def test_hud_watchdog_recovery_participates_in_alternating_streak():
    profile = NfsHotPursuit2V8Profile({"hud_gameplay_threshold": 0.82})
    controller = FakeController()
    _force_hud(profile)

    first = profile.recover(controller)
    first_direction = profile.recovery_direction
    profile.phase = NfsPhase.RACING
    second = profile.recover(controller)
    second_direction = profile.recovery_direction

    assert "HUD-owned watchdog recovery armed" in first
    assert "HUD-owned watchdog recovery armed" in second
    assert first_direction == -second_direction
    assert profile.recovery_streak == 2
    assert profile.hud_watchdog_recovery_arms == 2
    assert not any(event[:2] == ("tap", "start") for event in controller.events)


def test_recovery_completion_keeps_hud_owned_gameplay_in_racing():
    profile = NfsHotPursuit2V8Profile({"hud_gameplay_threshold": 0.82})
    controller = FakeController()
    _force_hud(profile)
    profile.phase = NfsPhase.RECOVERY
    profile.recovery_reason = "wall trap"
    profile.recovery_started_at = 0.0

    action = profile._tick_recovery(controller, _ctx(_hud_frame(), now=10.0, motion=0.0))

    assert profile.phase is NfsPhase.RACING
    assert "HUD still owns gameplay" in action
    assert "unattended bootstrap" not in action


def test_sustained_hud_stall_escalates_to_restart_race_last_resort():
    profile = NfsHotPursuit2V8Profile(
        {
            "hud_gameplay_threshold": 0.82,
            "hud_stall_hard_restart_recoveries": 2,
            "hud_stall_hard_restart_seconds": 12.0,
        }
    )
    controller = FakeController()
    _force_hud(profile)
    profile.hud_stall_since = 1.0
    profile.hud_stall_recovery_attempts = 2

    action = profile._start_recovery(
        controller,
        _ctx(_hud_frame(), now=20.0, motion=0.0),
        "road confidence lost",
    )

    assert "hard-stall restart: tap start" in action
    assert profile.hard_restart_stage == "pause"
    assert profile.hard_restart_attempts == 1
    assert ("tap", "start", 0.08) in controller.events


def test_hard_restart_uses_pause_restart_menu_sequence_once():
    profile = NfsHotPursuit2V8Profile(
        {
            "hud_gameplay_threshold": 0.82,
            "hard_restart_action_seconds": 0.35,
            "hard_restart_wait_seconds": 3.0,
        }
    )
    controller = FakeController()
    _force_hud(profile)
    frame = _hud_frame()

    profile._begin_hard_restart(controller, 30.0, frame=frame, source="test")
    profile._tick_hard_restart(controller, _ctx(frame, now=30.4))
    profile._tick_hard_restart(controller, _ctx(frame, now=30.8))
    profile._tick_hard_restart(controller, _ctx(frame, now=31.2))

    taps = [event[1] for event in controller.events if event[0] == "tap"]
    assert taps == ["start", "down", "confirm", "confirm"]
    assert profile.hard_restart_stage == "wait_restart"
    assert profile.hard_restart_inputs == 4


def test_hard_restart_hands_back_after_visual_progress():
    profile = NfsHotPursuit2V8Profile(
        {
            "hud_gameplay_threshold": 0.82,
            "hard_restart_action_seconds": 0.35,
            "hard_restart_progress_delta": 0.01,
        }
    )
    controller = FakeController()
    frame = _hud_frame(55)
    changed = np.full_like(frame, 180)
    _force_hud(profile)

    profile._begin_hard_restart(controller, 40.0, frame=frame, source="test")
    profile._tick_hard_restart(controller, _ctx(changed, now=40.4))
    profile._tick_hard_restart(controller, _ctx(changed, now=40.8))
    profile._tick_hard_restart(controller, _ctx(changed, now=41.2))
    action = profile._tick_hard_restart(controller, _ctx(changed, now=42.5))

    assert "visual progress" in action
    assert profile.hard_restart_stage is None
    assert profile.hard_restart_successes == 1
    assert profile.phase is NfsPhase.CALIBRATION
