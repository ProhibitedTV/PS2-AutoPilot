from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .jak_acceptance import evaluate_acceptance
from .jak_capture_suite import check_manifest as check_captures
from .jak_capture_suite import default_manifest as default_capture_manifest
from .jak_capture_suite import save_manifest as save_capture_manifest
from .jak_curriculum import check_manifest as check_curriculum
from .jak_curriculum import default_manifest as default_curriculum_manifest
from .jak_route_calibration import check_manifest as check_route
from .jak_route_calibration import load_manifest as load_route_manifest
from .jak_route_calibration import new_manifest as default_route_manifest
from .jak_route_calibration import save_manifest as save_route_manifest


WORKSPACE_SCHEMA = 1
DEFAULT_REQUIRED_RUNS = 5


class ValidationWorkspaceError(RuntimeError):
    pass


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _workspace_config() -> dict[str, Any]:
    return {
        "schema_version": WORKSPACE_SCHEMA,
        "expected_game_id": None,
        "expected_crc": None,
        "required_graduation_runs": DEFAULT_REQUIRED_RUNS,
        "semantic_trace": "semantic/contact.jsonl",
        "route_manifest": "route.json",
        "curriculum_manifest": "curriculum.json",
        "capture_manifest": "captures.json",
        "graduation_dir": "graduation",
    }


def _runbook_text() -> str:
    return """# Jak and Daxter validation workspace

This directory is an evidence workspace, not a set of pre-approved acceptance results.
Every generated manifest starts intentionally incomplete. Do not fill coordinates,
savestate paths, or review flags from guesses.

## 1. Verify semantic movement/contact

Capture a short movement/jump sequence from the real PCSX2 build:

    ps2-autopilot-jak-contact --samples 120 --interval 0.25 --output semantic/contact.jsonl
    ps2-autopilot-jak-semantic-check semantic/contact.jsonl

If known, add `--expected-game-id` and `--expected-crc` to the contact command and the
same identity values to `validation.json`.

## 2. Calibrate the sparse Geyser route

`route.json` contains every required landmark with null XYZ. At each landmark, record
verified semantic telemetry and capture that node with `ps2-autopilot-jak-route`.
Add only the defined traversal edge classes, then validate each edge from an ordered
source-to-target semantic trace. `ps2-autopilot-jak-route check route.json` must pass.

## 3. Capture deterministic skill-gym savestates

`curriculum.json` names the required V22 challenges but leaves savestate paths blank.
Capture real PCSX2 savestates, populate those paths, then run:

    ps2-autopilot-jak-curriculum check curriculum.json

## 4. Collect visual calibration evidence

`captures.json` lists the representative #48 footage classes. Attach real captures with
`ps2-autopilot-jak-captures add`, then explicitly review each scenario. File presence
alone is not a pass.

## 5. Record graduation runs

Store independent fresh-boot/new-save verbose logs in `graduation/`. The V23 target is
five successful autonomous runs out of five, each proving four Power Cells, seven Scout
Flies, Blue Eco door, cliff/platform sequence, return warp, and no intervention.

## 6. Check workspace status

    ps2-autopilot-jak-validation status .

The command reports semantic/route/curriculum/capture/graduation blockers separately.
Missing live evidence remains red by design.
"""


def init_workspace(root: Path, *, force: bool = False) -> dict[str, Any]:
    root = root.expanduser()
    paths = {
        "config": root / "validation.json",
        "route": root / "route.json",
        "curriculum": root / "curriculum.json",
        "captures": root / "captures.json",
        "runbook": root / "README.md",
    }
    existing = [str(path) for path in paths.values() if path.exists()]
    if existing and not force:
        raise ValidationWorkspaceError(
            "refusing to overwrite existing validation files: " + ", ".join(existing)
        )

    root.mkdir(parents=True, exist_ok=True)
    (root / "semantic").mkdir(exist_ok=True)
    (root / "graduation").mkdir(exist_ok=True)
    (root / "savestates").mkdir(exist_ok=True)
    (root / "capture-evidence").mkdir(exist_ok=True)

    _write_json(paths["config"], _workspace_config())
    save_route_manifest(paths["route"], default_route_manifest())
    _write_json(paths["curriculum"], default_curriculum_manifest())
    save_capture_manifest(paths["captures"], default_capture_manifest())
    paths["runbook"].write_text(_runbook_text(), encoding="utf-8")

    return {
        "workspace": str(root),
        "initialized": True,
        "ready": False,
        "files": {name: str(path) for name, path in paths.items()},
        "directories": [
            str(root / "semantic"),
            str(root / "graduation"),
            str(root / "savestates"),
            str(root / "capture-evidence"),
        ],
    }


def _load_config(root: Path) -> dict[str, Any]:
    path = root / "validation.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationWorkspaceError(f"could not read {path}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != WORKSPACE_SCHEMA:
        raise ValidationWorkspaceError(
            f"unsupported validation workspace schema in {path}"
        )
    return value


def _path(root: Path, config: dict[str, Any], key: str) -> Path:
    raw = str(config.get(key) or "").strip()
    if not raw:
        raise ValidationWorkspaceError(f"validation.json is missing {key!r}")
    candidate = Path(raw).expanduser()
    return candidate if candidate.is_absolute() else root / candidate


def status_workspace(root: Path) -> dict[str, Any]:
    root = root.expanduser()
    config = _load_config(root)
    semantic = _path(root, config, "semantic_trace")
    route = _path(root, config, "route_manifest")
    curriculum = _path(root, config, "curriculum_manifest")
    captures = _path(root, config, "capture_manifest")
    graduation_dir = _path(root, config, "graduation_dir")
    try:
        required_runs = max(1, int(config.get("required_graduation_runs", DEFAULT_REQUIRED_RUNS)))
    except (TypeError, ValueError) as exc:
        raise ValidationWorkspaceError("required_graduation_runs must be an integer") from exc

    graduation_logs = (
        sorted(graduation_dir.glob("*.jsonl")) if graduation_dir.is_dir() else []
    )
    acceptance = evaluate_acceptance(
        semantic_trace=semantic,
        route_manifest=route,
        curriculum_manifest=curriculum,
        graduation_logs=graduation_logs,
        expected_game_id=config.get("expected_game_id") or None,
        expected_crc=config.get("expected_crc") or None,
        required_runs=required_runs,
        autonomous_asserted=False,
        fresh_boots_asserted=False,
    )

    if captures.is_file():
        try:
            capture_report = check_captures(captures)
            capture_section = {
                "passed": bool(capture_report.get("ready")),
                "status": "passed" if capture_report.get("ready") else "evidence-incomplete",
                "report": capture_report,
            }
        except Exception as exc:
            capture_section = {
                "passed": False,
                "status": "invalid-evidence",
                "evidence": str(exc),
            }
    else:
        capture_section = {
            "passed": False,
            "status": "live-evidence-missing",
            "evidence": f"capture manifest not found: {captures}",
        }

    route_report = None
    if route.is_file():
        try:
            route_report = check_route(load_route_manifest(route))
        except Exception:
            route_report = None
    curriculum_report = None
    if curriculum.is_file():
        try:
            curriculum_report = check_curriculum(curriculum)
        except Exception:
            curriculum_report = None

    all_passed = bool(acceptance.get("passed") and capture_section["passed"])
    next_actions: list[str] = []
    semantic_section = acceptance["sections"]["v21_semantics"]
    if not semantic_section["passed"]:
        next_actions.append("capture/validate semantic ground-air-ground telemetry")
    if not acceptance["sections"]["v21_route"]["passed"]:
        next_actions.append("calibrate and validate the Geyser route manifest")
    if not acceptance["sections"]["v22_curriculum"]["passed"]:
        next_actions.append("capture all required deterministic curriculum savestates")
    if not capture_section["passed"]:
        next_actions.append("collect and review every required visual capture scenario")
    if not acceptance["sections"]["v23_graduation"]["passed"]:
        next_actions.append(
            f"record {required_runs} independent fresh autonomous Geyser graduation runs"
        )

    return {
        "workspace": str(root),
        "ready": all_passed,
        "required_graduation_runs": required_runs,
        "graduation_logs_found": len(graduation_logs),
        "acceptance": acceptance,
        "captures": capture_section,
        "route_summary": route_report,
        "curriculum_summary": curriculum_report,
        "next_actions": next_actions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ps2-autopilot-jak-validation",
        description="Initialize and report a finite Jak live-validation evidence workspace.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="create blank route/curriculum/capture evidence manifests")
    init.add_argument("workspace", type=Path)
    init.add_argument("--force", action="store_true")
    status = sub.add_parser("status", help="report every remaining Jak live-evidence blocker")
    status.add_argument("workspace", type=Path)
    args = parser.parse_args(argv)

    try:
        report = (
            init_workspace(args.workspace, force=args.force)
            if args.command == "init"
            else status_workspace(args.workspace)
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report.get("ready") else 1
    except ValidationWorkspaceError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
