from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.nfs_hot_pursuit_2_v2 import NfsScreen
from ps2_autopilot.profiles.nfs_hot_pursuit_2_v13 import NfsHotPursuit2V13Profile


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


def _ctx(frame: np.ndarray, *, now: float, motion: float = 0.03) -> ProfileContext:
    return ProfileContext(frame=frame, motion=motion, template=None, now=now)


def _replay_frame() -> np.ndarray:
    frame = np.full((360, 640, 3), 105, dtype=np.uint8)

    # HP2 replay chrome: dark timeline/status rail above a moving scene.
    frame[:65] = 12
    cv2.rectangle(frame, (15, 16), (625, 30), (120, 120, 120), -1)
    for x in (45, 150, 260, 370, 480, 590):
        cv2.rectangle(frame, (x, 10), (x + 5, 34), (200, 200, 200), -1)
    frame[65:295] = (55, 100, 55)

    # Dark replay transport rail with the four PS2 glyph colour families. The
    # detector intentionally treats the outer channels symmetrically (RGB/BGR safe).
    frame[295:350] = 10
    colours = (
        (180, 45, 40),
        (170, 40, 170),
        (40, 190, 45),
        (40, 45, 190),
    )
    for index, colour in enumerate(colours):
        x = 70 + index * 55
        cv2.rectangle(frame, (x, 310), (x + 14, 326), colour, -1)
    return frame


def _dark_unknown_frame() -> np.ndarray:
    frame = np.full((360, 640, 3), 25, dtype=np.uint8)
    cv2.rectangle(frame, (15, 16), (625, 30), (120, 120, 120), -1)
    return frame


def test_replay_chrome_claims_replay_without_template():
    profile = NfsHotPursuit2V13Profile({"broadcast_replay_variation_enabled": False})

    screen = profile._recognized_screen(_ctx(_replay_frame(), now=10.0))

    assert screen is NfsScreen.REPLAY
    assert profile.replay_visual_active is True
    assert profile.replay_visual_claims == 1
    assert profile.replay_visual_features["top_dark"] >= profile.replay_visual_top_dark_min
    assert (
        profile.replay_visual_features["controls_dark"]
        >= profile.replay_visual_controls_dark_min
    )
    assert profile.replay_visual_features["glyph_green"] >= profile.replay_visual_glyph_fraction_min


def test_visual_replay_reuses_one_shot_start_exit_not_bootstrap_confirm():
    profile = NfsHotPursuit2V13Profile(
        {
            "broadcast_replay_variation_enabled": False,
            "replay_hold_seconds": 0.20,
            "menu_action_seconds": 0.05,
        }
    )
    controller = FakeController()
    frame = _replay_frame()

    first = profile.tick(controller, _ctx(frame, now=20.0))
    second = profile.tick(controller, _ctx(frame, now=20.30))
    third = profile.tick(controller, _ctx(frame, now=20.60))

    assert first == "replay: preserve broadcast"
    assert "start" in second
    assert third == "replay: exit sent; awaiting visual progress"
    taps = [event for event in controller.events if event[0] == "tap"]
    assert [event[1] for event in taps] == ["start"]
    assert profile.replay_exit_actions == 1
    assert profile.bootstrap_actions == 0


def test_replay_visual_grace_suppresses_bootstrap_during_exit_transition():
    profile = NfsHotPursuit2V13Profile(
        {
            "broadcast_replay_variation_enabled": False,
            "replay_hold_seconds": 0.0,
            "menu_action_seconds": 0.05,
            "replay_visual_grace_seconds": 1.25,
        }
    )
    controller = FakeController()

    profile.tick(controller, _ctx(_replay_frame(), now=30.0))
    controller.events.clear()
    action = profile.tick(controller, _ctx(_dark_unknown_frame(), now=30.40, motion=0.0))

    assert action == "replay: exit sent; awaiting visual progress"
    assert profile.screen is NfsScreen.REPLAY
    assert profile.replay_visual_grace_fills >= 1
    assert not any(event[0] == "tap" for event in controller.events)
    assert profile.bootstrap_actions == 0


def test_dark_unknown_without_all_four_glyph_families_is_not_replay():
    profile = NfsHotPursuit2V13Profile({})

    screen = profile._recognized_screen(_ctx(_dark_unknown_frame(), now=40.0, motion=0.0))

    assert screen is NfsScreen.UNKNOWN
    assert profile.replay_visual_active is False
    assert profile.replay_visual_claims == 0


def test_replay_telemetry_exposes_detector_and_policy_version():
    profile = NfsHotPursuit2V13Profile({"broadcast_replay_variation_enabled": False})
    ctx = _ctx(_replay_frame(), now=50.0)
    profile._recognized_screen(ctx)

    telemetry = profile.telemetry(ctx)

    assert telemetry["nfs_policy_version"] == 13
    assert telemetry["nfs_replay_visual_enabled"] is True
    assert telemetry["nfs_replay_visual_active"] is True
    assert telemetry["nfs_replay_visual_claims"] == 1
    assert telemetry["nfs_replay_visual_features"]["glyph_magenta"] > 0.0
