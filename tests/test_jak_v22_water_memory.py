from __future__ import annotations

import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v21 import LearningSample
from ps2_autopilot.profiles.jak_and_daxter_v22_hardened import JakAndDaxterV22Profile


def profile(tmp_path, **overrides) -> JakAndDaxterV22Profile:
    cfg = {
        "mode": "production",
        "ocr_enabled": False,
        "learning_enabled": True,
        "learning_memory_path": str(tmp_path / "jak_experience.json"),
        "progress_probe_initial_delay_seconds": 999.0,
    }
    cfg.update(overrides)
    return JakAndDaxterV22Profile(cfg)


def ctx(now: float) -> ProfileContext:
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    return ProfileContext(
        frame=frame,
        previous_frame=frame.copy(),
        motion=0.01,
        template=None,
        now=now,
        semantic={},
        performance={},
    )


def seed_water_approach(p: JakAndDaxterV22Profile, now: float = 10.0) -> tuple[str, ...]:
    keys = ("xyz:4:0:2", "scene:confirmed-water-approach")
    p.learning_current_keys = keys
    p.learning_trail.append(LearningSample(now - 0.5, keys, None))
    return keys


def test_one_confirmed_water_entry_is_persistently_dangerous(tmp_path):
    p = profile(tmp_path)
    keys = seed_water_approach(p)

    p._record_water_entry(ctx(10.0))

    assert p.learning_water_penalty >= p.learning_danger_threshold
    assert p.experience.score(keys) >= p.learning_danger_threshold
    assert set(p.v22_water_entry_keys) == set(keys)
    assert p._episode_danger_active(ctx(10.1))


def test_successful_water_exit_teaches_the_escape_direction(tmp_path):
    p = profile(tmp_path)
    keys = seed_water_approach(p)
    p._record_water_entry(ctx(10.0))

    p._remember_successful_water_escape(ctx(12.0), direction=-1.0)

    assert p.experience.preferred_escape_direction(keys) == -1.0
    assert p.v22_water_entry_keys == ()
    assert p.v22_water_escape_learning_events == 1
    telemetry = p.telemetry(ctx(12.1))
    assert telemetry["jak_learning_last_water_escape_direction_v22"] == "L"
    assert telemetry["jak_learning_water_entry_keys_pending_v22"] == 0


def test_verified_progress_can_rehabilitate_single_water_penalty(tmp_path):
    p = profile(tmp_path)
    keys = seed_water_approach(p)
    p._record_water_entry(ctx(10.0))
    before = p.experience.score(keys)

    p._record_progress_reward(ctx(11.0), event="test-progress")
    after = p.experience.score(keys)

    assert after < before
    assert after < p.learning_danger_threshold
