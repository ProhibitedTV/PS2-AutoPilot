from __future__ import annotations

import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.nfs_hp2_hud import GameplayHudObservation
from ps2_autopilot.nfs_hp2_vision import RoadObservation
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.nfs_hot_pursuit_2 import NfsPhase
from ps2_autopilot.profiles.nfs_hot_pursuit_2_v9 import NfsHotPursuit2V9Profile


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


def _ctx(frame: np.ndarray, *, now: float, motion: float) -> ProfileContext:
    return ProfileContext(frame=frame, motion=motion, template=None, now=now)


def _hud_owned(profile: NfsHotPursuit2V9Profile) -> None:
    profile.hud = GameplayHudObservation(0.95, 0.95, 0.95, 0.95, 0.80)


def test_hud_road_memory_bridges_short_moving_dropout():
    profile = NfsHotPursuit2V9Profile(
        {
            "drive_confidence": 0.50,
            "hud_road_memory_seconds": 0.85,
            "hud_road_memory_min_motion": 0.01,
        }
    )
    _hud_owned(profile)
    profile.last_good_road = RoadObservation(0.84, 0.36, 0.11, 0.52, 0.35, 0.40)
    profile.last_good_road_at = 10.0

    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    profile._observe_road(_ctx(frame, now=10.45, motion=0.03))

    assert profile.road.confidence >= profile.drive_confidence
    assert profile.road.center_x > 0.20
    assert profile.hud_road_memory_fills == 1


def test_hud_road_memory_does_not_keep_stationary_wall_alive():
    profile = NfsHotPursuit2V9Profile(
        {
            "drive_confidence": 0.50,
            "hud_road_memory_seconds": 0.85,
            "hud_road_memory_min_motion": 0.01,
        }
    )
    _hud_owned(profile)
    profile.last_good_road = RoadObservation(0.84, 0.36, 0.11, 0.52, 0.35, 0.40)
    profile.last_good_road_at = 10.0

    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    profile._observe_road(_ctx(frame, now=10.45, motion=0.0))

    assert profile.road.confidence < profile.drive_confidence
    assert profile.hud_road_memory_fills == 0


def test_first_wall_recovery_uses_last_known_road_center():
    profile = NfsHotPursuit2V9Profile({"hud_stall_hard_restart_enabled": False})
    controller = FakeController()
    profile.last_good_road = RoadObservation(0.80, 0.45, 0.08, 0.48, 0.34, 0.40)
    profile.last_good_road_at = 20.0

    action = profile._start_recovery(
        controller,
        _ctx(np.zeros((360, 640, 3), dtype=np.uint8), now=21.0, motion=0.0),
        "road confidence lost",
    )

    assert profile.phase is NfsPhase.RECOVERY
    assert profile.recovery_direction == -1.0
    assert profile.recovery_center_guided == 1
    assert "center-guided forward=right" in action


def test_restart_success_requires_post_confirm_change():
    profile = NfsHotPursuit2V9Profile(
        {
            "hard_restart_action_seconds": 0.35,
            "hard_restart_wait_seconds": 3.0,
            "hard_restart_progress_delta": 0.02,
        }
    )
    controller = FakeController()
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    changed = np.full((360, 640, 3), 200, dtype=np.uint8)

    profile.hard_restart_stage = "restart_confirm"
    profile.hard_restart_stage_since = 1.0
    action = profile._tick_hard_restart(controller, _ctx(frame, now=1.5, motion=0.0))

    assert profile.hard_restart_stage == "wait_restart"
    assert not profile.hard_restart_progress_seen
    assert "verify post-confirm progress" in action

    action = profile._tick_hard_restart(controller, _ctx(frame, now=2.0, motion=0.0))
    assert profile.hard_restart_stage == "wait_restart"
    assert profile.hard_restart_successes == 0
    assert "awaiting post-confirm" in action

    action = profile._tick_hard_restart(controller, _ctx(changed, now=3.0, motion=0.0))
    assert profile.hard_restart_stage is None
    assert profile.hard_restart_successes == 1
    assert "visual progress" in action


def test_repeated_restart_failures_arm_quit_race_escalation():
    profile = NfsHotPursuit2V9Profile(
        {
            "hard_quit_enabled": True,
            "hard_quit_after_restart_failures": 2,
        }
    )
    frame = np.zeros((360, 640, 3), dtype=np.uint8)

    profile._finish_hard_restart(_ctx(frame, now=10.0, motion=0.0), progressed=False)
    assert not profile.hard_quit_armed

    action = profile._finish_hard_restart(
        _ctx(frame, now=11.0, motion=0.0),
        progressed=False,
    )
    assert profile.hard_quit_armed
    assert "Quit Race escalation armed" in action


def test_showmanship_horn_is_straight_only_and_never_cop_mode():
    profile = NfsHotPursuit2V9Profile(
        {
            "showmanship_horn_enabled": True,
            "showmanship_horn_interval_seconds": 15.0,
            "showmanship_horn_warmup_seconds": 3.0,
            "showmanship_horn_min_road_confidence": 0.60,
        }
    )
    controller = FakeController()
    profile.phase = NfsPhase.RACING
    profile.phase_since = 0.0
    profile.drive_mode = "racer"
    profile.road = RoadObservation(0.90, 0.02, 0.02, 0.55, 0.40, 0.45)
    profile.last_good_road = profile.road
    profile.last_good_road_at = 100.0
    profile.last_steer = 0.05

    fired = profile._maybe_showmanship_horn(
        controller,
        _ctx(np.zeros((360, 640, 3), dtype=np.uint8), now=100.0, motion=0.03),
    )
    assert fired
    assert ("tap", "circle", 0.04) in controller.events

    profile.drive_mode = "cop"
    profile.last_good_road_at = 200.0
    fired = profile._maybe_showmanship_horn(
        controller,
        _ctx(np.zeros((360, 640, 3), dtype=np.uint8), now=200.0, motion=0.03),
    )
    assert not fired
    assert profile.showmanship_horns == 1
