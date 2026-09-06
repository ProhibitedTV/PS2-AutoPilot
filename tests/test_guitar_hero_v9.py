from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.guitar_hero_types import GuitarHeroPhase, GuitarHeroScreen
from ps2_autopilot.profiles.guitar_hero_v9 import GuitarHeroV9Profile


COLORS = (
    (0, 255, 0),
    (0, 0, 255),
    (0, 255, 255),
    (255, 0, 0),
    (0, 128, 255),
)
RECEPTOR_X = (200, 260, 320, 380, 440)
RECEPTOR_Y = 400


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


def _failed_card() -> np.ndarray:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:] = (38, 22, 26)
    cv2.rectangle(frame, (120, 55), (520, 400), (22, 28, 31), -1)

    # Dense white failure/song/difficulty/menu copy.
    for y, x0, x1 in (
        (95, 185, 455),
        (130, 170, 470),
        (165, 225, 415),
        (205, 225, 415),
        (245, 225, 415),
        (325, 220, 430),
        (365, 220, 430),
    ):
        cv2.rectangle(frame, (x0, y), (x1, y + 22), (238, 238, 238), -1)

    # Yellow RETRY selection.
    cv2.rectangle(frame, (210, 270), (440, 315), (0, 235, 245), -1)

    # Green CONTINUE footer and neutral gray UP/DOWN footer; critically no red BACK.
    cv2.rectangle(frame, (70, 430), (185, 478), (30, 145, 35), -1)
    cv2.rectangle(frame, (390, 430), (520, 478), (145, 145, 145), -1)
    return frame


def _difficulty_poster() -> np.ndarray:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:] = (25, 20, 35)
    cv2.rectangle(frame, (160, 15), (480, 420), (40, 110, 180), -1)
    cv2.rectangle(frame, (245, 40), (395, 315), (20, 20, 20), -1)
    cv2.circle(frame, (220, 90), 55, (20, 20, 20), -1)
    cv2.circle(frame, (420, 110), 45, (20, 20, 20), -1)
    for y, x0, x1 in (
        (285, 190, 360),
        (325, 220, 300),
        (350, 300, 400),
        (375, 350, 445),
        (400, 390, 470),
    ):
        cv2.rectangle(frame, (x0, y), (x1, y + 16), (235, 235, 235), -1)
    cv2.rectangle(frame, (70, 425), (180, 478), (30, 145, 35), -1)
    cv2.rectangle(frame, (190, 425), (300, 478), (35, 35, 190), -1)
    return frame


def _highway(note_lane: int | None = None, note_y: int | None = None) -> np.ndarray:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:] = (28, 24, 22)
    for x, color in zip(RECEPTOR_X, COLORS, strict=True):
        cv2.circle(frame, (x, RECEPTOR_Y), 11, color, -1)
        cv2.circle(frame, (x, RECEPTOR_Y), 5, (35, 35, 35), -1)
    if note_lane is not None and note_y is not None:
        cv2.circle(frame, (RECEPTOR_X[note_lane], note_y), 8, COLORS[note_lane], -1)
    return frame


def _cfg(**overrides) -> dict:
    cfg = {
        "difficulty": "easy",
        "menu_stable_seconds": 0.0,
        "menu_input_settle_seconds": 0.0,
        "menu_progress_timeout_seconds": 0.0,
        "receptor_x_min": 0.20,
        "receptor_x_max": 0.85,
        "receptor_lock_threshold": 0.78,
        "receptor_lock_frames": 3,
        "note_trigger_gap": 0.030,
        "note_receptor_exclusion": 0.024,
        "note_lane_half_width": 0.032,
        "hit_threshold": 0.50,
        "hit_reset_threshold": 0.10,
    }
    cfg.update(overrides)
    return cfg


def test_live_failed_topology_preempts_title_and_selects_new_song():
    profile = GuitarHeroV9Profile(_cfg())
    controller = FakeController()
    frame = _failed_card()

    assert profile._failed_card_score(frame) >= profile.failed_card_threshold
    first = profile.tick(controller, _ctx(frame, 10.0))
    second = profile.tick(controller, _ctx(frame, 10.1))

    assert first == "menu failed_new_song: down"
    assert second == "menu failed_new_song: confirm"
    assert profile.screen is GuitarHeroScreen.FAILED
    assert profile.route_stage == "setlist"
    assert profile.phase is GuitarHeroPhase.MENU
    assert not any(event[:2] == ("tap", "start") for event in controller.events)


def test_failed_card_does_not_collide_with_difficulty_poster_red_back_footer():
    profile = GuitarHeroV9Profile(_cfg())
    frame = _difficulty_poster()

    assert profile._difficulty_card_score(frame) >= profile.difficulty_card_threshold
    assert profile._failed_card_score(frame) < profile.failed_card_threshold


def test_difficulty_poster_homes_to_easy_before_confirming():
    profile = GuitarHeroV9Profile(_cfg(difficulty="easy"))
    controller = FakeController()
    frame = _difficulty_poster()

    actions = []
    for index in range(4):
        actions.append(profile.tick(controller, _ctx(frame, 20.0 + index * 0.1)))

    assert actions == [
        "menu difficulty_home_to_easy: up",
        "menu difficulty_home_to_easy: up",
        "menu difficulty_home_to_easy: up",
        "menu difficulty_home_to_easy: confirm",
    ]
    taps = [event[1] for event in controller.events if event[0] == "tap"]
    assert taps == ["up", "up", "up", "confirm"]
    assert profile.phase is GuitarHeroPhase.AWAIT_GAMEPLAY
    assert profile.route_stage == "song"


def test_difficulty_homing_applies_configured_medium_after_top_clamp():
    profile = GuitarHeroV9Profile(_cfg(difficulty="medium"))
    controller = FakeController()
    frame = _difficulty_poster()

    for index in range(5):
        profile.tick(controller, _ctx(frame, 30.0 + index * 0.1))

    taps = [event[1] for event in controller.events if event[0] == "tap"]
    assert taps == ["up", "up", "up", "down", "confirm"]


def test_easy_masks_blue_and_orange_note_inputs_but_keeps_first_three():
    profile = GuitarHeroV9Profile(_cfg(difficulty="easy"))
    controller = FakeController()

    for index in range(3):
        profile.tick(controller, _ctx(_highway(), float(index), motion=0.02))
    controller.events.clear()

    blue_action = profile.tick(controller, _ctx(_highway(note_lane=3, note_y=380), 4.0, motion=0.02))
    assert blue_action == "track note highway"
    assert not any(event[:2] == ("hold", "r2") for event in controller.events)

    # Rearm/next frame: yellow is valid on Easy and should still play normally.
    controller.events.clear()
    profile._armed[2] = True
    yellow_action = profile.tick(
        controller,
        _ctx(_highway(note_lane=2, note_y=380), 5.0, motion=0.02),
    )
    assert yellow_action == "play yellow"
    assert ("hold", "r1") in controller.events


def test_v9_telemetry_exposes_failure_difficulty_and_active_lanes():
    profile = GuitarHeroV9Profile(_cfg())
    controller = FakeController()
    ctx = _ctx(_failed_card(), 40.0)
    profile.tick(controller, ctx)
    state = profile.telemetry(ctx)

    assert state["gh_policy_version"] == 9
    assert state["gh_failed_card_active"] is True
    assert state["gh_failed_card_score"] >= profile.failed_card_threshold
    assert state["gh_failed_song_action"] == "new_song"
    assert state["gh_active_lane_count"] == 3
