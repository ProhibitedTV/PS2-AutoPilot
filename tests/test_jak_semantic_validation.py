from __future__ import annotations

import json
from pathlib import Path

from ps2_autopilot.jak_semantic_validation import validate


def write_verbose(path: Path, states: list[dict]) -> Path:
    rows = [
        {
            "utc": f"2026-08-24T20:00:{index:02d}.000+00:00",
            "kind": "verbose",
            "decision_id": index,
            "state": state,
        }
        for index, state in enumerate(states)
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def semantic_state(index: int, *, grounded: bool | None = None) -> dict:
    state = {
        "pine_available": True,
        "pine_verified": True,
        "pine_stale": False,
        "pine_schema_verified": True,
        "pine_game_id": "SCUS-97124",
        "pine_game_crc": "1b3976ab",
        "jak_x": index * 0.20,
        "jak_y": 0.0,
        "jak_z": index * 0.10,
        "jak_vx": 0.20 if index else 0.0,
        "jak_vy": 0.0,
        "jak_vz": 0.10 if index else 0.0,
        "power_cells": 1 if index >= 4 else 0,
        "precursor_orbs": index,
        "scout_flies": 1 if index >= 3 else 0,
    }
    if grounded is not None:
        state["jak_grounded"] = grounded
    return state


def test_strict_semantic_validation_passes_with_contact_transition(tmp_path):
    states = [
        semantic_state(index, grounded=index not in {2, 3})
        for index in range(6)
    ]
    path = write_verbose(tmp_path / "verbose.jsonl", states)

    report = validate(
        path,
        min_verified_samples=5,
        expected_game_id="SCUS-97124",
        expected_crc="1b3976ab",
    )

    assert report["core_passed"] is True
    assert report["passed"] is True
    assert report["criteria"]["xyz_motion"]["passed"] is True
    assert report["criteria"]["velocity_motion"]["passed"] is True
    assert report["criteria"]["contact_transition"]["passed"] is True


def test_core_can_pass_while_contact_calibration_remains_open(tmp_path):
    states = [semantic_state(index) for index in range(6)]
    path = write_verbose(tmp_path / "verbose.jsonl", states)

    report = validate(path, min_verified_samples=5)

    assert report["core_passed"] is True
    assert report["passed"] is False
    assert report["criteria"]["contact_field"]["passed"] is False
    assert report["criteria"]["contact_transition"]["passed"] is False


def test_stale_and_unverified_rows_do_not_count_as_trusted(tmp_path):
    states = [semantic_state(index, grounded=True) for index in range(6)]
    states[0]["pine_stale"] = True
    states[1]["pine_verified"] = False
    path = write_verbose(tmp_path / "verbose.jsonl", states)

    report = validate(path, min_verified_samples=5)

    assert report["trusted_rows"] == 4
    assert report["criteria"]["verified_samples"]["passed"] is False
    assert report["rejected_rows"]["pine-stale"] == 1
    assert report["rejected_rows"]["identity-unverified"] == 1


def test_counter_regression_fails_core_validation(tmp_path):
    states = [
        semantic_state(index, grounded=index % 2 == 0)
        for index in range(6)
    ]
    states[5]["precursor_orbs"] = 1
    path = write_verbose(tmp_path / "verbose.jsonl", states)

    report = validate(path, min_verified_samples=5)

    assert report["criteria"]["progression_counters"]["passed"] is False
    assert report["criteria"]["progression_counters"]["counters"]["precursor_orbs"]["monotonic"] is False
    assert report["core_passed"] is False


def test_identity_expectation_mismatch_is_explicit(tmp_path):
    states = [
        semantic_state(index, grounded=index % 2 == 0)
        for index in range(6)
    ]
    path = write_verbose(tmp_path / "verbose.jsonl", states)

    report = validate(path, expected_crc="deadbeef")

    assert report["criteria"]["stable_identity"]["passed"] is False
    assert report["passed"] is False
