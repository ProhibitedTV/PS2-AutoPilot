import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v15 import VisualGoal
from ps2_autopilot.profiles.jak_and_daxter_v19 import JakAndDaxterV19Profile
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
        "nav_escape_backoff_seconds": 0.3,
        "nav_escape_pivot_seconds": 0.3,
        "nav_escape_drive_seconds": 0.8,
        "v19_stagnation_drive_seconds": 1.0,
        "v19_stagnation_macro_cooldown_seconds": 2.0,
    }
    cfg.update(extra)
    return JakAndDaxterV19Profile(cfg)


def ctx(now=1.0, motion=0.02):
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    return ProfileContext(
        frame=frame,
        previous_frame=frame.copy(),
        motion=motion,
        template=None,
        now=now,
    )


def test_loop_stagnation_scan_becomes_real_navigation_commit():
    p = profile()
    c = FakeController()
    action = p._start_land_scan(c, ctx(1.0), reason="loop/stagnation")
    assert p.navigation_commit_active is True
    assert p.land_scan_active is False
    assert p.navigation_commit_reason == "generic-stagnation"
    assert "backoff" in action
    assert c.left[-1][1] < 0.0


def test_ambiguous_scan_scores_relocate_instead_of_tiny_commit():
    p = profile(v19_scan_decision_margin=0.04)
    c = FakeController()
    p.land_scan_active = True
    p.land_scan_stage = "commit"
    p.land_scan_until = 10.0
    p.land_scan_left_score = 0.13
    p.land_scan_right_score = 0.14
    p.land_scan_choice = 1.0

    action = p._service_land_scan(c, ctx(1.0))
    assert p.ambiguous_scan_macros == 1
    assert p.navigation_commit_active is True
    assert p.navigation_commit_reason == "ambiguous-route-scan"
    assert "backoff" in action


def test_route_reward_scoring_uses_cached_hint_not_fresh_detector():
    p = profile()
    p.visual_goal = VisualGoal("orb", 0.1, 0.8, 0.002, 0.8, 1.5)

    def fail_if_called(_frame):
        raise AssertionError("route scan should not rerun collectible detectors")

    p._best_visual_goal = fail_if_called
    score = p._visual_interest_score(np.zeros((240, 360, 3), dtype=np.uint8))
    assert score > 0.0
    assert p.cached_scan_reward_reads == 1


def test_ledge_detector_is_staggered():
    p = profile(v19_ledge_refresh_seconds=0.5)
    p._refresh_ledge_cue(ctx(1.0))
    before = p.ledge_refresh_skips
    p._refresh_ledge_cue(ctx(1.1))
    assert p.ledge_refresh_skips == before + 1


def test_live_budget_defaults_reduce_perception_frequency():
    p = profile()
    assert p.goal_refresh_seconds >= 0.45
    assert p.cue_refresh_seconds >= 0.40
    assert p.water_geometry_max_width <= 360
    assert p.land_scan_cooldown_seconds >= 7.0


def test_registry_promotes_jak_to_v19():
    p = build_profile({"name": "jak_and_daxter", "ocr_enabled": False})
    assert isinstance(p, JakAndDaxterV19Profile)
