from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from ps2_autopilot.madden_policy_report import build_policy_report, session_policy_metrics


def test_session_attribution_uses_peak_counters_not_row_sum(tmp_path: Path):
    states = [
        {"game_event_attribution_counts": {"sack_caused": 1, "interception_made": 0}},
        {"game_event_attribution_counts": {"sack_caused": 2, "interception_made": 1}},
        {"game_event_attribution_counts": {"sack_caused": 2, "interception_made": 1}},
    ]
    metrics = session_policy_metrics(tmp_path / "unused", states)
    assert metrics["attributed_events"] == {
        "sack_caused": 2,
        "interception_made": 1,
    }


def _write_session(root: Path, attributed: list[dict[str, int]], *, minute: int) -> None:
    start = datetime(2026, 8, 24, 22, minute, tzinfo=timezone.utc)
    rows: list[dict] = []
    for index, counts in enumerate(attributed):
        rows.append(
            {
                "utc": (start + timedelta(seconds=index)).isoformat(),
                "kind": "verbose",
                "decision_id": index + 1,
                "state": {
                    "profile": "madden2005",
                    "game_display_name": "Madden NFL 2005",
                    "madden_policy_version": "v24",
                    "phase": "live",
                    "possession": "offense",
                    "timestamp": start.timestamp() + index,
                    "last_loop_ms": 40.0,
                    "loop_budget_ms": 83.3,
                    "capture_ms": 15.0,
                    "policy_ms": 8.0,
                    "action": "normal play",
                    "session_games_started": 1,
                    "session_games_completed": 1 if index == len(attributed) - 1 else 0,
                    "session_unknown_captures": 0,
                    "session_progress_recoveries": 0,
                    "recoveries": 0,
                    "game_event_counts": {},
                    "game_event_attribution_counts": counts,
                },
            }
        )
    root.mkdir(parents=True, exist_ok=True)
    (root / "verbose.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_multi_session_report_sums_owned_event_peaks_and_unknowns(tmp_path: Path):
    _write_session(
        tmp_path / "a",
        [
            {"touchdown_for": 1, "interception_thrown": 1},
            {
                "touchdown_for": 2,
                "interception_thrown": 1,
                "sack_suffered": 2,
                "first_down_gained": 3,
                "sack_ownership_unknown": 1,
            },
        ],
        minute=0,
    )
    _write_session(
        tmp_path / "b",
        [
            {"field_goal_against": 1, "interception_made": 1},
            {
                "field_goal_against": 1,
                "interception_made": 2,
                "sack_caused": 2,
                "first_down_allowed": 4,
                "touchdown_ownership_unknown": 2,
            },
        ],
        minute=5,
    )

    report = build_policy_report([tmp_path])
    football = report["football"]
    assert football["scoring_events_for"] == 2
    assert football["scoring_events_against"] == 1
    assert football["interceptions_made"] == 2
    assert football["interceptions_thrown"] == 1
    assert football["sacks_caused"] == 2
    assert football["sacks_suffered"] == 2
    assert football["first_downs_gained"] == 3
    assert football["first_downs_allowed"] == 4
    assert football["ownership_unknown_events"] == 3
    assert football["attributed_events"]["sack_caused"] == 2
    assert football["attributed_events"]["touchdown_for"] == 2


def test_historical_traces_without_attribution_remain_backward_compatible(tmp_path: Path):
    metrics = session_policy_metrics(
        tmp_path / "unused",
        [{"phase": "live", "game_event_counts": {"sack": 2}}],
    )
    assert metrics["events"]["sack"] == 2
    assert metrics["attributed_events"] == {}
