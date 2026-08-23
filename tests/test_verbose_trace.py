from ps2_autopilot.observability import read_jsonl
from ps2_autopilot.verbose_trace import VerboseRuntimeTrace


def test_verbose_trace_writes_full_and_spatial_snapshots(tmp_path):
    trace = VerboseRuntimeTrace(
        {
            "enabled": True,
            "verbose_console": False,
            "verbose_log_seconds": 1.0,
            "spatial_log_seconds": 0.5,
            "max_log_bytes": 1_000_000,
        },
        tmp_path,
    )
    state = {
        "phase": "live",
        "game_state": "live_play",
        "possession": "defense",
        "possession_confidence": 0.94,
        "action": "defense: pursue",
        "ocr_text": "1ST AND 10",
        "spatial_enabled": True,
        "spatial_players": 13,
        "spatial_player_candidates": [{"id": 4, "x": 0.1, "y": 0.2, "confidence": 0.8}],
        "spatial_ball_confidence": 0.61,
        "spatial_target_x": 0.2,
        "spatial_target_y": -0.1,
        "spatial_target_confidence": 0.67,
        "spatial_open_x": -0.3,
        "spatial_open_confidence": 0.52,
        "spatial_processing_ms": 4.2,
        "spatial_policy_mode": "defense-target",
        "spatial_policy_reason": "test",
        "spatial_overrides": 5,
    }

    trace.record(11, state, now=10.0)
    trace.record(12, state, now=10.2)
    trace.record(13, state, now=11.1)

    verbose = read_jsonl(tmp_path / "verbose.jsonl")
    spatial = read_jsonl(tmp_path / "spatial.jsonl")
    assert len(verbose) == 2
    assert verbose[-1]["state"]["spatial_players"] == 13
    assert len(spatial) == 2
    assert spatial[-1]["spatial_target_confidence"] == 0.67
    assert spatial[-1]["spatial_policy_mode"] == "defense-target"


def test_verbose_trace_stays_quiet_when_disabled(tmp_path):
    trace = VerboseRuntimeTrace({"enabled": False}, tmp_path)
    trace.record(1, {"spatial_enabled": True}, now=1.0)
    assert not (tmp_path / "verbose.jsonl").exists()
    assert not (tmp_path / "spatial.jsonl").exists()
