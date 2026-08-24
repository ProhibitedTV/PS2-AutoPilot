from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.jak_experience import JakExperienceMemory
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v21 import (
    JakAndDaxterV21Profile,
    LearningSample,
)
from ps2_autopilot.profiles.registry import build_profile


def profile(tmp_path, **extra):
    cfg = {
        "mode": "production",
        "ocr_enabled": False,
        "progress_probe_initial_delay_seconds": 999.0,
        "learning_memory_path": str(tmp_path / "jak_experience.json"),
    }
    cfg.update(extra)
    return JakAndDaxterV21Profile(cfg)


def ctx(now=1.0, *, x=0.0, y=0.0, z=0.0, semantic=True):
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    state = {}
    if semantic:
        state = {
            "pine_available": True,
            "pine_verified": True,
            "pine_stale": False,
            "pine_schema_verified": True,
            "jak_x": x,
            "jak_y": y,
            "jak_z": z,
            "power_cells": 0,
            "precursor_orbs": 0,
            "scout_flies": 0,
        }
    return ProfileContext(
        frame=frame,
        previous_frame=frame.copy(),
        motion=0.02,
        template=None,
        now=now,
        semantic=state,
        performance={"loop_pressure": "healthy"},
    )


def test_experience_memory_accumulates_and_persists(tmp_path):
    path = tmp_path / "memory.json"
    memory = JakExperienceMemory(path, bucket_size=3.0, save_interval_seconds=0.1)
    key = "xyz:1:2:3"

    memory.mark([key], now=1.0, event="water-entry", danger=0.72, water_entry=True)
    memory.mark([key], now=2.0, event="water-entry", danger=0.72, water_entry=True)
    assert memory.score([key]) == 1.44
    assert memory.total_water_events == 2
    assert memory.maybe_save(2.0, force=True)

    restored = JakExperienceMemory(path, bucket_size=3.0, save_interval_seconds=0.1)
    assert restored.score([key]) == 1.44
    assert restored.total_water_events == 2

    restored.mark([key], now=3.0, event="progress", reward=1.0)
    assert restored.score([key]) < 1.44


def test_escape_direction_learning_prefers_successful_side(tmp_path):
    memory = JakExperienceMemory(tmp_path / "memory.json")
    key = "xyz:0:0:0"
    memory.mark([key], now=1.0, event="danger", danger=2.0)
    memory.mark_escape_result([key], direction=1.0, success=True, now=2.0)
    memory.mark_escape_result([key], direction=-1.0, success=False, now=3.0)
    assert memory.preferred_escape_direction([key]) == 1.0


def test_safe_visits_do_not_create_persistent_scene_noise(tmp_path):
    memory = JakExperienceMemory(tmp_path / "memory.json")
    memory.touch(["scene:abc", "xyz:0:0:0"], now=1.0)
    assert memory.cells == {}
    assert not memory.dirty


def test_scene_hash_is_stable_across_brightness_change(tmp_path):
    p = profile(tmp_path)
    gray = np.tile(np.arange(32, 224, dtype=np.uint8), (160, 1))
    gray = cv2.resize(gray, (360, 240), interpolation=cv2.INTER_LINEAR)
    frame_a = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    frame_b = cv2.convertScaleAbs(frame_a, alpha=0.70, beta=15)
    assert p._scene_key(frame_a) == p._scene_key(frame_b)


def test_semantic_position_must_prove_motion_before_coordinate_learning(tmp_path):
    p = profile(tmp_path)

    first = ctx(1.0, x=0.0, y=0.0, z=0.0)
    assert p._validated_position(first) is None
    keys = p._keys_for(first, None)
    assert not any(key.startswith("xyz:") for key in keys)

    second = ctx(2.0, x=0.50, y=0.0, z=0.0)
    position = p._validated_position(second)
    assert position == (0.5, 0.0, 0.0)
    assert p.learning_position_validated
    keys = p._keys_for(second, position)
    assert any(key.startswith("xyz:") for key in keys)


def test_repeated_water_route_becomes_persistent_danger(tmp_path):
    p = profile(tmp_path)
    c = ctx(10.0, semantic=False)
    keys = ("xyz:0:0:0", "scene:water-route")
    p.learning_current_keys = keys
    p.learning_trail.append(LearningSample(9.5, keys, None))

    p._record_water_entry(c)
    assert p.experience.score(keys) == p.learning_water_penalty
    assert p._episode_danger_active(c)

    c2 = ctx(12.0, semantic=False)
    p.learning_trail.append(LearningSample(11.5, keys, None))
    p._record_water_entry(c2)
    assert p.experience.score(keys) >= p.learning_danger_threshold
    assert p.learning_water_entries == 2


def test_respawn_after_recent_hazard_strongly_penalizes_recent_route(tmp_path):
    p = profile(tmp_path, learning_respawn_distance=5.0)
    keys = ("xyz:1:0:0", "scene:bad-shore")
    p.learning_position_validated = True
    p.learning_last_position = (1.0, 0.0, 0.0)
    p.learning_current_keys = keys
    p.learning_trail.append(LearningSample(9.0, keys, (1.0, 0.0, 0.0)))
    p.learning_recent_hazard_until = 20.0

    p._observe_learning(ctx(10.0, x=20.0, y=0.0, z=0.0))
    assert p.learning_respawns_seen == 1
    assert p.experience.score(keys) >= p.learning_respawn_penalty


def test_registry_promotes_jak_to_v21(tmp_path):
    p = build_profile(
        {
            "name": "jak_and_daxter",
            "ocr_enabled": False,
            "learning_memory_path": str(tmp_path / "registry-memory.json"),
        }
    )
    assert isinstance(p, JakAndDaxterV21Profile)
