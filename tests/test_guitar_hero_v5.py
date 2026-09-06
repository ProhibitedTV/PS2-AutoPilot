from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.guitar_hero_v5 import GuitarHeroV5Profile


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


def _ctx(frame: np.ndarray, now: float) -> ProfileContext:
    return ProfileContext(frame=frame, motion=0.0, template=None, now=now)


def _controller_instruction_card() -> np.ndarray:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.rectangle(frame, (80, 60), (560, 440), (125, 180, 110), -1)
    cv2.rectangle(frame, (90, 40), (550, 170), (220, 235, 220), -1)
    for y in (70, 95, 120):
        cv2.rectangle(frame, (130, y), (500, y + 12), (20, 20, 20), -1)
    cv2.rectangle(frame, (20, 410), (210, 470), (30, 145, 35), -1)
    cv2.rectangle(frame, (55, 430), (180, 445), (225, 225, 225), -1)
    colors = ((0, 255, 0), (0, 0, 255), (0, 255, 255), (255, 0, 0), (0, 128, 255))
    for index, color in enumerate(colors):
        cv2.circle(frame, (355 + index * 42, 385), 12, color, 4)
    return frame


def test_tutorial_uses_cross_confirm_before_l2_fallback():
    profile = GuitarHeroV5Profile(
        {
            "controller_tutorial_threshold": 0.70,
            "controller_tutorial_settle_seconds": 0.0,
            "controller_tutorial_retry_seconds": 2.0,
        }
    )
    controller = FakeController()
    frame = _controller_instruction_card()

    first = profile.tick(controller, _ctx(frame, 10.0))
    second = profile.tick(controller, _ctx(frame, 12.1))
    third = profile.tick(controller, _ctx(frame, 14.2))

    taps = [event for event in controller.events if event[0] == "tap"]
    assert [event[1] for event in taps] == ["confirm", "l2", "confirm"]
    assert taps[0] == ("tap", "confirm", 0.08)
    assert "confirm/Cross" in first
    assert "green/L2 fallback" in second
    assert "confirm/Cross" in third
    assert profile.controller_tutorial_inputs == 3


def test_tutorial_custom_action_sequence_is_validated_and_bounded():
    profile = GuitarHeroV5Profile(
        {
            "controller_tutorial_continue_actions": ["cross", "l2"],
            "controller_tutorial_max_attempts": 9,
            "controller_tutorial_threshold": 0.70,
            "controller_tutorial_settle_seconds": 0.0,
            "controller_tutorial_retry_seconds": 1.0,
        }
    )
    controller = FakeController()
    frame = _controller_instruction_card()

    profile.tick(controller, _ctx(frame, 20.0))
    profile.tick(controller, _ctx(frame, 21.1))
    final = profile.tick(controller, _ctx(frame, 22.2))

    taps = [event[1] for event in controller.events if event[0] == "tap"]
    assert taps == ["cross", "l2"]
    assert profile.controller_tutorial_max_attempts == 2
    assert "budget exhausted" in final


def test_tutorial_telemetry_reports_actual_last_action():
    profile = GuitarHeroV5Profile(
        {
            "controller_tutorial_threshold": 0.70,
            "controller_tutorial_settle_seconds": 0.0,
        }
    )
    controller = FakeController()
    frame = _controller_instruction_card()
    ctx = _ctx(frame, 30.0)

    profile.tick(controller, ctx)
    state = profile.telemetry(ctx)

    assert state["gh_policy_version"] == 5
    assert state["gh_controller_tutorial_last_action"] == "confirm"
    assert state["gh_controller_tutorial_continue_actions"][0] == "confirm"
