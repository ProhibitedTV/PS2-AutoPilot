from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.guitar_hero_types import GuitarHeroPhase, GuitarHeroScreen
from ps2_autopilot.profiles.guitar_hero_v6 import GuitarHeroV6Profile


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


def _band_name_card() -> np.ndarray:
    """Synthetic topology matching the retained live NAME YOUR BAND frame."""

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    # Broad GH1 paper card; intentionally similar enough to the old controller card
    # that V3's broad tutorial detector can also accept it.
    cv2.rectangle(frame, (65, 20), (585, 470), (125, 180, 110), -1)

    # Dense dark title/name copy in the upper portion.
    for y in (55, 82, 109, 150):
        cv2.rectangle(frame, (155, y), (500, y + 14), (25, 25, 25), -1)
    cv2.rectangle(frame, (230, 205), (400, 235), (30, 30, 30), -1)

    # Distinctive footer: green NEXT beside red DELETE.
    cv2.rectangle(frame, (20, 408), (185, 472), (30, 145, 35), -1)
    cv2.rectangle(frame, (195, 408), (355, 472), (35, 35, 190), -1)
    cv2.rectangle(frame, (55, 430), (155, 447), (230, 230, 230), -1)
    cv2.rectangle(frame, (230, 430), (325, 447), (230, 230, 230), -1)
    return frame


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


def test_band_name_preempts_false_tutorial_and_presses_start():
    profile = GuitarHeroV6Profile(
        {
            "band_name_settle_seconds": 0.0,
            "band_name_retry_seconds": 2.0,
            "controller_tutorial_settle_seconds": 0.0,
        }
    )
    controller = FakeController()
    frame = _band_name_card()

    # Prove this is the exact class of collision V6 is meant to resolve.
    assert profile._instruction_card_score(frame) >= profile.controller_tutorial_threshold
    assert profile._band_name_score(frame) >= profile.band_name_threshold

    profile._controller_tutorial_active = True
    profile._controller_tutorial_attempts = 3
    action = profile.tick(controller, _ctx(frame, 10.0))

    assert "band name" in action
    assert "Start" in action
    assert ("tap", "start", 0.08) in controller.events
    assert not any(event[:2] in {("tap", "confirm"), ("tap", "l2")} for event in controller.events)
    assert not profile._controller_tutorial_active
    assert profile.screen is GuitarHeroScreen.PRESENTATION
    assert profile.phase is GuitarHeroPhase.BOOT
    assert profile.route_stage == "boot"


def test_band_name_start_retries_are_bounded():
    profile = GuitarHeroV6Profile(
        {
            "band_name_settle_seconds": 0.0,
            "band_name_retry_seconds": 2.0,
            "band_name_max_attempts": 3,
        }
    )
    controller = FakeController()
    frame = _band_name_card()

    first = profile.tick(controller, _ctx(frame, 20.0))
    second = profile.tick(controller, _ctx(frame, 22.1))
    third = profile.tick(controller, _ctx(frame, 24.2))
    final = profile.tick(controller, _ctx(frame, 26.3))

    starts = [event for event in controller.events if event[:2] == ("tap", "start")]
    assert len(starts) == 3
    assert "1/3" in first
    assert "2/3" in second
    assert "3/3" in third
    assert "budget exhausted" in final
    assert profile.band_name_inputs == 3


def test_controller_tutorial_without_red_delete_remains_v5_confirm_path():
    profile = GuitarHeroV6Profile(
        {
            "controller_tutorial_threshold": 0.70,
            "controller_tutorial_settle_seconds": 0.0,
            "controller_tutorial_retry_seconds": 2.0,
        }
    )
    controller = FakeController()
    frame = _controller_instruction_card()

    assert profile._band_name_score(frame) < profile.band_name_tutorial_guard_threshold
    action = profile.tick(controller, _ctx(frame, 30.0))

    assert "controller tutorial" in action
    assert "confirm/Cross" in action
    assert ("tap", "confirm", 0.08) in controller.events
    assert not any(event[:2] == ("tap", "start") for event in controller.events)


def test_band_name_telemetry_reports_v6_state():
    profile = GuitarHeroV6Profile({"band_name_settle_seconds": 0.0})
    controller = FakeController()
    frame = _band_name_card()
    ctx = _ctx(frame, 40.0)

    profile.tick(controller, ctx)
    state = profile.telemetry(ctx)

    assert state["gh_policy_version"] == 6
    assert state["gh_band_name_active"] is True
    assert state["gh_band_name_attempts"] == 1
    assert state["gh_band_name_inputs"] == 1
    assert state["gh_band_name_score"] >= profile.band_name_threshold
