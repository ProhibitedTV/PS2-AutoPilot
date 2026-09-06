from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.guitar_hero_vision_v10 import GuitarHeroVisionV10
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.guitar_hero_types import GuitarHeroScreen
from ps2_autopilot.profiles.guitar_hero_v10 import GuitarHeroV10Profile


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
        "receptor_x_min": 0.20,
        "receptor_x_max": 0.85,
        "receptor_lock_threshold": 0.78,
        "receptor_lock_frames": 3,
        "note_trigger_gap": 0.030,
        "note_receptor_exclusion": 0.024,
        "note_lane_half_width": 0.032,
        "hit_threshold": 0.50,
        "hit_reset_threshold": 0.10,
        "approach_history_frames": 4,
        "approach_min_delta": 0.0015,
        "approach_backtrack_tolerance": 0.006,
        "timing_lead_frames": 0.80,
        "timing_trigger_max": 0.055,
    }
    cfg.update(overrides)
    return cfg


def _highway(note_lane: int | None = None, note_y: int | None = None) -> np.ndarray:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:] = (28, 24, 22)
    for x, color in zip(RECEPTOR_X, COLORS, strict=True):
        cv2.circle(frame, (x, RECEPTOR_Y), 11, color, -1)
        cv2.circle(frame, (x, RECEPTOR_Y), 5, (35, 35, 35), -1)
    if note_lane is not None and note_y is not None:
        cv2.circle(frame, (RECEPTOR_X[note_lane], note_y), 8, COLORS[note_lane], -1)
    return frame


def _failed_card(selected: int) -> np.ndarray:
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


def test_temporal_filter_rejects_stationary_near_receptor_color():
    vision = GuitarHeroVisionV10(_cfg())
    for _ in range(3):
        vision.analyze(_highway())

    observations = [vision.analyze(_highway(note_lane=0, note_y=380)) for _ in range(3)]

    assert all(obs.hit_strengths[0] == 0.0 for obs in observations)
    assert vision.approach_confirmed[0] is False


def test_temporal_filter_fires_only_after_note_approaches_strike_line():
    vision = GuitarHeroVisionV10(_cfg())
    for _ in range(3):
        vision.analyze(_highway())

    far = vision.analyze(_highway(note_lane=0, note_y=350))
    middle = vision.analyze(_highway(note_lane=0, note_y=360))
    arriving = vision.analyze(_highway(note_lane=0, note_y=370))

    assert far.hit_strengths[0] == 0.0
    assert middle.hit_strengths[0] == 0.0
    assert arriving.hit_strengths[0] == 1.0
    assert vision.approach_confirmed[0] is True
    assert vision.note_velocities[0] > 0.0
    assert vision.dynamic_trigger_gaps[0] >= vision.note_trigger_gap


def test_failed_screen_reads_selected_row_and_moves_to_new_song_then_confirms():
    profile = GuitarHeroV10Profile(_cfg(failed_song_action="new_song"))
    controller = FakeController()

    first = profile.tick(controller, _ctx(_failed_card(0), 10.0))
    second = profile.tick(controller, _ctx(_failed_card(1), 10.2))

    assert profile.screen is GuitarHeroScreen.FAILED
    assert first == "song failed: move selection down 0->1"
    assert second == "song failed: confirm new_song"
    taps = [event[1] for event in controller.events if event[0] == "tap"]
    assert taps == ["down", "confirm"]
    assert not any(action == "start" for action in taps)


def test_failure_card_remains_owned_when_new_song_is_highlighted():
    profile = GuitarHeroV10Profile(_cfg())
    frame = _failed_card(1)

    assert profile._failed_card_score(frame) >= profile.failed_card_threshold
    assert profile._failed_selected_index(frame) == 1


def test_profile_plays_temporally_confirmed_easy_yellow_note():
    profile = GuitarHeroV10Profile(_cfg())
    controller = FakeController()
    for index in range(3):
        profile.tick(controller, _ctx(_highway(), float(index), motion=0.02))
    controller.events.clear()

    a = profile.tick(controller, _ctx(_highway(note_lane=2, note_y=350), 3.0, motion=0.02))
    b = profile.tick(controller, _ctx(_highway(note_lane=2, note_y=360), 3.1, motion=0.02))
    c = profile.tick(controller, _ctx(_highway(note_lane=2, note_y=370), 3.2, motion=0.02))

    assert a == "track note highway"
    assert b == "track note highway"
    assert c == "play yellow"
    assert ("hold", "r1") in controller.events


def test_v10_telemetry_exposes_temporal_calibration_state():
    profile = GuitarHeroV10Profile(_cfg())
    controller = FakeController()
    ctx = None
    for index in range(3):
        ctx = _ctx(_highway(), float(index), motion=0.02)
        profile.tick(controller, ctx)

    assert ctx is not None
    state = profile.telemetry(ctx)
    assert state["gh_policy_version"] == 10
    assert len(state["gh_approach_confirmed"]) == 5
    assert len(state["gh_note_velocities"]) == 5
    assert len(state["gh_dynamic_trigger_gaps"]) == 5
