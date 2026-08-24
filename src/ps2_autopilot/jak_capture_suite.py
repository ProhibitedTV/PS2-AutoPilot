from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".json", ".jsonl"}

DEFAULT_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "id": "gameplay_on_foot",
        "group": "state-classification",
        "description": "Representative controllable on-foot gameplay frames.",
        "min_samples": 3,
    },
    {
        "id": "cutscene_dialog",
        "group": "state-classification",
        "description": "Representative story cutscene and/or dialog presentation frames.",
        "min_samples": 3,
    },
    {
        "id": "pause_menu",
        "group": "state-classification",
        "description": "Paused gameplay/menu frames distinct from story presentation.",
        "min_samples": 2,
    },
    {
        "id": "death_checkpoint",
        "group": "state-classification",
        "description": "Death, respawn, and checkpoint transition evidence.",
        "min_samples": 3,
    },
    {
        "id": "loading_save_state",
        "group": "state-classification",
        "description": "Loading and save-related non-controllable states.",
        "min_samples": 3,
    },
    {
        "id": "new_game_save_selector",
        "group": "save-flow",
        "description": "New Game save-file selector with the intended empty-slot path visible.",
        "min_samples": 2,
    },
    {
        "id": "load_save_variant",
        "group": "save-flow",
        "description": "At least one populated/load-save menu variant for negative-boundary calibration.",
        "min_samples": 2,
    },
    {
        "id": "traversable_ground",
        "group": "world-perception",
        "description": "Representative traversable-space cues from on-foot gameplay.",
        "min_samples": 3,
    },
    {
        "id": "ledge_gap",
        "group": "world-perception",
        "description": "Representative ledges/gaps at useful approach distances.",
        "min_samples": 3,
    },
    {
        "id": "enemy_hazard",
        "group": "world-perception",
        "description": "Representative enemy and environmental hazard evidence.",
        "min_samples": 3,
    },
    {
        "id": "a_grav_zoomer",
        "group": "control-mode",
        "description": "A-Grav Zoomer active-control frames.",
        "min_samples": 3,
    },
    {
        "id": "flut_flut",
        "group": "control-mode",
        "description": "Flut Flut active-control frames.",
        "min_samples": 3,
    },
    {
        "id": "cannon",
        "group": "control-mode",
        "description": "Cannon active-control/aiming frames.",
        "min_samples": 3,
    },
    {
        "id": "fishing",
        "group": "control-mode",
        "description": "Fishing minigame active-control frames.",
        "min_samples": 3,
    },
)


class CaptureSuiteError(RuntimeError):
    pass


def default_manifest() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "game": "jak_and_daxter_the_precursor_legacy",
        "notes": (
            "Attach only real PCSX2 captures or runtime evidence. The suite checks evidence "
            "coverage; it does not claim visual calibration succeeded merely because files exist."
        ),
        "scenarios": [
            {
                **scenario,
                "required": True,
                "evidence": [],
                "reviewed": False,
                "review_note": None,
            }
            for scenario in DEFAULT_SCENARIOS
        ],
    }


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaptureSuiteError(f"could not read capture manifest {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CaptureSuiteError("capture manifest root must be an object")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise CaptureSuiteError(
            f"unsupported schema_version={raw.get('schema_version')!r}; expected {SCHEMA_VERSION}"
        )
    scenarios = raw.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise CaptureSuiteError("capture manifest scenarios must be a non-empty array")
    return raw


def save_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _scenario_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, scenario in enumerate(manifest.get("scenarios", [])):
        if not isinstance(scenario, dict):
            raise CaptureSuiteError(f"scenarios[{index}] must be an object")
        scenario_id = str(scenario.get("id") or "").strip()
        if not scenario_id:
            raise CaptureSuiteError(f"scenarios[{index}].id must be non-empty")
        if scenario_id in result:
            raise CaptureSuiteError(f"duplicate scenario id: {scenario_id}")
        result[scenario_id] = scenario
    return result


def _resolve_evidence(manifest_path: Path, value: Any) -> Path | None:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = manifest_path.parent / path
    return path


def add_evidence(
    manifest: dict[str, Any],
    scenario_id: str,
    evidence_paths: list[str],
) -> dict[str, Any]:
    scenarios = _scenario_map(manifest)
    if scenario_id not in scenarios:
        raise CaptureSuiteError(f"unknown capture scenario {scenario_id!r}")
    cleaned = [str(value).strip() for value in evidence_paths if str(value).strip()]
    if not cleaned:
        raise CaptureSuiteError("at least one evidence path is required")

    result = deepcopy(manifest)
    target = _scenario_map(result)[scenario_id]
    evidence = target.setdefault("evidence", [])
    if not isinstance(evidence, list):
        raise CaptureSuiteError(f"scenario {scenario_id}: evidence must be an array")
    for value in cleaned:
        if value not in evidence:
            evidence.append(value)
    target["reviewed"] = False
    target["review_note"] = None
    return result


def review_scenario(
    manifest: dict[str, Any],
    scenario_id: str,
    *,
    approved: bool,
    note: str,
) -> dict[str, Any]:
    scenarios = _scenario_map(manifest)
    if scenario_id not in scenarios:
        raise CaptureSuiteError(f"unknown capture scenario {scenario_id!r}")
    note = note.strip()
    if not note:
        raise CaptureSuiteError("review note must be non-empty")
    result = deepcopy(manifest)
    target = _scenario_map(result)[scenario_id]
    target["reviewed"] = bool(approved)
    target["review_note"] = note
    return result


def check_manifest(path: Path) -> dict[str, Any]:
    manifest = load_manifest(path)
    scenario_map = _scenario_map(manifest)
    expected_ids = {scenario["id"] for scenario in DEFAULT_SCENARIOS}
    missing_scenarios = sorted(expected_ids - set(scenario_map))
    extra_scenarios = sorted(set(scenario_map) - expected_ids)

    rows: list[dict[str, Any]] = []
    required_total = required_ready = 0
    for scenario_id, scenario in scenario_map.items():
        try:
            min_samples = max(1, int(scenario.get("min_samples", 1)))
        except (TypeError, ValueError):
            min_samples = 1
        evidence_values = scenario.get("evidence")
        if not isinstance(evidence_values, list):
            evidence_values = []

        files: list[dict[str, Any]] = []
        valid_existing = 0
        for value in evidence_values:
            resolved = _resolve_evidence(path, value)
            extension_ok = bool(resolved and resolved.suffix.lower() in ALLOWED_EXTENSIONS)
            exists = bool(resolved and resolved.is_file())
            usable = bool(exists and extension_ok)
            valid_existing += int(usable)
            files.append(
                {
                    "path": value,
                    "resolved": None if resolved is None else str(resolved),
                    "exists": exists,
                    "extension_ok": extension_ok,
                    "usable": usable,
                }
            )

        enough_samples = valid_existing >= min_samples
        reviewed = scenario.get("reviewed") is True
        ready = bool(enough_samples and reviewed)
        required = bool(scenario.get("required", True))
        if required:
            required_total += 1
            required_ready += int(ready)

        rows.append(
            {
                "id": scenario_id,
                "group": scenario.get("group"),
                "description": scenario.get("description"),
                "required": required,
                "min_samples": min_samples,
                "usable_samples": valid_existing,
                "enough_samples": enough_samples,
                "reviewed": reviewed,
                "review_note": scenario.get("review_note"),
                "ready": ready,
                "evidence": files,
            }
        )

    ready = bool(
        required_total > 0
        and required_ready == required_total
        and not missing_scenarios
        and not extra_scenarios
    )
    next_missing = next((row["id"] for row in rows if row["required"] and not row["ready"]), None)
    return {
        "manifest": str(path),
        "schema_version": manifest.get("schema_version"),
        "ready": ready,
        "required_total": required_total,
        "required_ready": required_ready,
        "missing_required": required_total - required_ready,
        "missing_scenarios": missing_scenarios,
        "extra_scenarios": extra_scenarios,
        "next_missing": next_missing,
        "scenarios": rows,
    }


def _write_template(path: Path, *, force: bool) -> None:
    if path.exists() and not force:
        raise CaptureSuiteError(f"refusing to overwrite existing file: {path}; use --force")
    save_manifest(path, default_manifest())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ps2-autopilot-jak-captures",
        description="Plan and verify the representative live capture suite for Jak acceptance.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    template = sub.add_parser("template", help="write the required Jak capture checklist")
    template.add_argument("manifest", type=Path)
    template.add_argument("--force", action="store_true")

    check = sub.add_parser("check", help="report missing/unreviewed capture evidence")
    check.add_argument("manifest", type=Path)

    add = sub.add_parser("add", help="attach capture files to one scenario")
    add.add_argument("manifest", type=Path)
    add.add_argument("scenario", choices=tuple(item["id"] for item in DEFAULT_SCENARIOS))
    add.add_argument("evidence", nargs="+")

    review = sub.add_parser("review", help="record explicit human review of a capture scenario")
    review.add_argument("manifest", type=Path)
    review.add_argument("scenario", choices=tuple(item["id"] for item in DEFAULT_SCENARIOS))
    review.add_argument("--note", required=True)
    review.add_argument("--reject", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "template":
            _write_template(args.manifest, force=args.force)
            report = check_manifest(args.manifest)
        elif args.command == "check":
            report = check_manifest(args.manifest)
        else:
            manifest = load_manifest(args.manifest)
            if args.command == "add":
                manifest = add_evidence(manifest, args.scenario, args.evidence)
            else:
                manifest = review_scenario(
                    manifest,
                    args.scenario,
                    approved=not args.reject,
                    note=args.note,
                )
            save_manifest(args.manifest, manifest)
            report = check_manifest(args.manifest)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["ready"] else 1
    except CaptureSuiteError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
