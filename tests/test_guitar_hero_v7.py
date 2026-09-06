from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.guitar_hero_types import GuitarHeroPhase, GuitarHeroScreen
from ps2_autopilot.profiles.guitar_hero_v7 import GuitarHeroV7Profile


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


def _difficulty_poster() -> np.ndarray:
    """Synthetic topology matching the retained live DIFFICULTY poster."""

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:] = (25, 20, 35)

    # Tall orange/brown band poster in the center.
    cv2.rectangle(frame, (160, 15), (480, 420), (40, 110, 180), -1)

    # Dense black band silhouettes across the poster.
    cv2.rectangle(frame, (245, 40), (395, 315), (20, 20, 20), -1)
    cv2.circle(frame, (220, 90), 55, (20, 20, 20), -1)
    cv2.circle(frame, (420, 110), 45, (20, 20, 20), -1)

    # Bright DIFFICULTY + four option rows in the lower poster.
    for y, x0, x1 in (
        (285, 190, 360),
        (325, 220, 300),
        (350, 300, 400),
        (375, 350, 445),
        (400, 390, 470),
    ):
        cv2.rectangle(frame, (x0, y), (x1, y + 16), (235, 235, 235), -1)

    # Green CONTINUE and adjacent red BACK footer badges.
    cv2.rectangle(frame, (70, 425), (180, 478), (30, 145, 35), -1)
    cv2.rectangle(frame, (190, 425), (300, 478), (35, 35, 190), -1)
    cv2.rectangle(frame, (95, 444), (160, 457), (235, 235, 235), -1)
    cv2.rectangle(frame, (215, 444), (280, 457), (235, 235, 235), -1)
    return frame


def _band_name_card() -> np.ndarray:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.rectangle(frame, (65, 20), (585, 470), (125, 180, 110), -1)
    for y in (55, 82, 109, 150):
        cv2.rectangle(frame, (155, y), (500, y + 14), (25, 25, 25), -1)
    cv2.rectangle(frame, (230, 205), (400, 235), (30, 30, 30), -1)
    cv2.rectangle(frame, (20, 408), (185, 472), (30, 145, 35), -1)
    cv2.rectangle(frame, (195, 408), (355, 472), (35, 35, 190), -1)
    return frame


def test_live_difficulty_topology_reacquires_from_presentation_and_confirms_easy():
    profile = GuitarHeroV7Profile(
        {
            "difficulty": "easy",
            "menu_stable_seconds": 0.0,
            "menu_input_settle_seconds": 0.0,
        }
    )
    controller = FakeController()
    frame = _difficulty_poster()

    # Reproduce the live failure state: the setlist transition was classified as
    # presentation before the static difficulty poster arrived.
    profile.phase = GuitarHeroPhase.PRESENTATION
    profile.route_stage = "difficulty"

    assert profile._difficulty_card_score(frame) >= profile.difficulty_card_threshold
    action = profile.tick(controller, _ctx(frame, 10.0))

    assert action == "menu difficulty_launch: confirm"
    assert ("tap", "confirm", 0.06) in controller.events
    assert profile.screen is GuitarHeroScreen.DIFFICULTY
    assert profile.phase is GuitarHeroPhase.AWAIT_GAMEPLAY
    assert profile.route_stage == "song"
    assert profile._first_difficulty_selection is False


def test_difficulty_poster_can_recover_even_if_route_state_was_lost():
    profile = GuitarHeroV7Profile(
        {
            "difficulty": "easy",
            "menu_stable_seconds": 0.0,
            "menu_input_settle_seconds": 0.0,
        }
    )
    controller = FakeController()
    frame = _difficulty_poster()

    profile.phase = GuitarHeroPhase.BOOT
    profile.route_stage = "boot"
    action = profile.tick(controller, _ctx(frame, 20.0))

    assert action == "menu difficulty_launch: confirm"
    assert profile.screen is GuitarHeroScreen.DIFFICULTY
    assert profile.route_stage == "song"
    assert not any(event[:2] == ("tap", "start") for event in controller.events)


def test_band_name_card_does_not_collide_with_difficulty_detector():
    profile = GuitarHeroV7Profile({})
    frame = _band_name_card()

    assert profile._band_name_score(frame) >= profile.band_name_threshold
    assert profile._difficulty_card_score(frame) < profile.difficulty_card_threshold


def test_difficulty_telemetry_reports_v7_state():
    profile = GuitarHeroV7Profile(
        {
            "menu_stable_seconds": 0.0,
            "menu_input_settle_seconds": 0.0,
        }
    )
    controller = FakeController()
    frame = _difficulty_poster()
    ctx = _ctx(frame, 30.0)

    profile.tick(controller, ctx)
    state = profile.telemetry(ctx)

    assert state["gh_policy_version"] == 7
    assert state["gh_difficulty_card_active"] is True
    assert state["gh_difficulty_card_episodes"] == 1
    assert state["gh_difficulty_card_score"] >= profile.difficulty_card_threshold
