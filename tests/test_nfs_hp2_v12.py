from __future__ import annotations

import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.nfs_hot_pursuit_2 import NfsPhase
from ps2_autopilot.profiles.nfs_hot_pursuit_2_v2 import NfsScreen
from ps2_autopilot.profiles.nfs_hot_pursuit_2_v12 import NfsHotPursuit2V12Profile
from ps2_autopilot.vision import TemplateMatch


def _ctx(
    *,
    now: float,
    name: str,
    score: float,
    motion: float = 0.008,
) -> ProfileContext:
    return ProfileContext(
        frame=np.zeros((360, 640, 3), dtype=np.uint8),
        motion=motion,
        template=TemplateMatch(name, score),
        now=now,
    )


def test_persistent_weak_world_racing_selected_claims_confirm_only_menu():
    profile = NfsHotPursuit2V12Profile(
        {"semantic_hint_min_score": 0.24, "semantic_hint_hold_seconds": 1.5}
    )

    assert (
        profile._recognized_screen(
            _ctx(
                now=10.0,
                name="nfs_main_menu_world_racing_selected_02",
                score=0.28,
            )
        )
        is NfsScreen.UNKNOWN
    )
    assert (
        profile._recognized_screen(
            _ctx(
                now=11.0,
                name="nfs_main_menu_world_racing_selected_03",
                score=0.29,
            )
        )
        is NfsScreen.UNKNOWN
    )
    assert (
        profile._recognized_screen(
            _ctx(
                now=11.6,
                name="nfs_main_menu_world_racing_selected_02",
                score=0.27,
            )
        )
        is NfsScreen.MAIN_WORLD_RACING
    )

    assert profile.semantic_hint_accepts == 1
    assert profile.semantic_hint_claim_active
    assert profile.last_template_score == 0.27


def test_numbered_quick_race_variants_share_one_semantic_hold():
    profile = NfsHotPursuit2V12Profile({"semantic_hint_hold_seconds": 1.0})

    first = profile._recognized_screen(
        _ctx(
            now=20.0,
            name="nfs_world_racing_quick_race_selected_01",
            score=0.31,
        )
    )
    second = profile._recognized_screen(
        _ctx(
            now=21.1,
            name="nfs_world_racing_quick_race_selected_02",
            score=0.30,
        )
    )

    assert first is NfsScreen.UNKNOWN
    assert second is NfsScreen.WORLD_QUICK_RACE
    assert profile.semantic_hint_candidates == 1
    assert profile.semantic_hint_accepts == 1


def test_weak_hint_refuses_directional_navigation():
    profile = NfsHotPursuit2V12Profile({"semantic_hint_hold_seconds": 0.5})

    for now in (30.0, 31.0, 32.0):
        screen = profile._recognized_screen(
            _ctx(
                now=now,
                name="nfs_main_menu_hot_pursuit_selected_01",
                score=0.32,
            )
        )
        assert screen is NfsScreen.UNKNOWN

    # Default route is World Racing, so Hot Pursuit would require Down. Weak evidence
    # is never allowed to own that action.
    assert profile.semantic_hint_accepts == 0
    assert profile.semantic_hint_rejections >= 1


def test_weak_hint_refuses_high_motion_scene():
    profile = NfsHotPursuit2V12Profile(
        {"semantic_hint_hold_seconds": 0.5, "semantic_hint_max_motion": 0.02}
    )

    for now in (40.0, 41.0):
        screen = profile._recognized_screen(
            _ctx(
                now=now,
                name="nfs_main_menu_world_racing_selected_02",
                score=0.30,
                motion=0.05,
            )
        )
        assert screen is NfsScreen.UNKNOWN

    assert profile.semantic_hint_accepts == 0


def test_strict_template_keeps_existing_menu_stability_and_resets_hint():
    profile = NfsHotPursuit2V12Profile({"template_threshold": 0.84})

    profile._recognized_screen(
        _ctx(
            now=50.0,
            name="nfs_main_menu_world_racing_selected_02",
            score=0.30,
        )
    )
    first_strict = profile._recognized_screen(
        _ctx(
            now=50.1,
            name="nfs_world_racing_quick_race_selected_01",
            score=0.90,
        )
    )

    assert first_strict is NfsScreen.UNKNOWN
    assert profile.semantic_hint_screen is None
    assert not profile.semantic_hint_claim_active

    screen = profile._recognized_screen(
        _ctx(
            now=50.2,
            name="nfs_world_racing_quick_race_selected_01",
            score=0.90,
        )
    )
    assert screen is NfsScreen.WORLD_QUICK_RACE


def test_gameplay_phase_never_uses_weak_menu_hint():
    profile = NfsHotPursuit2V12Profile({"semantic_hint_hold_seconds": 0.5})
    profile.phase = NfsPhase.RACING

    for now in (60.0, 61.0):
        assert (
            profile._recognized_screen(
                _ctx(
                    now=now,
                    name="nfs_main_menu_world_racing_selected_02",
                    score=0.31,
                )
            )
            is NfsScreen.UNKNOWN
        )

    assert profile.semantic_hint_accepts == 0


def test_v12_telemetry_exposes_temporal_semantic_state():
    profile = NfsHotPursuit2V12Profile(
        {
            "semantic_hint_min_score": 0.24,
            "semantic_hint_hold_seconds": 1.5,
            "semantic_hint_max_motion": 0.02,
        }
    )
    profile._recognized_screen(
        _ctx(
            now=70.0,
            name="nfs_main_menu_world_racing_selected_02",
            score=0.29,
        )
    )

    state = profile.telemetry(
        _ctx(
            now=70.75,
            name="nfs_main_menu_world_racing_selected_02",
            score=0.29,
        )
    )

    assert state["nfs_policy_version"] == 12
    assert state["nfs_semantic_hint_screen"] == "main_world_racing"
    assert state["nfs_semantic_hint_age"] == 0.75
    assert state["nfs_semantic_hint_min_score"] == 0.24
    assert state["nfs_semantic_hint_claim_active"] is False
