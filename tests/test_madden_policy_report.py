from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from ps2_autopilot.madden_policy_report import build_policy_report, session_policy_metrics


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _rows(game: str, profile: str, states: list[dict], *, start_minute: int = 0) -> list[dict]:
    started = datetime(2026, 8, 24, 20, start_minute, tzinfo=timezone.utc)
    rows: list[dict] = []
    for index, state in enumerate(states):
        payload = {
            "game_display_name": game,
            "profile": profile,
            "timestamp": started.timestamp() + index,
            "last_loop_ms": 50.0,
            "loop_budget_ms": 83.3,
            "capture_ms": 20.0,
            "policy_ms": 10.0,
            "session_games_started": 1,
            "session_games_completed": 0,
            "session_unknown_captures": 0,
            "session_progress_recoveries": 0,
            "recoveries": 0,
            "action": "normal play",
            **state,
        }
        rows.append(
            {
                "utc": (started + timedelta(seconds=index)).isoformat(),
                "kind": "verbose",
                "decision_id": index + 1,
                "state": payload,
            }
        )
    return rows


def test_session_metrics_take_counter_peaks_not_sum_cumulative_rows(tmp_path: Path):
    root = tmp_path / "run"
    states = [
        {
            "phase": "live",
            "plays_started": 1,
            "plays_completed": 0,
            "pass_attempts": 0,
            "game_event_counts": {"touchdown": 0},
        },
        {
            "phase": "post_play",
            "plays_started": 1,
            "plays_completed": 1,
            "pass_attempts": 1,
            "game_event_counts": {"touchdown": 1},
        },
        {
            "phase": "live",
            "plays_started": 2,
            "plays_completed": 1,
            "pass_attempts": 1,
            "game_event_counts": {"touchdown": 1},
        },
    ]

    metrics = session_policy_metrics(root, states)
    assert metrics["counters"]["plays_started"] == 2
    assert metrics["counters"]["plays_completed"] == 1
    assert metrics["counters"]["pass_attempts"] == 1
    assert metrics["events"]["touchdown"] == 1


def test_report_aggregates_sessions_and_ignores_non_madden_runs(tmp_path: Path):
    first = tmp_path / "runs" / "madden-a"
    second = tmp_path / "runs" / "madden-b"
    jak = tmp_path / "runs" / "jak"

    _write_jsonl(
        first / "verbose.jsonl",
        _rows(
            "Madden NFL 2005",
            "madden2005",
            [
                {
                    "phase": "live",
                    "possession": "unknown",
                    "madden_policy_version": "v24",
                    "plays_started": 0,
                    "plays_completed": 0,
                    "spatial_enabled": False,
                    "spatial_controlled_confidence": 0.2,
                    "spatial_target_confidence": 0.2,
                    "spatial_ball_confidence": 0.1,
                    "spatial_open_confidence": 0.2,
                    "game_event_counts": {},
                },
                {
                    "phase": "live",
                    "possession": "offense",
                    "madden_policy_version": "v24",
                    "plays_started": 1,
                    "plays_completed": 0,
                    "pass_attempts": 1,
                    "spatial_enabled": True,
                    "spatial_controlled_confidence": 0.7,
                    "spatial_target_confidence": 0.8,
                    "spatial_ball_confidence": 0.6,
                    "spatial_open_confidence": 0.9,
                    "special_teams_recognitions": 1,
                    "special_teams_handoffs": 0,
                    "game_event_counts": {"touchdown": 1, "first_down": 2},
                },
                {
                    "phase": "post_play",
                    "possession": "offense",
                    "madden_policy_version": "v24",
                    "plays_started": 1,
                    "plays_completed": 1,
                    "pass_attempts": 1,
                    "special_teams_recognitions": 1,
                    "special_teams_handoffs": 1,
                    "game_event_counts": {"touchdown": 1, "first_down": 2},
                    "session_games_completed": 1,
                },
            ],
        ),
    )

    _write_jsonl(
        second / "verbose.jsonl",
        _rows(
            "Madden NFL 2005",
            "madden2005",
            [
                {
                    "phase": "live",
                    "possession": "defense",
                    "madden_policy_version": "v24",
                    "plays_started": 1,
                    "plays_completed": 0,
                    "tackle_attempts": 1,
                    "defense_uncertain_ticks": 2,
                    "defense_contact_suppressed_ticks": 2,
                    "spatial_enabled": True,
                    "spatial_controlled_confidence": 0.8,
                    "spatial_target_confidence": 0.7,
                    "spatial_ball_confidence": 0.0,
                    "spatial_open_confidence": 0.0,
                    "game_event_counts": {"sack": 1},
                },
                {
                    "phase": "live",
                    "possession": "defense",
                    "madden_policy_version": "v24",
                    "plays_started": 2,
                    "plays_completed": 1,
                    "tackle_attempts": 1,
                    "defense_uncertain_ticks": 2,
                    "defense_contact_authorized_ticks": 2,
                    "defense_contact_suppressed_ticks": 2,
                    "special_teams_recognitions": 2,
                    "special_teams_handoffs": 2,
                    "special_return_sprints": 1,
                    "spatial_enabled": True,
                    "spatial_controlled_confidence": 0.9,
                    "spatial_target_confidence": 0.9,
                    "spatial_ball_confidence": 0.7,
                    "spatial_open_confidence": 0.6,
                    "game_event_counts": {"sack": 2, "interception": 1},
                    "session_games_completed": 1,
                },
            ],
            start_minute=5,
        ),
    )

    _write_jsonl(
        jak / "verbose.jsonl",
        _rows(
            "Jak and Daxter: The Precursor Legacy",
            "jak_and_daxter",
            [{"phase": "gameplay", "plays_started": 99, "plays_completed": 99}],
            start_minute=10,
        ),
    )

    report = build_policy_report([tmp_path / "runs"])
    assert report["session_count"] == 2
    assert report["policy_versions"] == ["v24"]
    assert report["football"]["plays_started"] == 3
    assert report["football"]["plays_completed"] == 2
    assert report["football"]["play_completion_pct"] == pytest.approx(66.667)
    assert report["football"]["pass_attempts"] == 1
    assert report["football"]["tackle_attempts"] == 1
    assert report["football"]["events"]["touchdown"] == 1
    assert report["football"]["events"]["first_down"] == 2
    assert report["football"]["events"]["sack"] == 2
    assert report["football"]["events"]["interception"] == 1
    assert report["football"]["scoring_events"] == 1
    assert report["football"]["turnover_events"] == 1

    assert report["live_samples"] == 4
    assert report["live_quality"]["unknown_possession_pct"] == 25.0
    assert report["live_quality"]["spatial_available_pct"] == 75.0
    assert report["live_quality"]["controlled_confidence_ge_050_pct"] == 75.0
    assert report["live_quality"]["target_confidence_ge_050_pct"] == 75.0
    assert report["live_quality"]["ball_confidence_ge_050_pct"] == 50.0
    assert report["live_quality"]["open_space_confidence_ge_050_pct"] == 50.0

    assert report["special_teams"]["recognitions"] == 3
    assert report["special_teams"]["handoffs"] == 3
    assert report["special_teams"]["return_sprints"] == 1
    assert report["defense"]["uncertain_ticks"] == 2
    assert report["defense"]["contact_authorized_ticks"] == 2
    assert report["defense"]["contact_suppressed_ticks"] == 2
    assert report["runtime"]["games_completed"] == 2


def test_no_madden_evidence_fails_loudly(tmp_path: Path):
    root = tmp_path / "jak"
    _write_jsonl(
        root / "verbose.jsonl",
        _rows(
            "Jak and Daxter: The Precursor Legacy",
            "jak_and_daxter",
            [{"phase": "gameplay"}],
        ),
    )
    with pytest.raises(ValueError, match="no retained Madden sessions"):
        build_policy_report([tmp_path])
