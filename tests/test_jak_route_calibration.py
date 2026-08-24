from __future__ import annotations

import json
from pathlib import Path

import pytest

from ps2_autopilot.jak_route_calibration import (
    EDGE_TYPES,
    REQUIRED_GEYSER_NODES,
    RouteCalibrationError,
    add_edge,
    capture_node,
    check_manifest,
    new_manifest,
    validate_edge,
)


def _trace(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _state(x: float, y: float, z: float, *, timestamp: float) -> dict[str, object]:
    return {
        "utc": "2026-08-24T20:00:00Z",
        "state": {
            "timestamp": timestamp,
            "pine_schema_verified": True,
            "pine_semantic_schema": "jak1-goal-symbols-v1",
            "pine_target_root_offset": 120,
            "jak_x": x,
            "jak_y": y,
            "jak_z": z,
        },
    }


def test_template_names_required_landmarks_without_inventing_coordinates() -> None:
    manifest = new_manifest()
    assert set(manifest["nodes"]) == set(REQUIRED_GEYSER_NODES)
    assert set(manifest["edge_types"]) == set(EDGE_TYPES)
    assert all(node["xyz"] is None for node in manifest["nodes"].values())
    report = check_manifest(manifest)
    assert report["ready"] is False
    assert set(report["uncalibrated_nodes"]) == set(REQUIRED_GEYSER_NODES)
    assert report["edge_count"] == 0


def test_capture_node_requires_verified_semantic_xyz_and_uses_latest_sample(tmp_path: Path) -> None:
    trace = _trace(
        tmp_path / "verbose.jsonl",
        [
            {
                "state": {
                    "timestamp": 1.0,
                    "pine_schema_verified": False,
                    "pine_semantic_schema": "jak1-goal-symbols-v1",
                    "jak_x": 999.0,
                    "jak_y": 999.0,
                    "jak_z": 999.0,
                }
            },
            _state(1.0, 2.0, 3.0, timestamp=2.0),
            _state(4.0, 5.0, 6.0, timestamp=3.0),
        ],
    )
    manifest = capture_node(new_manifest(), "warp_start", trace)
    assert manifest["nodes"]["warp_start"]["xyz"] == [4.0, 5.0, 6.0]
    assert manifest["nodes"]["warp_start"]["evidence"]["timestamp"] == 3.0
    assert new_manifest()["nodes"]["warp_start"]["xyz"] is None


def test_capture_node_rejects_wrong_or_unverified_schema(tmp_path: Path) -> None:
    trace = _trace(
        tmp_path / "bad.jsonl",
        [
            {
                "state": {
                    "pine_schema_verified": True,
                    "pine_semantic_schema": "guessed-addresses",
                    "jak_x": 1.0,
                    "jak_y": 2.0,
                    "jak_z": 3.0,
                }
            }
        ],
    )
    with pytest.raises(RouteCalibrationError, match="contains no verified"):
        capture_node(new_manifest(), "warp_start", trace)


def test_add_edge_uses_closed_traversal_vocabulary_and_starts_unvalidated() -> None:
    manifest = add_edge(new_manifest(), "warp_start", "first_cell_path", "walk")
    assert manifest["edges"] == [
        {
            "from": "warp_start",
            "to": "first_cell_path",
            "type": "WALK",
            "bidirectional": False,
            "validated": False,
            "evidence": None,
        }
    ]
    with pytest.raises(RouteCalibrationError, match="unsupported edge type"):
        add_edge(new_manifest(), "warp_start", "first_cell_path", "TELEPORT")


def test_validate_edge_requires_ordered_source_then_target_semantic_evidence(tmp_path: Path) -> None:
    manifest = new_manifest()
    manifest["nodes"]["warp_start"]["xyz"] = [0.0, 0.0, 0.0]
    manifest["nodes"]["first_cell_path"]["xyz"] = [10.0, 0.0, 0.0]
    manifest = add_edge(manifest, "warp_start", "first_cell_path", "WALK")

    trace = _trace(
        tmp_path / "edge.jsonl",
        [
            _state(-0.4, 0.0, 0.0, timestamp=1.0),
            _state(5.0, 0.0, 0.0, timestamp=2.0),
            _state(9.6, 0.0, 0.0, timestamp=3.0),
        ],
    )
    validated = validate_edge(manifest, 0, trace, radius_m=1.0)
    assert validated["edges"][0]["validated"] is True
    assert validated["edges"][0]["evidence"]["source_sample"] == 0
    assert validated["edges"][0]["evidence"]["target_sample"] == 2

    reversed_trace = _trace(
        tmp_path / "reversed.jsonl",
        [
            _state(10.0, 0.0, 0.0, timestamp=1.0),
            _state(0.0, 0.0, 0.0, timestamp=2.0),
        ],
    )
    with pytest.raises(RouteCalibrationError, match="did not prove ordered traversal"):
        validate_edge(manifest, 0, reversed_trace, radius_m=1.0)


def test_check_manifest_never_calls_partial_calibration_ready() -> None:
    manifest = new_manifest()
    for node in manifest["nodes"].values():
        node["xyz"] = [1.0, 2.0, 3.0]
    manifest = add_edge(manifest, "warp_start", "first_cell_path", "WALK")
    report = check_manifest(manifest)
    assert report["uncalibrated_nodes"] == []
    assert report["unvalidated_edges"] == [0]
    assert report["ready"] is False

    manifest["edges"][0]["validated"] = True
    report = check_manifest(manifest)
    assert report["ready"] is True
