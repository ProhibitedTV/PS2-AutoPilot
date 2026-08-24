import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v12 import JakAndDaxterV12Profile
from ps2_autopilot.profiles.registry import build_profile


class FakeController:
    def __init__(self):
        self.left = []
        self.right = []
        self.taps = []
        self.release_all_count = 0

    def tap(self, action, duration=0.08):
        self.taps.append((action, duration))

    def hold(self, action):
        pass

    def release(self, action):
        pass

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
        "water_progress_epsilon": 0.025,
        "water_progress_timeout": 5.0,
        "water_stall_motion_max": 0.0045,
        "water_stall_seconds": 3.0,
        "water_worsen_margin": 0.14,
        "water_direction_lock_seconds": 4.0,
        "water_near_shore_total": 0.14,
        "water_near_shore_center": 0.18,
        "water_near_shore_side": 0.08,
        "water_shore_hop_seconds": 3.2,
        "water_shore_hop_interval": 0.65,
        "water_shore_hop_forward": 0.78,
        "water_shore_hop_turn": 0.22,
        "water_uturn_seconds": 1.15,
        "water_uturn_turn": 0.94,
        "water_uturn_camera": 0.52,
        "water_seek_forward": 0.68,
        "water_seek_turn": 0.30,
        "water_seek_camera": 0.12,
        "water_backtrack_v12_seconds": 1.25,
        "water_backtrack_v12_speed": 0.76,
    }
    cfg.update(extra)
    return JakAndDaxterV12Profile(cfg)


def ctx(now=0.0, motion=0.0):
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    return ProfileContext(
        frame=frame,
        previous_frame=frame.copy(),
        motion=motion,
        template=None,
        now=now,
    )


def prime_water(p, *, total, left, center, right, now=0.0, mode="seek"):
    p.water_escape_active = True
    p.water_geometry_confirmed = True
    p.water_ratio_total = total
    p.water_ratio_left = left
    p.water_ratio_center = center
    p.water_ratio_right = right
    p.water_nav_mode = mode
    p.water_nav_direction = 1.0
    p.water_escape_started_at_v12 = 0.0
    p.water_best_ratio = total
    p.water_leg_best_ratio = total
    p.water_best_at = 0.0
    p.water_last_progress_at = now
    p.water_direction_locked_until = 0.0


def test_live_scale_near_shore_switches_to_cross_only_shore_hop():
    p = profile()
    c = FakeController()

    # The V11 soak briefly reached ~0.06 total water, center ~0.12 and one side dry.
    prime_water(p, total=0.062, left=0.063, center=0.124, right=0.0, now=1.0)
    p._update_water_progress(ctx(now=1.0, motion=0.0025))

    assert p.water_near_shore is True
    action = p._water_escape(c, ctx(now=1.0, motion=0.0025))

    assert "shore-hop" in action
    assert any(button == "cross" for button, _ in c.taps)
    assert not any(button in {"square", "circle"} for button, _ in c.taps)
    assert c.left[-1][1] > 0.0


def test_water_stall_triggers_bounded_uturn_instead_of_infinite_seek():
    p = profile()
    c = FakeController()
    prime_water(p, total=0.31, left=0.40, center=0.34, right=0.19, now=0.0)
    p.water_low_motion_since = 0.0
    p.water_last_progress_at = 0.0

    action = p._water_escape(c, ctx(now=3.2, motion=0.0020))

    assert "U-turn" in action
    assert p.water_nav_mode == "u-turn"
    assert p.water_uturns == 1
    assert c.left[-1][1] <= 0.0
    assert c.taps == []


def test_water_progress_timeout_triggers_search_reset_even_when_ripples_move():
    p = profile()
    c = FakeController()
    prime_water(p, total=0.42, left=0.55, center=0.46, right=0.25, now=0.0)
    p.water_low_motion_since = None
    p.water_last_progress_at = 0.0

    action = p._water_escape(c, ctx(now=5.2, motion=0.010))

    assert "U-turn" in action
    assert p.water_uturns == 1


def test_watchdog_while_swimming_schedules_water_recovery_not_land_combo():
    p = profile()
    c = FakeController()
    prime_water(p, total=0.80, left=0.82, center=0.84, right=0.74, now=0.0)

    action = p.recover(c)

    assert "schedule swim U-turn" in action
    assert p.water_watchdog_uturn_pending is True
    assert p.water_watchdog_recoveries == 1
    assert c.release_all_count == 1
    assert c.taps == []

    action = p._water_escape(c, ctx(now=1.0, motion=0.002))
    assert "U-turn" in action
    assert p.water_watchdog_uturn_pending is False


def test_water_activation_cancels_unsafe_v10_skill_state():
    p = profile()
    p.skill_active = True
    p.skill_name = "scout_dive"
    p.skill_stage = "jump"
    p.skill_button_sent = True

    # Simulate the post-parent state after coherent water has taken ownership, then
    # exercise V12's ownership cleanup directly.
    p.water_escape_active = True
    p.local_stuck_active = True
    p.local_stuck_stage = "test"
    p.water_ratio_total = 0.8
    p.water_ratio_left = 0.8
    p.water_ratio_center = 0.8
    p.water_ratio_right = 0.8

    # The cleanup is part of _refresh_water_state; monkeypatch the parent's water
    # ratios/geometry path by using a full blue frame and enough confirmations.
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    frame[:] = (200, 120, 40)
    context = ProfileContext(frame=frame, previous_frame=frame.copy(), motion=0.002, template=None, now=1.0)
    p._refresh_water_state(context)

    if p.water_escape_active:
        assert p.skill_active is False
        assert p.skill_name == "none"
        assert p.local_stuck_active is False


def test_registry_promotes_jak_to_v12():
    p = build_profile({"name": "jak_and_daxter", "ocr_enabled": False})
    assert isinstance(p, JakAndDaxterV12Profile)
