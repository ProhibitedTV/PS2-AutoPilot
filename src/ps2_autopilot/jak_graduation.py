from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from .jak_benchmark import _records, summarize


_COMPLETION_KEYS = (
    "geyser_complete",
    "jak_geyser_complete",
    "geyser_graduated",
    "jak_geyser_graduated",
)
_BLUE_ECO_DOOR_KEYS = (
    "geyser_blue_eco_door_complete",
    "jak_blue_eco_door_complete",
    "blue_eco_door_complete",
)
_CLIFF_SEQUENCE_KEYS = (
    "geyser_cliff_sequence_complete",
    "jak_cliff_sequence_complete",
    "cliff_sequence_complete",
)
_RETURN_WARP_KEYS = (
    "geyser_return_warp_complete",
    "jak_return_warp_complete",
    "return_warp_complete",
)
_INTERVENTION_COUNT_KEYS = (
    "jak_human_interventions",
    "human_interventions",
    "manual_interventions",
    "operator_interventions",
)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "complete", "completed", "done"}


def _explicit_true(rows: Iterable[dict[str, Any]], keys: tuple[str, ...]) -> bool:
    for row in rows:
        for key in keys:
            if key in row and _truthy(row.get(key)):
                return True
    return False


def _intervention_evidence(rows: Iterable[dict[str, Any]]) -> tuple[bool | None, str]:
    seen = False
    maximum = 0
    for row in rows:
        for key in _INTERVENTION_COUNT_KEYS:
            if key not in row:
                continue
            seen = True
            try:
                maximum = max(maximum, int(row.get(key) or 0))
            except (TypeError, ValueError):
                return None, f"invalid telemetry in {key}"
    if not seen:
        return None, "no intervention telemetry"
    if maximum > 0:
        return False, f"telemetry reports {maximum} intervention(s)"
    return True, "telemetry reports zero interventions"


def _criterion(passed: bool, evidence: str) -> dict[str, Any]:
    return {"passed": bool(passed), "evidence": evidence}


def evaluate_run(
    path: Path,
    *,
    autonomous_asserted: bool = False,
    fresh_boot_asserted: bool = False,
) -> dict[str, Any]:
    """Evaluate one Jak verbose log against the V23 Geyser Rock acceptance gate.

    Transaction milestones prefer explicit telemetry. A verified overall Geyser
    completion is also accepted as proof that prerequisite door/cliff/return stages
    were completed. Merely reaching a later planner stage is reported as diagnostic
    evidence but never turns a strict acceptance criterion green by itself.
    """

    rows = list(_records(path))
    summary = summarize(path)
    stage_names = {str(name) for name, _count in summary.get("objective_stage_samples", [])}

    cells = summary.get("max_geyser_cells")
    flies = summary.get("max_geyser_scout_flies")
    cells_ok = cells is not None and int(cells) >= 4
    flies_ok = flies is not None and int(flies) >= 7

    explicit_completion = _explicit_true(rows, _COMPLETION_KEYS)
    completion_proof = bool(explicit_completion or summary.get("graduated"))

    door_explicit = _explicit_true(rows, _BLUE_ECO_DOOR_KEYS)
    cliff_explicit = _explicit_true(rows, _CLIFF_SEQUENCE_KEYS)
    return_explicit = _explicit_true(rows, _RETURN_WARP_KEYS)

    door_ok = bool(door_explicit or completion_proof)
    cliff_ok = bool(cliff_explicit or completion_proof)
    return_ok = bool(return_explicit or completion_proof)

    intervention_ok, intervention_evidence = _intervention_evidence(rows)
    autonomous_ok = bool(autonomous_asserted or intervention_ok is True)
    if autonomous_asserted:
        intervention_evidence = "operator asserted autonomous run"

    inferred = {
        "blue_eco_door": bool(stage_names & {"cliff_cell", "return_warp", "complete"}),
        "cliff_sequence": bool(stage_names & {"return_warp", "complete"}),
        "return_warp": "complete" in stage_names,
    }

    criteria = {
        "four_power_cells": _criterion(cells_ok, f"max Geyser cell delta={cells}"),
        "seven_scout_flies": _criterion(flies_ok, f"max Geyser fly delta={flies}"),
        "blue_eco_door": _criterion(
            door_ok,
            "explicit milestone" if door_explicit else "verified Geyser completion" if completion_proof else "missing explicit completion evidence",
        ),
        "cliff_platform_sequence": _criterion(
            cliff_ok,
            "explicit milestone" if cliff_explicit else "verified Geyser completion" if completion_proof else "missing explicit completion evidence",
        ),
        "return_warp": _criterion(
            return_ok,
            "explicit milestone" if return_explicit else "verified Geyser completion" if completion_proof else "missing explicit completion evidence",
        ),
        "no_human_intervention": _criterion(autonomous_ok, intervention_evidence),
        "fresh_boot_new_save": _criterion(
            fresh_boot_asserted,
            "operator asserted fresh boot/new save" if fresh_boot_asserted else "fresh boot/new save not verified",
        ),
    }
    passed = all(item["passed"] for item in criteria.values())

    return {
        "file": str(path),
        "passed": passed,
        "criteria": criteria,
        "planner_inference_only": inferred,
        "completion_proof": completion_proof,
        "samples": summary.get("samples", 0),
        "duration_seconds": summary.get("duration_seconds"),
        "policy_samples": summary.get("policy_samples", []),
    }


def evaluate_suite(
    paths: Iterable[Path],
    *,
    required_runs: int = 5,
    autonomous_asserted: bool = False,
    fresh_boots_asserted: bool = False,
) -> dict[str, Any]:
    required_runs = max(1, int(required_runs))
    runs = [
        evaluate_run(
            path,
            autonomous_asserted=autonomous_asserted,
            fresh_boot_asserted=fresh_boots_asserted,
        )
        for path in paths
    ]
    passed_runs = sum(1 for run in runs if run["passed"])
    total_runs = len(runs)
    graduated = bool(
        total_runs >= required_runs
        and passed_runs == total_runs
        and autonomous_asserted
        and fresh_boots_asserted
    )
    return {
        "graduated": graduated,
        "required_runs": required_runs,
        "total_runs": total_runs,
        "passed_runs": passed_runs,
        "target": f"{required_runs}/{required_runs} fresh autonomous completions",
        "autonomous_asserted": autonomous_asserted,
        "fresh_boots_asserted": fresh_boots_asserted,
        "runs": runs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ps2-autopilot-jak-graduation",
        description="Evaluate Jak Geyser Rock logs against the strict V23 graduation gate.",
    )
    parser.add_argument("paths", nargs="+", help="verbose.jsonl files from independent runs")
    parser.add_argument("--required-runs", type=int, default=5)
    parser.add_argument(
        "--autonomous",
        action="store_true",
        help="assert that supplied runs had no human controller intervention",
    )
    parser.add_argument(
        "--fresh-boots",
        action="store_true",
        help="assert that each supplied run began from a fresh boot/new save",
    )
    args = parser.parse_args(argv)

    paths = [Path(value) for value in args.paths]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        parser.error("file not found: " + ", ".join(missing))

    report = evaluate_suite(
        paths,
        required_runs=args.required_runs,
        autonomous_asserted=args.autonomous,
        fresh_boots_asserted=args.fresh_boots,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["graduated"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
