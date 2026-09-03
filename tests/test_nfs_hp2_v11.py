from __future__ import annotations

import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.nfs_hp2_hud import GameplayHudObservation
from ps2_autopilot.nfs_hp2_vision import RoadObservation
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.nfs_hot_pursuit_2 import NfsPhase
from ps2_autopilot.profiles.nfs_hot_pursuit_2_v2 import NfsScreen
from ps2_autopilot.profiles.nfs_hot_pursuit_2_v11 import NfsHotPursuit2V11Profile


class FakeController(Controller):
    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.left_stick = (0.0, 0.0)

    def tap(self, action: str, duration: float = 0.08) -> None:
        self.events.append(("tap", action, duration))

    def hold(self, action: str) -> None:
        self.events.append(("hold", action))

    def release(self, action: str) -> None:
        self.events.append(("release", action))

    def release_all(self) -> None:
        self.events.append(("release_all",))

    def set_left_stick(self, x: float, y: float) -> None:
        self.left_stick = (x, y)
        self.events.append(("left_stick", x, y))

    def set_right_stick(self, x: float, y: float) -> None:
        self.events.append(("right_stick", x, y))


def _ctx(*, now: float, motion: float = 0.03) -> ProfileContext:
    return ProfileContext(
        frame=np.zeros((360, 640, 3), dtype=np.uint8),
        motion=motion,
        template=None,
        now=now,
    )


def _hud_owned(profile: NfsHotPursuit2V11Profile) -> None:
    profile.hud = GameplayHudObservation(0.95, 0.95, 0.95, 0.95, 0.80)


def _racing(profile: NfsHotPursuit2V11Profile, *, since: float = 0.0) -> None:
    profile.phase = NfsPhase.RACING
    profile.phase_since = since


def test_launch_guard_holds_throttle_and_caps_first_frame_steering():
    profile = NfsHotPursuit2V11Profile(
        {"launch_guard_seconds": 4.0, "launch_guard_max_steer": 0.36}
    )
    controller = FakeController()
    _racing(profile, since=10.0)
    profile.last_steer = 0.90
    profile.road = RoadObservation.unavailable("overwide-surface")

    action = profile._drive(controller, _ctx(now=12.0))

    assert action.startswith("v11 launch guard")
    assert ("hold", "cross") in controller.events
    assert ("release", "square") in controller.events
    assert controller.left_stick == (0.36, 0.0)
    assert profile.launch_guard_ticks == 1


def test_moving_blind_drives_forward_then_restarts_without_reversing():
    profile = NfsHotPursuit2V11Profile(
        {
            "launch_guard_seconds": 1.0,
            "blind_restart_seconds": 7.0,
            "blind_motion_threshold": 0.012,
        }
    )
    controller = FakeController()
    _racing(profile)
    _hud_owned(profile)
    profile.last_steer = 0.50
    profile.road = RoadObservation.unavailable("reverse-perspective")

    action = profile._drive(controller, _ctx(now=10.0, motion=0.03))
    assert action.startswith("v11 moving-blind")
    assert ("hold", "cross") in controller.events
    assert ("hold", "square") not in controller.events
    assert abs(controller.left_stick[0]) <= profile.blind_max_steer

    action = profile._drive(controller, _ctx(now=17.1, motion=0.03))
    assert "source=moving-blind" in action
    assert profile.hard_restart_stage == "pause"
    assert profile.blind_moving_restarts == 1
    assert ("tap", "start", 0.08) in controller.events
    assert ("hold", "square") not in controller.events


def test_wrong_way_evidence_restarts_instead_of_blind_uturn():
    profile = NfsHotPursuit2V11Profile({})
    controller = FakeController()
    _racing(profile)
    _hud_owned(profile)

    action = profile._start_recovery(controller, _ctx(now=20.0), "wrong-way HUD")

    assert "source=wrong-way" in action
    assert profile.wrong_way_restarts == 1
    assert profile.hard_restart_stage == "pause"
    assert profile.recoveries == 0


def test_good_road_steering_is_capped_below_recovery_authority():
    profile = NfsHotPursuit2V11Profile(
        {"launch_guard_seconds": 1.0, "racing_max_steer": 0.62}
    )
    controller = FakeController()
    _racing(profile)
    profile.last_steer = 0.90
    profile.road = RoadObservation(0.90, 0.90, 0.60, 0.55, 0.35, 0.40)

    action = profile._drive(controller, _ctx(now=10.0))

    assert "v11-steer-cap" in action
    assert profile.last_steer == 0.62
    assert controller.left_stick == (0.62, 0.0)
    assert profile.racing_steer_clamps == 1


def test_v11_telemetry_exposes_road_rejection_and_safety_state():
    profile = NfsHotPursuit2V11Profile({})
    profile.road = RoadObservation.unavailable("overwide-surface")
    profile.blind_moving_since = 10.0
    profile.blind_moving_ticks = 12

    state = profile.telemetry(_ctx(now=12.5))

    assert state["nfs_policy_version"] == 11
    assert state["nfs_road_rejection_reason"] == "overwide-surface"
    assert state["nfs_blind_moving_age"] == 2.5
    assert state["nfs_blind_moving_ticks"] == 12


def test_unknown_road_takeover_requires_fixed_gameplay_hud():
    profile = NfsHotPursuit2V11Profile({"race_enter_frames": 1})
    profile.phase = NfsPhase.CALIBRATION
    profile.race_evidence_frames = 20

    allowed = profile._road_takeover_allowed(
        _ctx(now=20.0),
        NfsScreen.UNKNOWN,
    )

    assert not allowed
    assert profile.fast_takeover_hud_blocks == 1

    _hud_owned(profile)
    assert profile._road_takeover_allowed(_ctx(now=20.1), NfsScreen.UNKNOWN)
