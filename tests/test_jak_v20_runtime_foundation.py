import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v20 import JakAndDaxterV20Profile
from ps2_autopilot.profiles.registry import build_profile


def profile(**extra):
    cfg = {
        "mode": "production",
        "ocr_enabled": False,
        "progress_probe_initial_delay_seconds": 999.0,
    }
    cfg.update(extra)
    return JakAndDaxterV20Profile(cfg)


def ctx(now=1.0, pressure="healthy", semantic=None):
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    return ProfileContext(
        frame=frame,
        previous_frame=frame.copy(),
        motion=0.02,
        template=None,
        now=now,
        semantic=dict(semantic or {}),
        performance={"loop_pressure": pressure},
    )


def test_runtime_pressure_stretches_expensive_perception_cadence():
    p = profile()
    p._apply_runtime_budget(ctx(1.0, "healthy"))
    healthy = (p.goal_refresh_seconds, p.cue_refresh_seconds, p.ledge_refresh_seconds)
    p._apply_runtime_budget(ctx(2.0, "critical"))
    critical = (p.goal_refresh_seconds, p.cue_refresh_seconds, p.ledge_refresh_seconds)
    assert critical[0] > healthy[0]
    assert critical[1] > healthy[1]
    assert critical[2] > healthy[2]
    assert p.v20_load_shed_ticks == 1


def test_atomic_navigation_commit_suppresses_goal_and_ledge_refresh():
    p = profile()
    p.navigation_commit_active = True
    p._refresh_visual_goal(ctx(1.0))
    p._refresh_ledge_cue(ctx(1.0))
    assert p.v20_reflex_perception_skips == 2


def test_semantic_xyz_produces_true_translation_evidence():
    p = profile()
    p._update_semantic_translation(ctx(1.0, semantic={"jak_x": 1.0, "jak_y": 2.0, "jak_z": 3.0}))
    p._update_semantic_translation(ctx(1.2, semantic={"jak_x": 4.0, "jak_y": 6.0, "jak_z": 3.0}))
    assert p.semantic_position_samples == 2
    assert abs(p.semantic_translation_delta - 5.0) < 1e-6
    assert abs(p.semantic_translation_total - 5.0) < 1e-6


def test_registry_promotes_jak_to_v20():
    p = build_profile({"name": "jak_and_daxter", "ocr_enabled": False})
    assert isinstance(p, JakAndDaxterV20Profile)
