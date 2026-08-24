from ps2_autopilot.jak_skill_engine import AtomicSkillEngine


def test_skill_engine_tracks_phases_retries_and_success_latency():
    engine = AtomicSkillEngine()
    item = engine.start(
        "double_jump",
        now=10.0,
        phase="align",
        phase_seconds=0.1,
        timeout_seconds=2.0,
        heading=0.2,
        forward=0.7,
        start_position=(1.0, 2.0, 3.0),
        payload={"reason": "test"},
    )
    assert engine.is_active
    assert item.phase == "align"
    assert not engine.phase_done(10.05)
    assert engine.phase_done(10.10)

    engine.transition("commit", now=10.10, seconds=0.2)
    assert engine.active is not None
    assert engine.active.phase == "commit"
    engine.retry(now=10.30, phase="recover", seconds=0.1)
    assert engine.active.retries == 1

    finished = engine.finish(now=10.75, success=True, result="xyz:0.42")
    assert finished is not None
    assert finished.name == "double_jump"
    assert not engine.is_active

    metrics = engine.telemetry(11.0)
    assert metrics["jak_skill_double_jump_attempts"] == 1
    assert metrics["jak_skill_double_jump_successes"] == 1
    assert metrics["jak_skill_double_jump_failures"] == 0
    assert metrics["jak_skill_double_jump_retries"] == 1
    assert metrics["jak_skill_double_jump_last_result"] == "xyz:0.42"
    assert metrics["jak_atomic_skill_active"] is False


def test_skill_engine_safety_abort_is_not_counted_as_skill_failure():
    engine = AtomicSkillEngine()
    engine.start("roll_jump", now=2.0, timeout_seconds=2.0)
    aborted = engine.abort(now=2.4, reason="water")
    assert aborted is not None

    metrics = engine.telemetry(2.5)
    assert metrics["jak_skill_roll_jump_attempts"] == 1
    assert metrics["jak_skill_roll_jump_failures"] == 0
    assert metrics["jak_skill_roll_jump_safety_aborts"] == 1
    assert metrics["jak_skill_roll_jump_last_result"] == "abort:water"


def test_skill_engine_timeout_is_query_only_until_profile_finishes_transaction():
    engine = AtomicSkillEngine()
    engine.start("jump", now=5.0, timeout_seconds=1.0)
    assert not engine.timed_out(5.99)
    assert engine.timed_out(6.0)
    assert engine.is_active
    engine.finish(now=6.0, success=False, result="timeout")
    assert engine.telemetry(6.0)["jak_skill_jump_failures"] == 1
