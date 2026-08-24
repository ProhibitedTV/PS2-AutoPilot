import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v15 import VisualGoal
from ps2_autopilot.profiles.jak_and_daxter_v18 import JakAndDaxterV18Profile
from ps2_autopilot.profiles.registry import build_profile


class FakeController:
    def __init__(self):
        self.left = []
        self.right = []
        self.taps = []

    def tap(self, action, duration=0.08):
        self.taps.append((action, duration))

    def hold(self, action):
        pass

    def release(self, action):
        pass

    def release_all(self):
        pass

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
        "target_hard_static_seconds": 2.0,
        "target_episode_timeout_seconds": 5.0,
        "reward_control_cooldown_seconds": 2.0,
        "nav_escape_backoff_seconds": 0.5,
        "nav_escape_pivot_seconds": 0.5,
        "nav_escape_drive_seconds": 1.0,
    }
    cfg.update(extra)
    return JakAndDaxterV18Profile(cfg)


def ctx(now=1.0, motion=0.02):
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    return ProfileContext(
        frame=frame,
        previous_frame=frame.copy(),
        motion=motion,
        template=None,
        now=now,
    )


def arm_goal(p, g):
    p.visual_goal = g
    p.visual_goal_last_kind = g.kind
    p.visual_goal_stable_frames = p.goal_stable_frames_required
    p._v16_now = 1.0


def test_orbs_are_breadcrumbs_not_direct_steering_commands():
    p = profile()
    arm_goal(p, VisualGoal("orb", 0.04, 0.81, 0.002, 0.90, 1.57))
    assert p._visual_goal_actionable() is False
    assert p._goal_has_navigation_authority(p.visual_goal) is False


def test_weak_power_cell_hint_does_not_take_control():
    p = profile()
    arm_goal(p, VisualGoal("power_cell", -0.38, 0.58, 0.002, 0.50, 1.95))
    assert p._visual_goal_actionable() is False


def test_strong_centered_cell_can_still_take_control_for_cell_objective():
    p = profile()
    arm_goal(p, VisualGoal("power_cell", -0.18, 0.62, 0.004, 0.78, 2.08))
    assert p._visual_goal_actionable() is True


def test_route_scan_owns_control_without_target_resolver_interrupt():
    p = profile()
    arm_goal(p, VisualGoal("power_cell", -0.18, 0.62, 0.004, 0.78, 2.08))
    p.land_scan_active = True
    p.target_signature = p._goal_signature(p.visual_goal)
    p.target_started_at = 1.0
    p.target_last_progress_at = 1.0
    assert p._visual_goal_actionable() is False
    assert p._target_stalled(ctx(5.0, motion=0.0)) is False


def test_blacklist_starts_macro_escape_and_reward_cooldown():
    p = profile()
    g = VisualGoal("power_cell", -0.25, 0.60, 0.003, 0.75, 2.0)
    arm_goal(p, g)
    p.target_signature = p._goal_signature(g)
    p.target_last_x = g.x
    p.target_last_y = g.y

    p._blacklist_current_target(ctx(3.0))

    assert p.navigation_commit_active is True
    assert p.navigation_commit_stage == "backoff"
    assert p.navigation_commit_direction > 0.0
    assert p.reward_control_suppressed_until > 3.0
    assert p.visual_goal.kind == "none"


def test_macro_escape_is_backoff_pivot_then_sustained_drive():
    p = profile()
    c = FakeController()
    p._begin_navigation_commit(ctx(1.0), reason="test", direction=1.0)

    a = p._service_navigation_commit(c, ctx(1.1))
    assert "backoff" in a
    assert c.left[-1][1] < 0.0

    a = p._service_navigation_commit(c, ctx(1.6))
    assert "backoff" in a
    assert p.navigation_commit_stage == "pivot"

    a = p._service_navigation_commit(c, ctx(1.7))
    assert "pivot" in a
    assert abs(c.left[-1][0]) >= 0.8

    a = p._service_navigation_commit(c, ctx(2.3))
    assert "pivot" in a
    assert p.navigation_commit_stage == "drive"

    a = p._service_navigation_commit(c, ctx(2.4))
    assert "sustained drive" in a
    assert c.left[-1][1] >= 0.7
    assert any(action == "cross" for action, _duration in c.taps)


def test_registry_promotes_jak_to_v18():
    p = build_profile({"name": "jak_and_daxter", "ocr_enabled": False})
    assert isinstance(p, JakAndDaxterV18Profile)
