from ps2_autopilot.loop_health import RollingLoopHealth


def test_rolling_loop_health_reports_recent_percentiles_and_pressure():
    health = RollingLoopHealth(window=20)
    for _ in range(12):
        health.record(capture_ms=10.0, policy_ms=40.0, loop_ms=70.0, budget_ms=83.33)
    snap = health.snapshot(83.33)
    assert snap.samples == 12
    assert snap.loop_p50_ms == 70.0
    assert snap.loop_p95_ms == 70.0
    assert snap.overrun_ratio == 0.0
    assert snap.pressure == "healthy"


def test_rolling_loop_health_marks_sustained_overruns_critical():
    health = RollingLoopHealth(window=20)
    for _ in range(12):
        health.record(capture_ms=20.0, policy_ms=180.0, loop_ms=220.0, budget_ms=83.33)
    snap = health.snapshot(83.33)
    assert snap.overrun_ratio == 1.0
    assert snap.policy_p95_ms == 180.0
    assert snap.pressure == "critical"


def test_window_discards_old_pressure():
    health = RollingLoopHealth(window=12)
    for _ in range(12):
        health.record(capture_ms=10.0, policy_ms=160.0, loop_ms=200.0, budget_ms=83.33)
    for _ in range(12):
        health.record(capture_ms=8.0, policy_ms=30.0, loop_ms=55.0, budget_ms=83.33)
    snap = health.snapshot(83.33)
    assert snap.samples == 12
    assert snap.overrun_ratio == 0.0
    assert snap.pressure == "healthy"
