from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.guitar_hero_vision import GuitarHeroVision
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.guitar_hero_v2 import (
    GuitarHeroPhase,
    GuitarHeroScreen,
    GuitarHeroV2Profile,
)
from ps2_autopilot.vision import TemplateMatch


LANE_COLORS = (
    (0, 255, 0),
    (0, 0, 255),
    (0, 255, 255),
    (255, 0, 0),
    (0, 128, 255),
)
LANE_X = (360, 410, 460, 510, 560)
RECEPTOR_Y = 410


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


def _highway(*, notes: tuple[int, ...] = (), sustain: tuple[int, ...] = ()) -> np.ndarray:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    for x, color in zip(LANE_X, LANE_COLORS):
        cv2.circle(frame, (x, RECEPTOR_Y), 16, color, 5)
    for index in notes:
        cv2.circle(frame, (LANE_X[index], 382), 11, LANE_COLORS[index], -1)
    for index in sustain:
        cv2.rectangle(
            frame,
            (LANE_X[index] - 3, 300),
            (LANE_X[index] + 3, 382),
            LANE_COLORS[index],
            -1,
        )
    return frame


def _ctx(
    frame: np.ndarray,
    *,
    now: float,
    motion: float = 0.0,
    template: str | None = None,
    score: float = 0.95,
) -> ProfileContext:
    match = None if template is None else TemplateMatch(template, score)
    return ProfileContext(frame=frame, motion=motion, template=match, now=now)


def test_highway_vision_uses_receptors_without_firing_idle_lanes():
    vision = GuitarHeroVision({})
    idle = vision.analyze(_highway())
    note = vision.analyze(_highway(notes=(0, 2)))

    assert idle.gameplay_confidence >= 0.70
    assert max(idle.hit_strengths) < 0.03
    assert note.hit_strengths[0] > 0.40
    assert note.hit_strengths[2] > 0.40
    assert note.hit_strengths[1] < 0.03
    assert note.hit_strengths[3] < 0.03
    assert note.hit_strengths[4] < 0.03


def test_profile_hits_dualshock_chord_without_blocking_taps():
    profile = GuitarHeroV2Profile(
        {
            "gameplay_threshold": 0.60,
            "hit_threshold": 0.06,
            "whammy_enabled": False,
        }
    )
    controller = FakeController()

    action = profile.tick(controller, _ctx(_highway(notes=(0, 2)), now=10.0, motion=0.01))

    assert action == "play green+yellow"
    assert ("hold", "l2") in controller.events
    assert ("hold", "r1") in controller.events
    assert not any(event[0] == "tap" for event in controller.events)
    assert profile.notes_attempted == 2
    assert profile.chords_attempted == 1


def test_sustain_keeps_note_held_and_whammies_only_in_gameplay():
    profile = GuitarHeroV2Profile(
        {"gameplay_threshold": 0.60, "hit_threshold": 0.06, "whammy_enabled": True}
    )
    controller = FakeController()

    action = profile.tick(
        controller,
        _ctx(_highway(notes=(0,), sustain=(0,)), now=12.0, motion=0.01),
    )

    assert "green" in action
    assert ("hold", "l2") in controller.events
    assert any(event[0] == "left_stick" and abs(event[2]) > 0.5 for event in controller.events)


def test_moving_unknown_screen_is_presentation_and_receives_no_input():
    profile = GuitarHeroV2Profile({})
    controller = FakeController()
    black = np.zeros((480, 640, 3), dtype=np.uint8)

    action = profile.tick(controller, _ctx(black, now=20.0, motion=0.08))

    assert profile.screen is GuitarHeroScreen.PRESENTATION
    assert profile.phase is GuitarHeroPhase.PRESENTATION
    assert "wait for cutscene" in action
    assert controller.events == []


def test_known_loading_screen_suppresses_watchdog_recovery_input():
    profile = GuitarHeroV2Profile({})
    controller = FakeController()
    black = np.zeros((480, 640, 3), dtype=np.uint8)

    profile.tick(controller, _ctx(black, now=30.0, template="gh_loading"))
    controller.events.clear()
    result = profile.recover(controller)

    assert "suppressed" in result
    assert not any(event[0] == "tap" for event in controller.events)


def test_save_prompt_and_main_menu_have_bounded_transactional_inputs():
    profile = GuitarHeroV2Profile(
        {
            "menu_stable_seconds": 0.0,
            "menu_input_settle_seconds": 0.0,
            "menu_progress_timeout_seconds": 0.0,
        }
    )
    controller = FakeController()
    black = np.zeros((480, 640, 3), dtype=np.uint8)

    action = profile.tick(
        controller,
        _ctx(black, now=40.0, template="gh_save_prompt_yes_selected"),
    )
    assert action.endswith("confirm")
    assert ("tap", "confirm", 0.06) in controller.events

    controller.events.clear()
    action = profile.tick(
        controller,
        _ctx(black, now=41.0, template="gh_main_menu_career_selected"),
    )
    assert action.endswith("down")
    action = profile.tick(
        controller,
        _ctx(black, now=42.0, template="gh_main_menu_career_selected"),
    )
    assert action.endswith("confirm")
    taps = [event[1] for event in controller.events if event[0] == "tap"]
    assert taps == ["down", "confirm"]


def test_difficulty_menu_uses_selected_row_evidence():
    profile = GuitarHeroV2Profile(
        {
            "difficulty": "hard",
            "menu_stable_seconds": 0.0,
            "menu_input_settle_seconds": 0.0,
            "menu_progress_timeout_seconds": 0.0,
        }
    )
    controller = FakeController()
    black = np.zeros((480, 640, 3), dtype=np.uint8)

    # Template owns the screen; inject row evidence to prove the planner walks only
    # the required distance rather than blindly normalizing/wrapping the list.
    original = profile.vision.analyze
    observation = original(black)
    profile.vision.analyze = lambda frame: observation.__class__(
        gameplay_confidence=observation.gameplay_confidence,
        receptor_confidence=observation.receptor_confidence,
        receptor_centers=observation.receptor_centers,
        hit_strengths=observation.hit_strengths,
        sustains=observation.sustains,
        save_prompt_score=observation.save_prompt_score,
        main_menu_score=observation.main_menu_score,
        setlist_score=observation.setlist_score,
        difficulty_score=1.0,
        title_score=observation.title_score,
        selected_main_index=observation.selected_main_index,
        selected_difficulty_index=1,
        frame_signature=observation.frame_signature,
    )

    for now in (50.0, 51.0):
        profile.tick(controller, _ctx(black, now=now, template="gh_difficulty_medium_selected"))

    taps = [event[1] for event in controller.events if event[0] == "tap"]
    assert taps == ["down", "confirm"]
    assert profile.phase is GuitarHeroPhase.AWAIT_GAMEPLAY
