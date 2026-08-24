from __future__ import annotations

import json
from pathlib import Path

from ps2_autopilot.jak_benchmark import _records, summarize
from ps2_autopilot.jak_graduation import evaluate_run


def write_rows(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def verbose_row(utc: str, state: dict, decision_id: int = 1) -> dict:
    return {
        "utc": utc,
        "kind": "verbose",
        "decision_id": decision_id,
        "state": state,
    }


def test_records_unwrap_normal_verbose_runtime_envelope(tmp_path):
    path = write_rows(
        tmp_path / "verbose.jsonl",
        [
            verbose_row(
                "2026-08-24T20:00:00.000+00:00",
                {
                    "action": "jak: move",
                    "jak_policy_version": "v22",
                    "jak_goal_cells_delta": 2,
                },
            )
        ],
    )

    rows = list(_records(path))

    assert len(rows) == 1
    assert rows[0]["action"] == "jak: move"
    assert rows[0]["jak_policy_version"] == "v22"
    assert rows[0]["jak_goal_cells_delta"] == 2
    assert rows[0]["decision_id"] == 1
    assert isinstance(rows[0]["timestamp"], float)


def test_summarize_reads_nested_verbose_state_and_utc_duration(tmp_path):
    path = write_rows(
        tmp_path / "verbose.jsonl",
        [
            verbose_row(
                "2026-08-24T20:00:00.000+00:00",
                {
                    "action": "jak: first",
                    "jak_policy_version": "v22",
                    "jak_objective_stage": "first_cell",
                    "jak_goal_cells_delta": 1,
                    "jak_goal_flies_delta": 3,
                },
                1,
            ),
            verbose_row(
                "2026-08-24T20:00:05.000+00:00",
                {
                    "action": "jak: second",
                    "jak_policy_version": "v22",
                    "jak_objective_stage": "scout_flies",
                    "jak_goal_cells_delta": 2,
                    "jak_goal_flies_delta": 5,
                    "jak_goal_progress_events": 2,
                },
                2,
            ),
        ],
    )

    report = summarize(path)

    assert report["samples"] == 2
    assert report["duration_seconds"] == 5.0
    assert report["max_geyser_cells"] == 2
    assert report["max_geyser_scout_flies"] == 5
    assert report["objective_progress_events"] == 2
    assert report["policy_samples"] == [("v22", 2)]


def test_graduation_evaluator_accepts_real_verbose_envelope(tmp_path):
    path = write_rows(
        tmp_path / "graduated.jsonl",
        [
            verbose_row(
                "2026-08-24T20:00:00.000+00:00",
                {
                    "jak_policy_version": "v22",
                    "jak_goal_cells_delta": 4,
                    "jak_goal_flies_delta": 7,
                    "jak_goal_completion_percent": 100,
                    "jak_objective_stage": "complete",
                    "geyser_complete": True,
                },
            )
        ],
    )

    report = evaluate_run(
        path,
        autonomous_asserted=True,
        fresh_boot_asserted=True,
    )

    assert report["passed"] is True


def test_flat_fixture_format_remains_supported(tmp_path):
    path = write_rows(
        tmp_path / "flat.jsonl",
        [
            {
                "timestamp": 1.0,
                "jak_policy_version": "v22",
                "jak_goal_cells_delta": 1,
            }
        ],
    )

    report = summarize(path)

    assert report["samples"] == 1
    assert report["max_geyser_cells"] == 1
