import numpy as np

from ps2_autopilot.jak_knowledge import JakControlMode
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v15 import VisualGoal
from ps2_autopilot.profiles.jak_and_daxter_v16 import (
    JakAndDaxterV16Profile,
    ShorelineRisk,
)
from ps2_autopilot.profiles.registry import build_profile


class FakeController:
    def __init__(self):
        self.left = []
        self.right = []
        self.taps = []
        self.holds = []
        self.releases = []
        self.release_all_count = 0

    def tap(self, action, duration=0.08):
        self.taps.append((action, duration))

    def hold(self, action):
        self.holds.append(action)

    def release(self, action):
        self.releases.append(action)

    def release_all(self):
        self.release_all_count += 1

    def set_left_stick(self, x, y):
        self.left.append((x, y))

    def set_right_stick(self, x, y):
        self.right.append((x, y))

    def neutral_sticks(self):
        self.set_left_stick(0.0, 0.0)
        self.set_right_stick(0.0, 0.0)


def profile(**extra):
    cfg = {
        "mode": "production",
        "ocr_enabled": False,
        "progress_probe_initial_delay_seconds": 999.0,
        "goal_stable_frames_required": 1,
        "target_static_seconds": 1.0,
        "mobility_stall_seconds": 0.8,
    }
    cfg.update(extra)
    return JakAndDaxterV16Profile(cfg)


def ctx(now=1.0, motion=0.02, frame=None, previous=None, semantic=None):
    if frame is None:
        frame = np.zeros((240, 360, 3), dtype=np.uint8)
    if previous is None:
        previous = frame.copy()
    return ProfileContext(
        frame=frame,
        previous_frame=previous,
        motion=motion,
        template=None,
        now=now,
        semantic=semantic or {},
    )


def test_stalled_reward_target_escalates_to_jump_resolution():
    p = profile(target_static_seconds=0.8)
    c = FakeController()
    p.visual_goal = VisualGoal("power_cell", 0.25, 0.60, 0.004, 0.8, 2.0)
    p.visual_goal_stable_frames = 4
    p._track_target(ctx(1.0, 0.02))
    # Keep the same target with negligible motion and no approach progress.
    p.target_last_progress_at = 1.0
    p.target_low_motion_since = 1.0
    assert p._target_stalled(ctx(2.0, 0.001)) is True
    action = p._start_target_resolution(c, ctx(2.0, 0.001))
    assert "jump/ledge solve" in action
    assert any(name == "cross" for name, _duration in c.taps)
    assert c.left[-1][1] > 0.0


def test_repeated_failed_target_is_blacklisted_instead_of_monopolizing_navigation():
    p = profile(target_max_resolution_attempts=2, target_blacklist_seconds=10.0)
    c = FakeController()
    p.visual_goal = VisualGoal("power_cell", 0.35, 0.72, 0.004, 0.8, 2.0)
    p.visual_goal_stable_frames = 4
    p.target_signature = p._goal_signature(p.visual_goal)
    p.target_resolution_attempts = 2
    action = p._start_target_resolution(c, ctx(5.0, 0.001))
    assert "rejected/blacklisted" in action
    assert p.target_blacklists == 1
    assert p.visual_goal.kind == "none"
    assert p.target_blacklist


def test_shoreline_guard_turns_away_from_wetter_left_foreground():
    p = profile()
    c = FakeController()
    p.shoreline_risk = ShorelineRisk(total=0.20, left=0.38, center=0.16, right=0.03, active=True)
    action = p._service_shoreline_guard(c, ctx(2.0))
    assert "shoreline guard R" in action
    assert c.left[-1][0] > 0.0
    assert c.left[-1][1] > 0.0


def test_low_motion_land_contact_starts_proactive_small_ledge_hop():
    p = profile(mobility_stall_seconds=0.6)
    c = FakeController()
    p.water_escape_active = False
    p.water_geometry_confirmed = False
    p.shoreline_risk = ShorelineRisk()
    p.visual_goal = VisualGoal()
    p.mobility_low_motion_since = 1.0
    assert p._mobility_due(ctx(1.8, 0.001)) is True
    action = p._start_mobility_probe(c, ctx(1.8, 0.001))
    assert "hop small ledge" in action
    assert any(name == "cross" for name, _duration in c.taps)


def test_zoomer_stall_uses_brake_and_hard_turn_hop_not_on_foot_jump_logic():
    p = profile()
    c = FakeController()
    p.specialist_stall_since = 1.0
    action = p._zoomer(c, ctx(3.0, 0.001))
    assert "Zoomer brake" in action
    assert any(name == "square" for name, _duration in c.taps)
    assert any(name in {"l1", "r1"} for name, _duration in c.taps)
    assert not any(name == "cross" for name, _duration in c.taps)


def test_semantic_mode_hint_can_select_specialist_without_template():
    p = profile()
    p._semantic_refresh(ctx(1.0, semantic={"control_mode": "zoomer"}))
    p._update_control_mode()
    assert p.control_mode == JakControlMode.ZOOMER


def test_fishing_tracks_dominant_motion_horizontally():
    p = profile()
    c = FakeController()
    prev = np.zeros((240, 360, 3), dtype=np.uint8)
    frame = prev.copy()
    frame[100:135, 260:300] = 255
    action = p._fishing(c, ctx(1.0, 0.02, frame=frame, previous=prev))
    assert "fishing track" in action
    assert c.left[-1][0] > 0.0


def test_registry_promotes_jak_to_v16():
    p = build_profile({"name": "jak_and_daxter", "ocr_enabled": False})
    assert isinstance(p, JakAndDaxterV16Profile)
