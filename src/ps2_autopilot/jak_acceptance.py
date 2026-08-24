from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from .jak_curriculum import check_manifest as check_curriculum
from .jak_graduation import evaluate_suite
from .jak_route_calibration import check_manifest as check_route, load_manifest as load_route
from .jak_semantic_validation import validate as validate_semantics


def _missing(name: str, evidence: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": False,
        "status": "live-evidence-missing",
        "evidence": evidence,
    }


def evaluate_acceptance(
    *,
    semantic_trace: Path | None = None,
    route_manifest: Path | None = None,
    curriculum_manifest: Path | None = None,
    graduation_logs: Iterable[Path] = (),
    expected_game_id: str | None = None,
    expected_crc: str | None = None,
    required_runs: int = 5,
    autonomous_asserted: bool = False,
    fresh_boots_asserted: bool = False,
) -> dict[str, Any]:
    """Aggregate the remaining Jak #66 acceptance gates without weakening them."""

    sections: dict[str, dict[str, Any]] = {}

    if semantic_trace is None:
        sections["v21_semantics"] = _missing(
            "V21 semantic calibration",
            "provide a verbose JSONL trace from the calibrated live PCSX2 build",
        )
    elif not semantic_trace.is_file():
        sections["v21_semantics"] = _missing(
            "V21 semantic calibration",
            f"semantic trace not found: {semantic_trace}",
        )
    else:
        report = validate_semantics(
            semantic_trace,
            expected_game_id=expected_game_id,
            expected_crc=expected_crc,
        )
        sections["v21_semantics"] = {
            "name": "V21 semantic calibration",
            "passed": bool(report.get("passed")),
            "status": "passed" if report.get("passed") else "evidence-incomplete",
            "report": report,
        }

    if route_manifest is None:
        sections["v21_route"] = _missing(
            "V21 Geyser route map",
            "provide a route manifest populated from verified semantic traces",
        )
    elif not route_manifest.is_file():
        sections["v21_route"] = _missing(
            "V21 Geyser route map",
            f"route manifest not found: {route_manifest}",
        )
    else:
        report = check_route(load_route(route_manifest))
        sections["v21_route"] = {
            "name": "V21 Geyser route map",
            "passed": bool(report.get("ready")),
            "status": "passed" if report.get("ready") else "evidence-incomplete",
            "report": report,
        }

    if curriculum_manifest is None:
        sections["v22_curriculum"] = _missing(
            "V22 deterministic skill curriculum",
            "provide a curriculum manifest whose required PCSX2 savestates exist",
        )
    elif not curriculum_manifest.is_file():
        sections["v22_curriculum"] = _missing(
            "V22 deterministic skill curriculum",
            f"curriculum manifest not found: {curriculum_manifest}",
        )
    else:
        try:
            report = check_curriculum(curriculum_manifest)
            sections["v22_curriculum"] = {
                "name": "V22 deterministic skill curriculum",
                "passed": bool(report.get("ready")),
                "status": "passed" if report.get("ready") else "evidence-incomplete",
                "report": report,
            }
        except ValueError as exc:
            sections["v22_curriculum"] = {
                "name": "V22 deterministic skill curriculum",
                "passed": False,
                "status": "invalid-evidence",
                "evidence": str(exc),
            }

    logs = tuple(graduation_logs)
    missing_logs = [str(path) for path in logs if not path.is_file()]
    if not logs:
        sections["v23_graduation"] = _missing(
            "V23 Geyser graduation",
            f"provide {max(1, int(required_runs))} independent fresh autonomous run logs",
        )
    elif missing_logs:
        sections["v23_graduation"] = _missing(
            "V23 Geyser graduation",
            "graduation log(s) not found: " + ", ".join(missing_logs),
        )
    else:
        report = evaluate_suite(
            logs,
            required_runs=required_runs,
            autonomous_asserted=autonomous_asserted,
            fresh_boots_asserted=fresh_boots_asserted,
        )
        sections["v23_graduation"] = {
            "name": "V23 Geyser graduation",
            "passed": bool(report.get("graduated")),
            "status": "passed" if report.get("graduated") else "evidence-incomplete",
            "report": report,
        }

    passed_sections = [name for name, section in sections.items() if section["passed"]]
    blockers = [
        {
            "section": name,
            "status": section["status"],
            "evidence": section.get("evidence"),
        }
        for name, section in sections.items()
        if not section["passed"]
    ]
    passed = len(passed_sections) == len(sections)
    return {
        "passed": passed,
        "target": "Jak Geyser Rock V21-V23 acceptance",
        "passed_sections": passed_sections,
        "remaining_sections": [name for name in sections if name not in passed_sections],
        "blockers": blockers,
        "sections": sections,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ps2-autopilot-jak-acceptance",
        description="Aggregate the strict Jak V21-V23 acceptance gates into one report.",
    )
    parser.add_argument("--semantic-trace", type=Path)
    parser.add_argument("--route-manifest", type=Path)
    parser.add_argument("--curriculum-manifest", type=Path)
    parser.add_argument("--graduation-log", action="append", type=Path, default=[])
    parser.add_argument("--expected-game-id")
    parser.add_argument("--expected-crc")
    parser.add_argument("--required-runs", type=int, default=5)
    parser.add_argument(
        "--autonomous",
        action="store_true",
        help="assert that supplied graduation runs had no human controller intervention",
    )
    parser.add_argument(
        "--fresh-boots",
        action="store_true",
        help="assert that each supplied graduation run began from a fresh boot/new save",
    )
    args = parser.parse_args(argv)

    report = evaluate_acceptance(
        semantic_trace=args.semantic_trace,
        route_manifest=args.route_manifest,
        curriculum_manifest=args.curriculum_manifest,
        graduation_logs=args.graduation_log,
        expected_game_id=args.expected_game_id,
        expected_crc=args.expected_crc,
        required_runs=args.required_runs,
        autonomous_asserted=args.autonomous,
        fresh_boots_asserted=args.fresh_boots,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
