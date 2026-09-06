from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.guitar_hero_vision_v8 import GuitarHeroVisionV8
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.guitar_hero_v8 import GuitarHeroV8Profile
from ps2_autopilot.controllers.base import Controller


COLORS = (
    (0, 255, 0),      # green
    (0, 0, 255),      # red
    (0, 255, 255),    # yellow
    (255, 0, 0),      # blue
    (0, 128, 255),    # orange
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


def _highway(note_lane: int | None = None, note_y: int | None = None) -> np.ndarray:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:] = (28, 24, 22)

    # True five-lane receptor row.
    for x, color in zip(RECEPTOR_X, COLORS, strict=True):
        cv2.circle(frame, (x, RECEPTOR_Y), 11, color, -1)
        cv2.circle(frame, (x, RECEPTOR_Y), 5, (35, 35, 35), -1)

    # Concert-stage color clutter that is individually plausible but does not form a
    # common ordered/equally-spaced receptor row.
    decoys = ((500, 330), (180, 350), (455, 300), (235, 345), (520, 365))
    for (x, y), color in zip(decoys, COLORS, strict=True):
        cv2.circle(frame, (x, y), 13, color, -1)

    if note_lane is not None and note_y is not None:
        cv2.circle(frame, (RECEPTOR_X[note_lane], note_y), 8, COLORS[note_lane], -1)
    return frame


def _stage_clutter_only() -> np.ndarray:
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:] = (28, 24, 22)
    points = ((500, 330), (180, 410), (455, 300), (235, 365), (520, 425))
    for (x, y), color in zip(points, COLORS, strict=True):
        cv2.circle(frame, (x, y), 14, color, -1)
    return frame


def _cfg() -> dict:
    return {
        "vision_width": 640,
        "vision_height": 480,
        "receptor_x_min": 0.20,
        "receptor_x_max": 0.85,
        "receptor_lock_threshold": 0.78,
        "receptor_lock_frames": 3,
        "note_trigger_gap": 0.030,
        "note_receptor_exclusion": 0.024,
        "note_lane_half_width": 0.032,
        "hit_threshold": 0.50,
        "hit_reset_threshold": 0.10,
        "gameplay_receptor_floor": 0.78,
        "menu_stable_seconds": 0.0,
    }


def test_joint_layout_locks_true_receptors_despite_stage_color_clutter():
    vision = GuitarHeroVisionV8(_cfg())
    obs = None
    for _ in range(3):
        obs = vision.analyze(_highway())

    assert obs is not None
    assert vision.layout_locked is True
    assert vision.lock_support == 5
    assert obs.receptor_confidence >= 0.80
    assert obs.gameplay_confidence >= 0.75
    xs = [center[0] for center in obs.receptor_centers if center is not None]
    assert len(xs) == 5
    assert all(xs[index] < xs[index + 1] for index in range(4))


def test_note_does_not_fire_early_then_fires_at_receptor_arrival_zone():
    vision = GuitarHeroVisionV8(_cfg())
    for _ in range(3):
        vision.analyze(_highway())

    far = vision.analyze(_highway(note_lane=0, note_y=340))
    near = vision.analyze(_highway(note_lane=0, note_y=380))

    assert far.hit_strengths[0] == 0.0
    assert near.hit_strengths[0] == 1.0
    assert all(value == 0.0 for value in near.hit_strengths[1:])
    assert vision.note_gaps[0] is not None
    assert vision.note_gaps[0] <= vision.note_trigger_gap


def test_remembered_layout_does_not_turn_stage_clutter_into_gameplay():
    vision = GuitarHeroVisionV8(_cfg())
    for _ in range(3):
        vision.analyze(_highway())
    assert vision.layout_locked is True

    obs = vision.analyze(_stage_clutter_only())

    assert vision.lock_support < 3
    assert obs.receptor_confidence < 0.78
    assert obs.gameplay_confidence < 0.64
    assert all(value == 0.0 for value in obs.hit_strengths)


def test_v8_profile_uses_hardened_vision_and_emits_only_arriving_lane():
    profile = GuitarHeroV8Profile(_cfg())
    controller = FakeController()

    for index in range(3):
        profile.tick(
            controller,
            ProfileContext(frame=_highway(), motion=0.02, template=None, now=float(index)),
        )
    controller.events.clear()

    action = profile.tick(
        controller,
        ProfileContext(
            frame=_highway(note_lane=2, note_y=380),
            motion=0.02,
            template=None,
            now=4.0,
        ),
    )

    assert action == "play yellow"
    assert ("hold", "r1") in controller.events
    assert not any(event[:2] in {("hold", "l2"), ("hold", "l1"), ("hold", "r2"), ("hold", "cross")} for event in controller.events)


def test_v8_telemetry_exposes_layout_lock_and_note_gap():
    profile = GuitarHeroV8Profile(_cfg())
    controller = FakeController()
    ctx = None
    for index in range(3):
        ctx = ProfileContext(frame=_highway(), motion=0.02, template=None, now=float(index))
        profile.tick(controller, ctx)

    assert ctx is not None
    state = profile.telemetry(ctx)
    assert state["gh_policy_version"] == 8
    assert state["gh_highway_layout_locked"] is True
    assert state["gh_highway_lock_support"] == 5
    assert len(state["gh_note_gaps"]) == 5
