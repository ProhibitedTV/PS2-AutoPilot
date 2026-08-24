from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from ps2_autopilot.soak_report import (
    analyze_session,
    build_report,
    evaluate_acceptance,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _rows(game: str, profile: str, states: list[dict]) -> list[dict]:
    started = datetime(2026, 8, 24, 18, 0, tzinfo=timezone.utc)
    result = []
    for index, state in enumerate(states):
        payload = {
            "game_display_name": game,
            "profile": profile,
            "timestamp": started.timestamp() + index,
            "last_loop_ms": 70.0 + index * 10.0,
            "loop_budget_ms": 83.3,
            "capture_ms": 30.0 + index,
            "policy_ms": 20.0 + index * 2,
            "ocr_result_age_ms": 110.0 + index * 20.0,
            "ocr_completion_age_ms": 50.0 + index * 10.0,
            **state,
        }
        result.append(
            {
                "utc": (started + timedelta(seconds=index)).isoformat(),
                "kind": "verbose",
                "decision_id": index + 1,
                "state": payload,
            }
        )
    return result


def test_session_report_counts_recoveries_latency_and_unresolved_time(tmp_path: Path) -> None:
    root = tmp_path / "madden"
    rows = _rows(
        "Madden NFL 2005",
        "madden2005",
        [
            {
                "session_games_started": 0,
                "session_games_completed": 0,
                "recoveries": 0,
                "session_progress_recoveries": 0,
                "session_unknown_captures": 0,
                "ocr_dropped_frames": 0,
                "action": "pre-snap",
            },
            {
                "session_games_started": 1,
                "session_games_completed": 0,
                "recoveries": 1,
                "session_progress_recoveries": 0,
                "session_unknown_captures": 1,
                "ocr_dropped_frames": 2,
                "action": "semantic progress recovery",
                "progress_recovery_reason": "menu-stall",
            },
            {
                "session_games_started": 2,
                "session_games_completed": 1,
                "recoveries": 0,
                "session_progress_recoveries": 1,
                "session_unknown_captures": 1,
                "ocr_dropped_frames": 3,
                "action": "live play",
            },
            {
                "session_games_started": 2,
                "session_games_completed": 1,
                "recoveries": 1,
                "session_progress_recoveries": 1,
                "session_unknown_captures": 2,
                "ocr_dropped_frames": 4,
                "action": "visual loop/stagnation -> relocate",
            },
        ],
    )
    _write_jsonl(root / "verbose.jsonl", rows)
    _write_jsonl(
        root / "events.jsonl",
        [
            {"kind": "failure_bundle", "reason": "menu-stall"},
            {"kind": "decision", "action": "ok"},
        ],
    )

    report = analyze_session(root)
    assert report.game == "Madden NFL 2005"
    assert report.games_started == 2
    assert report.games_completed == 1
    # Counter reset 1 -> 0 -> 1 still represents two hard recoveries.
    assert report.hard_recoveries == 2
    assert report.semantic_recoveries == 1
    assert report.unknown_captures == 2
    assert report.ocr_dropped_frames == 4
    assert report.failure_bundles == 1
    assert report.duration_seconds == 4.0
    assert report.unresolved_seconds == 2.0
    assert report.unresolved_reasons["progress-recovery"] == 1
    assert report.unresolved_reasons["stagnation"] == 1
    assert report.loop_samples == 4
    assert report.loop_overruns == 2
    assert report.ocr_result_age_ms == [110.0, 130.0, 150.0, 170.0]


def test_build_report_recursively_aggregates_multiple_games_and_rotated_logs(tmp_path: Path) -> None:
    madden = tmp_path / "runs" / "madden-001"
    jak = tmp_path / "runs" / "jak-001"

    _write_jsonl(
        madden / "verbose.jsonl.1",
        _rows(
            "Madden NFL 2005",
            "madden2005",
            [{"session_games_started": 1, "session_games_completed": 0, "action": "live"}],
        ),
    )
    _write_jsonl(
        madden / "verbose.jsonl",
        _rows(
            "Madden NFL 2005",
            "madden2005",
            [{"session_games_started": 1, "session_games_completed": 1, "action": "final"}],
        ),
    )
    _write_jsonl(
        jak / "verbose.jsonl",
        _rows(
            "Jak and Daxter: The Precursor Legacy",
            "jak_and_daxter",
            [
                {"action": "safe traversal"},
                {"action": "jak: water escape toward shore", "water_escape_active": True},
            ],
        ),
    )

    report = build_report([tmp_path / "runs"])
    assert report["schema"] == "ps2-autopilot-soak-report-v1"
    assert report["session_count"] == 2
    assert report["game_count"] == 2
    assert set(report["games"]) == {
        "Jak and Daxter: The Precursor Legacy",
        "Madden NFL 2005",
    }
    assert report["overall"]["games_started"] == 1
    assert report["overall"]["games_completed"] == 1
    assert report["games"]["Jak and Daxter: The Precursor Legacy"][
        "unresolved_navigation_seconds"
    ] > 0.0


def test_acceptance_thresholds_are_machine_actionable(tmp_path: Path) -> None:
    root = tmp_path / "run"
    _write_jsonl(
        root / "verbose.jsonl",
        _rows(
            "Madden NFL 2005",
            "madden2005",
            [
                {
                    "session_games_started": 1,
                    "session_games_completed": 0,
                    "session_unknown_captures": 2,
                    "recoveries": 1,
                    "action": "unknown; fail closed / hold inputs",
                    "phase": "unknown",
                },
                {
                    "session_games_started": 1,
                    "session_games_completed": 0,
                    "session_unknown_captures": 2,
                    "recoveries": 1,
                    "action": "unknown; fail closed / hold inputs",
                    "phase": "unknown",
                },
            ],
        ),
    )
    report = build_report([root])
    failures = evaluate_acceptance(
        report,
        min_games_completed=1,
        max_unresolved_pct=20.0,
        max_unknown_captures=0,
        max_hard_recoveries=0,
    )
    assert any("games_completed" in failure for failure in failures)
    assert any("unresolved_navigation_pct" in failure for failure in failures)
    assert any("unknown_captures" in failure for failure in failures)
    assert any("hard_recoveries" in failure for failure in failures)
