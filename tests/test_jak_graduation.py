from __future__ import annotations

import json
from pathlib import Path

from ps2_autopilot.jak_graduation import evaluate_run, evaluate_suite


def write_log(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def completed_row(timestamp: float = 1.0) -> dict:
    return {
        "timestamp": timestamp,
        "jak_policy_version": "v22",
        "jak_goal_cells_delta": 4,
        "jak_goal_flies_delta": 7,
        "jak_goal_completion_percent": 100,
        "jak_objective_stage": "complete",
        "geyser_complete": True,
    }


def test_completed_fresh_autonomous_run_passes(tmp_path):
    path = write_log(tmp_path / "run.jsonl", [completed_row()])

    report = evaluate_run(
        path,
        autonomous_asserted=True,
        fresh_boot_asserted=True,
    )

    assert report["passed"] is True
    assert all(item["passed"] for item in report["criteria"].values())


def test_later_planner_stage_is_diagnostic_not_strict_transaction_proof(tmp_path):
    path = write_log(
        tmp_path / "inferred.jsonl",
        [
            {
                "timestamp": 1.0,
                "jak_goal_cells_delta": 4,
                "jak_goal_flies_delta": 7,
                "jak_goal_completion_percent": 80,
                "jak_objective_stage": "return_warp",
            }
        ],
    )

    report = evaluate_run(
        path,
        autonomous_asserted=True,
        fresh_boot_asserted=True,
    )

    assert report["planner_inference_only"]["blue_eco_door"] is True
    assert report["planner_inference_only"]["cliff_sequence"] is True
    assert report["criteria"]["blue_eco_door"]["passed"] is False
    assert report["criteria"]["cliff_platform_sequence"]["passed"] is False
    assert report["passed"] is False


def test_five_of_five_complete_runs_graduate(tmp_path):
    paths = [
        write_log(tmp_path / f"run-{index}.jsonl", [completed_row(float(index))])
        for index in range(5)
    ]

    report = evaluate_suite(
        paths,
        required_runs=5,
        autonomous_asserted=True,
        fresh_boots_asserted=True,
    )

    assert report["graduated"] is True
    assert report["passed_runs"] == 5
    assert report["total_runs"] == 5


def test_complete_logs_do_not_graduate_without_run_provenance(tmp_path):
    paths = [
        write_log(tmp_path / f"run-{index}.jsonl", [completed_row(float(index))])
        for index in range(5)
    ]

    report = evaluate_suite(paths, required_runs=5)

    assert report["graduated"] is False
    assert report["passed_runs"] == 0
    assert report["runs"][0]["criteria"]["no_human_intervention"]["passed"] is False
    assert report["runs"][0]["criteria"]["fresh_boot_new_save"]["passed"] is False


def test_missing_collectible_requirement_blocks_graduation(tmp_path):
    row = completed_row()
    row["jak_goal_flies_delta"] = 6
    path = write_log(tmp_path / "missing-fly.jsonl", [row])

    report = evaluate_run(
        path,
        autonomous_asserted=True,
        fresh_boot_asserted=True,
    )

    assert report["criteria"]["seven_scout_flies"]["passed"] is False
    assert report["passed"] is False
