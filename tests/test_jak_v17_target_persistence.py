import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v15 import VisualGoal
from ps2_autopilot.profiles.jak_and_daxter_v17 import JakAndDaxterV17Profile
from ps2_autopilot.profiles.registry import build_profile


class FakeController:
    def __init__(self):
        self.left = []
        self.right = []
        self.taps = []
        self.releases = []
        self.release_all_count = 0

    def tap(self, action, duration=0.08):
        self.taps.append((action, duration))

    def hold(self, action):
        pass

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
        "target_hard_static_seconds": 2.0,
        "target_episode_timeout_seconds": 5.0,
        "target_resolution_progress_min": 0.04,
        "target_max_resolution_attempts": 4,
    }
    cfg.update(extra)
    return JakAndDaxterV17Profile(cfg)


def ctx(now=1.0, motion=0.02):
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    return ProfileContext(
        frame=frame,
        previous_frame=frame.copy(),
        motion=motion,
        template=None,
        now=now,
    )


def goal(x=-0.33, y=0.45, area=0.002, score=2.0):
    return VisualGoal("power_cell", x, y, area, 0.8, score)


def arm_goal(p, g):
    p.visual_goal = g
    p.visual_goal_last_kind = g.kind
    p.visual_goal_stable_frames = p.goal_stable_frames_required


def test_live_power_cell_jitter_stays_one_target_track():
    p = profile()
    first = goal(-0.33, 0.45)
    arm_goal(p, first)
    p._track_target(ctx(1.0))
    started = p.target_started_at

    # The first V16 soak showed the same false target jittering through roughly this
    # region. It must not become a brand-new target every time the detector moves.
    moved = goal(-0.46, 0.65, area=0.003)
    arm_goal(p, moved)
    p._track_target(ctx(1.4))

    assert p.target_started_at == started
    assert p.target_signature is not None
    assert p.target_track_last_seen_at == 1.4


def test_hard_no_progress_stall_does_not_require_low_frame_motion():
    p = profile(target_hard_static_seconds=2.0, target_episode_timeout_seconds=8.0)
    g = goal(-0.35, 0.45)
    arm_goal(p, g)
    p._track_target(ctx(1.0, motion=0.03))

    # Character/camera animation can keep full-frame motion high while Jak pushes
    # uselessly into geometry. V17 must still time out the target.
    arm_goal(p, g)
    p._track_target(ctx(3.2, motion=0.03))
    assert p._target_stalled(ctx(3.2, motion=0.03)) is True
    assert p.target_hard_stalls >= 1


def test_resolution_does_not_call_camera_animation_success():
    p = profile(target_resolution_progress_min=0.04)
    c = FakeController()
    g = goal(-0.35, 0.45)
    arm_goal(p, g)
    p._track_target(ctx(1.0))

    p.target_resolution_active = True
    p.target_resolution_stage = "jump"
    p.target_resolution_until = 1.2
    p.target_resolution_start_metric = p._target_metric(g)
    p.target_resolution_attempts = 1
    p._refresh_visual_goal = lambda _ctx: None

    # High image motion alone used to end the V16 resolver as "cleared".
    action = p._service_target_resolution(c, ctx(1.5, motion=0.05))
    assert "geometry progress confirmed" not in action
    assert p.target_resolution_false_successes_prevented >= 1
    assert p.target_resolution_attempts >= 2


def test_resolution_accepts_real_target_closeness_progress():
    p = profile(target_resolution_progress_min=0.04)
    c = FakeController()
    original = goal(-0.35, 0.45)
    arm_goal(p, original)
    p._track_target(ctx(1.0))

    p.target_resolution_active = True
    p.target_resolution_stage = "jump"
    p.target_resolution_until = 1.2
    p.target_resolution_start_metric = p._target_metric(original)
    p.target_resolution_attempts = 1

    closer = goal(-0.20, 0.62)
    arm_goal(p, closer)
    p._refresh_visual_goal = lambda _ctx: None
    action = p._service_target_resolution(c, ctx(1.5, motion=0.03))

    assert "geometry progress confirmed" in action
    assert p.target_resolution_progresses == 1
    assert p.target_resolution_active is False


def test_attention_budget_blacklists_persistent_unreachable_target():
    p = profile(target_episode_timeout_seconds=5.0, target_max_resolution_attempts=4)
    c = FakeController()
    g = goal(-0.35, 0.45)
    arm_goal(p, g)
    p._track_target(ctx(1.0))
    p.target_resolution_attempts = 2

    action = p._start_target_resolution(c, ctx(6.2))
    assert "budget exhausted" in action
    assert p.target_blacklists == 1
    assert p.visual_goal.kind == "none"


def test_neighbor_blacklist_blocks_same_false_object_after_small_jitter():
    p = profile(target_blacklist_neighbor_bins=1)
    p._v16_now = 2.0
    a = goal(-0.33, 0.45)
    b = goal(-0.42, 0.58)
    p.target_blacklist[p._goal_signature(a)] = 20.0
    assert p._neighbor_blacklisted(b) is True


def test_registry_promotes_jak_to_v17():
    p = build_profile({"name": "jak_and_daxter", "ocr_enabled": False})
    assert isinstance(p, JakAndDaxterV17Profile)
