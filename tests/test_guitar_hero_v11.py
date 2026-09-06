from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.guitar_hero_vision_v11 import GuitarHeroVisionV11
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.guitar_hero_types import GuitarHeroPhase, GuitarHeroScreen
from ps2_autopilot.profiles.guitar_hero_v11 import GuitarHeroV11Profile


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


def _cfg(**overrides) -> dict:
    cfg = {
        "difficulty": "easy",
        "menu_stable_seconds": 0.0,
        "failed_menu_settle_seconds": 0.0,
        "failed_confirm_retry_seconds": 0.5,
        "failure_transition_seconds": 5.0,
        "receptor_x_min": 0.20,
        "receptor_x_max": 0.85,
        "receptor_lock_threshold": 0.78,
        "receptor_lock_frames": 3,
        "receptor_candidate_limit": 4,
        "note_trigger_gap": 0.030,
        "note_receptor_exclusion": 0.024,
        "note_lane_half_width": 0.032,
        "hit_threshold": 0.50,
        "hit_reset_threshold": 0.10,
    }
    cfg.update(overrides)
    return cfg


def _highway() -> np.ndarray:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:] = (28, 24, 22)
    for x, color in zip(RECEPTOR_X, COLORS, strict=True):
        cv2.circle(frame, (x, RECEPTOR_Y), 11, color, -1)
        cv2.circle(frame, (x, RECEPTOR_Y), 5, (35, 35, 35), -1)
    return frame


def _failed_card(selected: int = 1) -> np.ndarray:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:] = (38, 22, 26)
    cv2.rectangle(frame, (120, 55), (520, 400), (22, 28, 31), -1)
    for y, x0, x1 in (
        (95, 185, 455),
        (130, 170, 470),
        (165, 225, 415),
        (205, 225, 415),
        (245, 225, 415),
    ):
        cv2.rectangle(frame, (x0, y), (x1, y + 22), (238, 238, 238), -1)
    rows = ((282, 210, 440), (326, 220, 430), (360, 220, 430))
    for index, (y, x0, x1) in enumerate(rows):
        color = (0, 235, 245) if index == selected else (238, 238, 238)
        cv2.rectangle(frame, (x0, y), (x1, y + 24), color, -1)
    cv2.rectangle(frame, (70, 430), (185, 478), (30, 145, 35), -1)
    cv2.rectangle(frame, (390, 430), (520, 478), (145, 145, 145), -1)
    return frame


def _title_like_transition() -> np.ndarray:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:] = (30, 25, 35)
    # Deliberately satisfy V4's broad image-only title topology. The old policy would
    # reset to BOOT here; V11 may instead conservatively wait or legitimately claim a
    # downstream route, but it must never treat this image-only collision as TITLE.
    cv2.rectangle(frame, (150, 80), (490, 320), (210, 210, 210), -1)
    cv2.rectangle(frame, (180, 355), (460, 390), (225, 225, 225), -1)
    return frame


def test_locked_highway_uses_fast_local_support_after_initial_geometry_lock():
    vision = GuitarHeroVisionV11(_cfg())
    for _ in range(3):
        vision.analyze(_highway())
    assert vision.layout_locked is True
    full_before = vision.full_layout_frames

    obs = vision.analyze(_highway())

    assert obs.gameplay_confidence >= 0.64
    assert vision.fast_layout_frames >= 1
    assert vision.full_layout_frames == full_before
    assert vision.lock_support >= 3


def test_gameplay_mode_can_skip_unneeded_menu_morphology():
    vision = GuitarHeroVisionV11(_cfg())
    vision.skip_menu_scores = True
    obs = vision.analyze(_highway())

    assert vision.menu_score_frames_skipped == 1
    assert obs.main_menu_score == 0.0
    assert obs.setlist_score == 0.0
    assert obs.difficulty_score == 0.0


def test_failed_new_song_transition_cannot_be_reset_to_boot_by_title_false_positive():
    profile = GuitarHeroV11Profile(_cfg(failed_song_action="new_song"))
    controller = FakeController()

    first = profile.tick(controller, _ctx(_failed_card(1), 10.0))
    assert first == "song failed: confirm new_song"
    assert profile.route_stage == "setlist"
    assert profile.phase is GuitarHeroPhase.MENU

    transition = _title_like_transition()
    assert profile._title_splash_score(transition) >= profile.title_splash_threshold
    profile.tick(controller, _ctx(transition, 10.2, motion=0.0))

    # The retained bug was specifically an image-only TITLE claim resetting the whole
    # lifecycle to BOOT. A frame with strong setlist evidence is allowed to advance to
    # difficulty, but the route must stay downstream and must never emit Start.
    assert profile.route_stage in {"setlist", "difficulty"}
    assert profile.phase is GuitarHeroPhase.MENU
    assert profile.screen is not GuitarHeroScreen.TITLE
    taps = [event[1] for event in controller.events if event[0] == "tap"]
    assert "start" not in taps


def test_v11_telemetry_exposes_route_guard_and_fast_path_counters():
    profile = GuitarHeroV11Profile(_cfg())
    controller = FakeController()
    ctx = _ctx(_highway(), 1.0, motion=0.02)
    profile.tick(controller, ctx)
    state = profile.telemetry(ctx)

    assert state["gh_policy_version"] == 11
    assert "gh_fast_layout_frames" in state
    assert "gh_full_layout_frames" in state
    assert "gh_menu_score_frames_skipped" in state
    assert "gh_title_guard_suppressions" in state
