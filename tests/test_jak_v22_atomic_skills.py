import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.profiles import JakAndDaxterProfile
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v22 import JakAndDaxterV22Profile
from ps2_autopilot.profiles.registry import build_profile


class RecordingController(Controller):
    def __init__(self) -> None:
        self.taps: list[str] = []
        self.left: tuple[float, float] = (0.0, 0.0)
        self.right: tuple[float, float] = (0.0, 0.0)

    def tap(self, action: str, duration: float = 0.08) -> None:
        del duration
        self.taps.append(action)

    def hold(self, action: str) -> None:
        del action

    def release(self, action: str) -> None:
        del action

    def release_all(self) -> None:
        pass

    def set_left_stick(self, x: float, y: float) -> None:
        self.left = (float(x), float(y))

    def set_right_stick(self, x: float, y: float) -> None:
        self.right = (float(x), float(y))


def profile(**overrides) -> JakAndDaxterV22Profile:
    cfg = {
        "ocr_enabled": False,
        "random_seed": 22,
        "production_random_seed": 22,
        "learning_enabled": False,
        "v22_skill_align_seconds": 0.05,
        "v22_skill_verify_seconds": 0.10,
        "v22_skill_timeout_seconds": 2.0,
    }
    cfg.update(overrides)
    return JakAndDaxterV22Profile(cfg)


def ctx(now: float, motion: float = 0.02) -> ProfileContext:
    return ProfileContext(
        frame=np.zeros((120, 160, 3), dtype=np.uint8),
        previous_frame=np.zeros((120, 160, 3), dtype=np.uint8),
        motion=motion,
        template=None,
        now=now,
        semantic={},
        performance={},
    )


def test_registered_jak_profile_is_v22():
    assert JakAndDaxterProfile is JakAndDaxterV22Profile
    built = build_profile({"name": "jak_and_daxter", "ocr_enabled": False})
    assert isinstance(built, JakAndDaxterV22Profile)


def test_roll_jump_runs_commit_airborne_verify_and_reports_success():
    p = profile()
    pad = RecordingController()

    p._start_roll_jump(ctx(1.0), heading=0.10)
    assert p.atomic_skills.active is not None
    assert p.atomic_skills.active.name == "roll_jump"
    assert p.atomic_skills.active.phase == "align"

    p._service_skill(pad, ctx(1.06))
    assert "l1" in pad.taps
    assert p.atomic_skills.active is not None
    assert p.atomic_skills.active.phase == "roll"

    p._service_skill(pad, ctx(1.30))
    assert "cross" in pad.taps
    assert p.atomic_skills.active is not None
    assert p.atomic_skills.active.phase == "airborne"

    p._service_skill(pad, ctx(1.90, motion=0.03))
    assert not p.atomic_skills.is_active
    telemetry = p.telemetry(ctx(1.91))
    assert telemetry["jak_policy_version"] == "v22"
    assert telemetry["jak_skill_roll_jump_attempts"] == 1
    assert telemetry["jak_skill_roll_jump_successes"] == 1
    assert telemetry["jak_skill_motion_verifications_v22"] >= 1


def test_mobility_hop_failure_upgrades_once_to_double_jump():
    p = profile(v22_skill_verify_motion=0.05)
    pad = RecordingController()

    p.mobility_low_motion_since = 0.0
    p._start_mobility_probe(pad, ctx(2.0, motion=0.0))
    assert p.atomic_skills.active is not None
    assert p.atomic_skills.active.name == "hop_step"

    p._service_skill(pad, ctx(2.06, motion=0.0))  # commit hop
    p._service_skill(pad, ctx(2.40, motion=0.0))  # enter verify
    action = p._service_skill(pad, ctx(2.60, motion=0.0))  # fail verify -> double jump

    assert "double_jump" in action
    assert p.atomic_skills.active is not None
    assert p.atomic_skills.active.name == "double_jump"
    assert p.v22_hop_upgrades == 1
    telemetry = p.telemetry(ctx(2.61, motion=0.0))
    assert telemetry["jak_skill_hop_step_failures"] == 1
    assert telemetry["jak_skill_double_jump_attempts"] == 1


def test_semantic_xyz_can_verify_skill_when_position_is_trusted():
    p = profile(v22_skill_verify_motion=0.05)
    pad = RecordingController()
    p.learning_position_validated = True
    p.learning_current_position = (10.0, 20.0, 30.0)

    p._start_roll_jump(ctx(4.0, motion=0.0), heading=0.0)
    p._service_skill(pad, ctx(4.06, motion=0.0))
    p._service_skill(pad, ctx(4.30, motion=0.0))
    p.learning_current_position = (10.7, 20.0, 30.0)
    p._service_skill(pad, ctx(4.90, motion=0.0))

    assert not p.atomic_skills.is_active
    telemetry = p.telemetry(ctx(4.91, motion=0.0))
    assert telemetry["jak_skill_roll_jump_successes"] == 1
    assert telemetry["jak_skill_semantic_verifications_v22"] == 1
    assert str(telemetry["jak_skill_last_verification_v22"]).startswith("xyz:")


def test_atomic_skill_can_be_safety_aborted_without_counting_success():
    p = profile()
    p._start_roll_jump(ctx(6.0), heading=0.0)
    assert p.atomic_skills.is_active
    p._abort_atomic(ctx(6.1), "water")
    telemetry = p.telemetry(ctx(6.2))
    assert telemetry["jak_skill_roll_jump_safety_aborts"] == 1
    assert telemetry["jak_skill_roll_jump_successes"] == 0
    assert telemetry["jak_skill_preemptions_v22"] == 1
