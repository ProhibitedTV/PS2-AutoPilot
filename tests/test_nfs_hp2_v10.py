from __future__ import annotations

import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.nfs_hp2_hud import GameplayHudObservation
from ps2_autopilot.nfs_hp2_vision import RoadObservation
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.nfs_hot_pursuit_2 import NfsPhase
from ps2_autopilot.profiles.nfs_hot_pursuit_2_v10 import NfsHotPursuit2V10Profile


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


def _ctx(*, now: float, motion: float = 0.0) -> ProfileContext:
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    return ProfileContext(frame=frame, motion=motion, template=None, now=now)


def _hud_owned(profile: NfsHotPursuit2V10Profile) -> None:
    profile.hud = GameplayHudObservation(0.95, 0.95, 0.95, 0.95, 0.80)


def test_recovery_storm_reaches_hard_restart_despite_motion_resets():
    profile = NfsHotPursuit2V10Profile(
        {
            "recovery_storm_limit": 3,
            "recovery_storm_window_seconds": 60.0,
            "hud_stall_hard_restart_enabled": True,
        }
    )
    controller = FakeController()
    _hud_owned(profile)

    for now in (10.0, 20.0):
        action = profile._start_recovery(controller, _ctx(now=now, motion=0.12), "road lost")
        assert "hard-stall restart" not in action
        # Model the exact V9 failure mode: a recovery wiggle restores enough motion
        # to return to racing and reset the instantaneous HUD-stall streak.
        profile.phase = NfsPhase.RACING
        profile.hud_stall_since = None
        profile.hud_stall_recovery_attempts = 0

    action = profile._start_recovery(controller, _ctx(now=30.0, motion=0.12), "road lost")

    assert profile.hard_restart_stage == "pause"
    assert profile.hard_restart_attempts == 1
    assert profile.recovery_storm_triggers == 1
    assert "source=recovery-storm" in action
    assert "storm=3/3" in action
    assert ("tap", "start", 0.08) in controller.events


def test_recovery_storm_forgets_attempts_outside_window():
    profile = NfsHotPursuit2V10Profile(
        {
            "recovery_storm_limit": 3,
            "recovery_storm_window_seconds": 20.0,
            "hud_stall_hard_restart_enabled": True,
        }
    )
    controller = FakeController()
    _hud_owned(profile)

    for now in (1.0, 30.0, 60.0):
        profile._start_recovery(controller, _ctx(now=now, motion=0.1), "road lost")
        profile.phase = NfsPhase.RACING

    assert profile.hard_restart_stage is None
    assert profile.recovery_storm_triggers == 0
    assert list(profile.recovery_storm_starts) == [60.0]


def test_race_entry_accounting_separates_reacquisition():
    profile = NfsHotPursuit2V10Profile({})

    profile.race_entries = 1
    profile._reconcile_race_entries(0)
    assert profile.verified_race_entries == 1
    assert profile.race_entries == 1
    assert not profile.race_launch_armed

    profile.race_entries = 2
    profile._reconcile_race_entries(1)
    assert profile.verified_race_entries == 1
    assert profile.gameplay_reacquisitions == 1
    assert profile.race_entries == 1

    profile.race_launch_armed = True
    profile.race_entries = 2
    profile._reconcile_race_entries(1)
    assert profile.verified_race_entries == 2
    assert profile.race_entries == 2


def test_showmanship_requires_recovery_quiet_period():
    profile = NfsHotPursuit2V10Profile(
        {
            "showmanship_horn_enabled": True,
            "showmanship_horn_interval_seconds": 15.0,
            "showmanship_horn_warmup_seconds": 3.0,
            "showmanship_horn_min_road_confidence": 0.60,
            "showmanship_recovery_quiet_seconds": 60.0,
        }
    )
    profile.phase = NfsPhase.RACING
    profile.phase_since = 0.0
    profile.drive_mode = "racer"
    profile.road = RoadObservation(0.90, 0.02, 0.02, 0.55, 0.40, 0.45)
    profile.last_good_road = profile.road
    profile.last_steer = 0.05
    profile.last_recovery_at = 100.0

    profile.last_good_road_at = 150.0
    assert not profile._showmanship_safe(_ctx(now=150.0, motion=0.03))

    profile.last_good_road_at = 161.0
    assert profile._showmanship_safe(_ctx(now=161.0, motion=0.03))


def test_v10_telemetry_exposes_liveness_and_accounting():
    profile = NfsHotPursuit2V10Profile({})
    _hud_owned(profile)
    profile._note_recovery_start(10.0)
    profile.verified_race_entries = 1
    profile.gameplay_reacquisitions = 4

    state = profile.telemetry(_ctx(now=12.0))

    assert state["nfs_policy_version"] == 10
    assert state["nfs_verified_race_entries"] == 1
    assert state["nfs_gameplay_reacquisitions"] == 4
    assert state["nfs_recovery_storm_count"] == 1
    assert state["nfs_last_recovery_age"] == 2.0
