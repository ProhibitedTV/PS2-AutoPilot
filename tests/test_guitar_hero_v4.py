from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.guitar_hero_types import GuitarHeroPhase, GuitarHeroScreen
from ps2_autopilot.profiles.guitar_hero_v4 import GuitarHeroV4Profile


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


def _ctx(frame: np.ndarray, now: float, motion: float = 0.0) -> ProfileContext:
    return ProfileContext(frame=frame, motion=motion, template=None, now=now)


def _title_card() -> np.ndarray:
    """Synthetic topology matching GH1's retained PRESS ANY BUTTON title frame."""

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    # Large pale logo mass in the center.
    cv2.rectangle(frame, (180, 95), (475, 310), (220, 220, 220), -1)
    # Break it up with dark cuts so this behaves more like lettering than a flat card.
    for x in (220, 285, 350, 415):
        cv2.rectangle(frame, (x, 115), (x + 18, 285), (20, 20, 20), -1)
    # Separate bright "PRESS ANY BUTTON TO BEGIN"-style prompt line.
    cv2.rectangle(frame, (185, 365), (455, 384), (235, 235, 235), -1)
    # Decorative colored logo fragments: deliberately enough to tempt weak color-only
    # highway logic, but not a real five-receptor row.
    cv2.circle(frame, (330, 350), 18, (0, 0, 255), -1)
    cv2.circle(frame, (455, 375), 16, (0, 255, 255), -1)
    cv2.circle(frame, (470, 360), 14, (0, 128, 255), -1)
    return frame


def test_title_splash_preempts_false_main_menu_and_gameplay_and_presses_start():
    profile = GuitarHeroV4Profile(
        {
            "menu_stable_seconds": 0.0,
            "menu_input_settle_seconds": 0.0,
            "menu_progress_timeout_seconds": 0.0,
        }
    )
    controller = FakeController()
    frame = _title_card()
    base = profile.vision.analyze(np.zeros((480, 640, 3), dtype=np.uint8))
    ambiguous = replace(
        base,
        gameplay_confidence=0.95,
        receptor_confidence=0.66,
        main_menu_score=1.0,
        selected_main_index=0,
        title_score=0.0,
    )
    profile.vision.analyze = lambda _frame: ambiguous

    # Simulate state already corrupted by the previous live false-positive path.
    profile.phase = GuitarHeroPhase.PLAYING
    profile.route_stage = "gameplay"
    profile.screen = GuitarHeroScreen.GAMEPLAY

    action = profile.tick(controller, _ctx(frame, 10.0))

    assert profile.screen is GuitarHeroScreen.TITLE
    assert profile.phase is GuitarHeroPhase.BOOT
    assert profile.route_stage == "boot"
    assert action.endswith("start")
    assert ("tap", "start", 0.06) in controller.events
    assert not any(event[:2] == ("hold", "l2") for event in controller.events)
    assert profile._title_splash_score(frame) >= profile.title_splash_threshold


def test_weak_receptor_layout_cannot_claim_image_only_gameplay():
    profile = GuitarHeroV4Profile({})
    black = np.zeros((480, 640, 3), dtype=np.uint8)
    base = profile.vision.analyze(black)
    weak = replace(base, gameplay_confidence=0.95, receptor_confidence=0.66)

    screen = profile._classify(_ctx(black, 20.0), weak)

    assert screen is not GuitarHeroScreen.GAMEPLAY


def test_strong_receptor_layout_still_claims_gameplay():
    profile = GuitarHeroV4Profile({})
    black = np.zeros((480, 640, 3), dtype=np.uint8)
    base = profile.vision.analyze(black)
    strong = replace(base, gameplay_confidence=0.95, receptor_confidence=0.90)

    screen = profile._classify(_ctx(black, 21.0), strong)

    assert screen is GuitarHeroScreen.GAMEPLAY


def test_image_only_main_menu_requires_real_stacked_text_rows():
    profile = GuitarHeroV4Profile({})
    black = np.zeros((480, 640, 3), dtype=np.uint8)
    base = profile.vision.analyze(black)
    fake_menu = replace(
        base,
        gameplay_confidence=0.0,
        receptor_confidence=0.0,
        main_menu_score=1.0,
        selected_main_index=0,
    )
    profile.phase = GuitarHeroPhase.MENU
    profile.route_stage = "main"

    screen = profile._classify(_ctx(black, 30.0), fake_menu)

    assert screen is GuitarHeroScreen.UNKNOWN
