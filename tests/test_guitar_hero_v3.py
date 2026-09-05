from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.guitar_hero_v3 import GuitarHeroV3Profile


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
    """Synthetic topology matching the retained live first-run GH1 card."""

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    # Green paper body and pale headline banner.
    cv2.rectangle(frame, (80, 60), (560, 440), (125, 180, 110), -1)
    cv2.rectangle(frame, (90, 40), (550, 170), (220, 235, 220), -1)
    # Dense dark headline/text strokes.
    for y in (70, 95, 120):
        cv2.rectangle(frame, (130, y), (500, y + 12), (20, 20, 20), -1)
    # Bottom-left green CONTINUE-style badge.
    cv2.rectangle(frame, (20, 410), (210, 470), (30, 145, 35), -1)
    cv2.rectangle(frame, (55, 430), (180, 445), (225, 225, 225), -1)
    # Decorative colored fret markers that would otherwise tempt highway vision.
    colors = ((0, 255, 0), (0, 0, 255), (0, 255, 255), (255, 0, 0), (0, 128, 255))
    for index, color in enumerate(colors):
        cv2.circle(frame, (355 + index * 42, 385), 12, color, 4)
    return frame


def test_instruction_card_preempts_false_gameplay_and_uses_green_l2():
    profile = GuitarHeroV3Profile(
        {
            "controller_tutorial_threshold": 0.70,
            "controller_tutorial_settle_seconds": 0.30,
            "controller_tutorial_retry_seconds": 2.0,
        }
    )
    controller = FakeController()
    frame = _controller_instruction_card()

    first = profile.tick(controller, _ctx(frame, 10.0))
    second = profile.tick(controller, _ctx(frame, 10.35))
    third = profile.tick(controller, _ctx(frame, 10.50))

    assert "controller tutorial" in first
    assert "green/L2" in second
    assert "track note highway" not in second
    assert [event for event in controller.events if event[:2] == ("tap", "l2")] == [
        ("tap", "l2", 0.06)
    ]
    assert not any(event[0] == "hold" for event in controller.events)
    assert "controller tutorial" in third
    assert profile.controller_tutorial_inputs == 1


def test_instruction_card_retries_l2_only_after_bounded_timeout():
    profile = GuitarHeroV3Profile(
        {
            "controller_tutorial_threshold": 0.70,
            "controller_tutorial_settle_seconds": 0.0,
            "controller_tutorial_retry_seconds": 2.0,
            "controller_tutorial_max_attempts": 2,
        }
    )
    controller = FakeController()
    frame = _controller_instruction_card()

    profile.tick(controller, _ctx(frame, 20.0))
    profile.tick(controller, _ctx(frame, 20.5))
    profile.tick(controller, _ctx(frame, 22.1))
    profile.tick(controller, _ctx(frame, 25.0))

    taps = [event for event in controller.events if event[:2] == ("tap", "l2")]
    assert len(taps) == 2
    assert profile.controller_tutorial_inputs == 2


def test_ordinary_dark_frame_does_not_claim_controller_tutorial():
    profile = GuitarHeroV3Profile({"boot_prompt_seconds": 99.0})
    controller = FakeController()
    frame = np.zeros((480, 640, 3), dtype=np.uint8)

    action = profile.tick(controller, _ctx(frame, 30.0))

    assert profile._instruction_card_score(frame) == 0.0
    assert not profile._controller_tutorial_active
    assert not any(event[:2] == ("tap", "l2") for event in controller.events)
    assert "controller tutorial" not in action
