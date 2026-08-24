from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
from typing import Any, Iterable

from ps2_autopilot.jak_benchmark import _records


SCHEMA = "ps2-autopilot-jak-route-v1"
SEMANTIC_SCHEMA = "jak1-goal-symbols-v1"
EDGE_TYPES = (
    "WALK",
    "GAP_JUMP",
    "ROLL_JUMP",
    "DIVE",
    "ECO_RUN",
    "PLATFORM_CHAIN",
    "SWIM_ESCAPE",
)
REQUIRED_GEYSER_NODES = {
    "warp_start": "Warp/start",
    "first_cell_path": "First-cell path",
    "scout_fly_cluster": "Scout Fly cluster",
    "blue_eco_vent": "Blue Eco vent",
    "precursor_door": "Precursor door",
    "pond_shore": "Pond/shore",
    "cliff_platform_chain": "Cliff/platform chain",
    "upper_cell": "Upper cell",
    "return_route": "Return route",
}


class RouteCalibrationError(RuntimeError):
    pass


def new_manifest() -> dict[str, Any]:
    """Return a deliberately uncalibrated Geyser Rock route manifest.

    The required semantic landmarks and traversal vocabulary are known ahead of a
    live run, but their coordinates are not. Keeping `xyz` null prevents an offline
    engineering pass from accidentally turning guessed coordinates into production
    navigation data.
    """

    return {
        "schema": SCHEMA,
        "game": "Jak and Daxter: The Precursor Legacy",
        "area": "Geyser Rock",
        "semantic_schema": SEMANTIC_SCHEMA,
        "edge_types": list(EDGE_TYPES),
        "nodes": {
            node_id: {
                "label": label,
                "xyz": None,
                "evidence": None,
            }
            for node_id, label in REQUIRED_GEYSER_NODES.items()
        },
        "edges": [],
    }


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RouteCalibrationError(f"could not read route manifest {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RouteCalibrationError("route manifest root must be an object")
    if value.get("schema") != SCHEMA:
        raise RouteCalibrationError(
            f"unsupported route manifest schema {value.get('schema')!r}; expected {SCHEMA!r}"
        )
    return value


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _finite_xyz(row: dict[str, Any]) -> tuple[float, float, float] | None:
    if row.get("pine_schema_verified") is not True:
        return None
    if row.get("pine_semantic_schema") != SEMANTIC_SCHEMA:
        return None
    values: list[float] = []
    for key in ("jak_x", "jak_y", "jak_z"):
        try:
            value = float(row[key])
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(value) or abs(value) > 100_000.0:
            return None
        values.append(value)
    return values[0], values[1], values[2]


def _verified_samples(path: Path) -> Iterable[tuple[dict[str, Any], tuple[float, float, float]]]:
    for row in _records(path):
        xyz = _finite_xyz(row)
        if xyz is not None:
            yield row, xyz


def capture_node(manifest: dict[str, Any], node_id: str, trace_path: Path) -> dict[str, Any]:
    nodes = manifest.get("nodes")
    if not isinstance(nodes, dict) or node_id not in nodes:
        raise RouteCalibrationError(f"unknown route node {node_id!r}")

    chosen: tuple[dict[str, Any], tuple[float, float, float]] | None = None
    for chosen in _verified_samples(trace_path):
        pass
    if chosen is None:
        raise RouteCalibrationError(
            f"{trace_path} contains no verified {SEMANTIC_SCHEMA} XYZ samples"
        )

    row, xyz = chosen
    result = deepcopy(manifest)
    result["nodes"][node_id]["xyz"] = [round(value, 6) for value in xyz]
    result["nodes"][node_id]["evidence"] = {
        "trace": str(trace_path),
        "timestamp": row.get("timestamp"),
        "utc": row.get("utc"),
        "target_root_offset": row.get("pine_target_root_offset"),
    }
    return result


def add_edge(
    manifest: dict[str, Any],
    source: str,
    target: str,
    edge_type: str,
    *,
    bidirectional: bool = False,
) -> dict[str, Any]:
    nodes = manifest.get("nodes")
    if not isinstance(nodes, dict) or source not in nodes or target not in nodes:
        raise RouteCalibrationError("edge endpoints must name nodes already present in the manifest")
    edge_type = edge_type.upper()
    if edge_type not in EDGE_TYPES:
        raise RouteCalibrationError(
            f"unsupported edge type {edge_type!r}; expected one of {', '.join(EDGE_TYPES)}"
        )
    if source == target:
        raise RouteCalibrationError("route edge cannot point from a node to itself")

    result = deepcopy(manifest)
    edges = result.setdefault("edges", [])
    if not isinstance(edges, list):
        raise RouteCalibrationError("manifest edges must be a list")
    if any(
        isinstance(edge, dict)
        and edge.get("from") == source
        and edge.get("to") == target
        and edge.get("type") == edge_type
        for edge in edges
    ):
        raise RouteCalibrationError(f"duplicate edge {source}->{target} ({edge_type})")

    edges.append(
        {
            "from": source,
            "to": target,
            "type": edge_type,
            "bidirectional": bool(bidirectional),
            "validated": False,
            "evidence": None,
        }
    )
    return result


def _distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt(sum((left - right) ** 2 for left, right in zip(a, b)))


def validate_edge(
    manifest: dict[str, Any],
    edge_index: int,
    trace_path: Path,
    *,
    radius_m: float = 2.5,
) -> dict[str, Any]:
    """Validate an edge only when a verified trace travels source -> target.

    This is intentionally geometric rather than inferential: the source and target
    nodes must already have captured coordinates, and one verified semantic trace
    must enter the source radius before it later enters the target radius.
    """

    if not math.isfinite(radius_m) or not 0.1 <= radius_m <= 25.0:
        raise RouteCalibrationError("edge validation radius must be between 0.1 and 25 meters")
    edges = manifest.get("edges")
    nodes = manifest.get("nodes")
    if not isinstance(edges, list) or not 0 <= edge_index < len(edges):
        raise RouteCalibrationError(f"edge index {edge_index} is out of range")
    if not isinstance(nodes, dict):
        raise RouteCalibrationError("manifest nodes must be an object")
    edge = edges[edge_index]
    if not isinstance(edge, dict):
        raise RouteCalibrationError("edge entry must be an object")

    def node_xyz(node_id: Any) -> tuple[float, float, float]:
        node = nodes.get(node_id)
        xyz = node.get("xyz") if isinstance(node, dict) else None
        if not isinstance(xyz, list) or len(xyz) != 3:
            raise RouteCalibrationError(f"node {node_id!r} has not been calibrated")
        try:
            values = tuple(float(value) for value in xyz)
        except (TypeError, ValueError) as exc:
            raise RouteCalibrationError(f"node {node_id!r} has invalid XYZ") from exc
        if not all(math.isfinite(value) for value in values):
            raise RouteCalibrationError(f"node {node_id!r} has invalid XYZ")
        return values[0], values[1], values[2]

    source_xyz = node_xyz(edge.get("from"))
    target_xyz = node_xyz(edge.get("to"))
    source_hit: tuple[int, dict[str, Any]] | None = None
    target_hit: tuple[int, dict[str, Any]] | None = None
    sample_count = 0

    for index, (row, xyz) in enumerate(_verified_samples(trace_path)):
        sample_count += 1
        if source_hit is None and _distance(xyz, source_xyz) <= radius_m:
            source_hit = index, row
            continue
        if source_hit is not None and index > source_hit[0] and _distance(xyz, target_xyz) <= radius_m:
            target_hit = index, row
            break

    if source_hit is None or target_hit is None:
        raise RouteCalibrationError(
            f"trace did not prove ordered traversal for edge {edge_index}: "
            f"source_hit={source_hit is not None}, target_hit={target_hit is not None}, "
            f"verified_samples={sample_count}"
        )

    result = deepcopy(manifest)
    result_edge = result["edges"][edge_index]
    result_edge["validated"] = True
    result_edge["evidence"] = {
        "trace": str(trace_path),
        "radius_m": radius_m,
        "source_sample": source_hit[0],
        "target_sample": target_hit[0],
        "source_timestamp": source_hit[1].get("timestamp"),
        "target_timestamp": target_hit[1].get("timestamp"),
    }
    return result


def check_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    nodes = manifest.get("nodes")
    edges = manifest.get("edges")
    problems: list[str] = []
    if not isinstance(nodes, dict):
        nodes = {}
        problems.append("nodes must be an object")
    if not isinstance(edges, list):
        edges = []
        problems.append("edges must be a list")

    missing_nodes = [node_id for node_id in REQUIRED_GEYSER_NODES if node_id not in nodes]
    calibrated_nodes: list[str] = []
    uncalibrated_nodes: list[str] = []
    for node_id in REQUIRED_GEYSER_NODES:
        node = nodes.get(node_id)
        xyz = node.get("xyz") if isinstance(node, dict) else None
        if (
            isinstance(xyz, list)
            and len(xyz) == 3
            and all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in xyz)
        ):
            calibrated_nodes.append(node_id)
        else:
            uncalibrated_nodes.append(node_id)

    invalid_edges: list[int] = []
    unvalidated_edges: list[int] = []
    used_edge_types: set[str] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            invalid_edges.append(index)
            continue
        source = edge.get("from")
        target = edge.get("to")
        edge_type = edge.get("type")
        if source not in nodes or target not in nodes or edge_type not in EDGE_TYPES or source == target:
            invalid_edges.append(index)
            continue
        used_edge_types.add(str(edge_type))
        if edge.get("validated") is not True:
            unvalidated_edges.append(index)

    ready = (
        not problems
        and not missing_nodes
        and not uncalibrated_nodes
        and bool(edges)
        and not invalid_edges
        and not unvalidated_edges
    )
    return {
        "schema": manifest.get("schema"),
        "semantic_schema": manifest.get("semantic_schema"),
        "required_nodes": len(REQUIRED_GEYSER_NODES),
        "calibrated_nodes": calibrated_nodes,
        "uncalibrated_nodes": uncalibrated_nodes,
        "missing_nodes": missing_nodes,
        "edge_count": len(edges),
        "edge_types_used": sorted(used_edge_types),
        "invalid_edges": invalid_edges,
        "unvalidated_edges": unvalidated_edges,
        "problems": problems,
        "ready": ready,
    }


def _write_or_print(path: Path | None, manifest: dict[str, Any]) -> None:
    if path is None:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        save_manifest(path, manifest)
        print(json.dumps(check_manifest(manifest), indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build and validate a calibration-safe Jak Geyser Rock route manifest."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help="write an uncalibrated Geyser Rock manifest")
    init_parser.add_argument("manifest", type=Path)

    check_parser = sub.add_parser("check", help="report calibration/validation readiness")
    check_parser.add_argument("manifest", type=Path)

    capture_parser = sub.add_parser("capture-node", help="capture a node from verified semantic JSONL")
    capture_parser.add_argument("manifest", type=Path)
    capture_parser.add_argument("node", choices=tuple(REQUIRED_GEYSER_NODES))
    capture_parser.add_argument("trace", type=Path)

    edge_parser = sub.add_parser("add-edge", help="add an unvalidated route edge")
    edge_parser.add_argument("manifest", type=Path)
    edge_parser.add_argument("source", choices=tuple(REQUIRED_GEYSER_NODES))
    edge_parser.add_argument("target", choices=tuple(REQUIRED_GEYSER_NODES))
    edge_parser.add_argument("edge_type", choices=EDGE_TYPES)
    edge_parser.add_argument("--bidirectional", action="store_true")

    validate_parser = sub.add_parser(
        "validate-edge", help="prove an edge from ordered verified semantic trace samples"
    )
    validate_parser.add_argument("manifest", type=Path)
    validate_parser.add_argument("edge_index", type=int)
    validate_parser.add_argument("trace", type=Path)
    validate_parser.add_argument("--radius-m", type=float, default=2.5)

    args = parser.parse_args()
    try:
        if args.command == "init":
            manifest = new_manifest()
            save_manifest(args.manifest, manifest)
            print(json.dumps(check_manifest(manifest), indent=2, sort_keys=True))
            return 0
        if args.command == "check":
            report = check_manifest(load_manifest(args.manifest))
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if report["ready"] else 2

        manifest = load_manifest(args.manifest)
        if args.command == "capture-node":
            manifest = capture_node(manifest, args.node, args.trace)
        elif args.command == "add-edge":
            manifest = add_edge(
                manifest,
                args.source,
                args.target,
                args.edge_type,
                bidirectional=args.bidirectional,
            )
        elif args.command == "validate-edge":
            manifest = validate_edge(
                manifest,
                args.edge_index,
                args.trace,
                radius_m=args.radius_m,
            )
        save_manifest(args.manifest, manifest)
        print(json.dumps(check_manifest(manifest), indent=2, sort_keys=True))
        return 0
    except RouteCalibrationError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
