from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
KNOWN_ATOMIC_SKILLS = {
    "jump",
    "hop_step",
    "double_jump",
    "roll_jump",
    "dive",
    "platform_chain",
}
KNOWN_CHALLENGE_KINDS = {"atomic_skill", "safety", "objective"}


DEFAULT_CHALLENGES: tuple[dict[str, Any], ...] = (
    {
        "id": "first-gap-hop",
        "kind": "atomic_skill",
        "description": "Short Geyser traversal gap suitable for hop-step verification.",
        "atomic_skill": "hop_step",
        "objective_stage": "first_cell",
        "savestate_path": None,
        "expected_telemetry": [
            "jak_skill_hop_step_attempts",
            "jak_skill_hop_step_successes",
        ],
    },
    {
        "id": "blocked-target-jump",
        "kind": "atomic_skill",
        "description": "Low obstruction or blocked reward requiring one committed jump.",
        "atomic_skill": "jump",
        "objective_stage": "first_cell",
        "savestate_path": None,
        "expected_telemetry": [
            "jak_skill_jump_attempts",
            "jak_skill_jump_successes",
        ],
    },
    {
        "id": "high-gap-double-jump",
        "kind": "atomic_skill",
        "description": "Representative gap or ledge requiring a double-jump transaction.",
        "atomic_skill": "double_jump",
        "objective_stage": "cliff_cell",
        "savestate_path": None,
        "expected_telemetry": [
            "jak_skill_double_jump_attempts",
            "jak_skill_double_jump_successes",
        ],
    },
    {
        "id": "roll-jump-gap",
        "kind": "atomic_skill",
        "description": "Open approach with enough runway for deterministic roll-jump testing.",
        "atomic_skill": "roll_jump",
        "objective_stage": "cliff_cell",
        "savestate_path": None,
        "expected_telemetry": [
            "jak_skill_roll_jump_attempts",
            "jak_skill_roll_jump_successes",
        ],
    },
    {
        "id": "scout-fly-dive",
        "kind": "atomic_skill",
        "description": "Stable Scout Fly box alignment before the jump-and-dive interaction.",
        "atomic_skill": "dive",
        "objective_stage": "scout_flies",
        "savestate_path": None,
        "expected_telemetry": [
            "jak_skill_dive_attempts",
            "jak_skill_dive_successes",
        ],
    },
    {
        "id": "cliff-platform-chain",
        "kind": "atomic_skill",
        "description": "Representative cliff ledge for the V22 platform-chain controller.",
        "atomic_skill": "platform_chain",
        "objective_stage": "cliff_cell",
        "savestate_path": None,
        "expected_telemetry": [
            "jak_skill_platform_chain_attempts",
            "jak_skill_platform_chain_successes",
        ],
    },
    {
        "id": "shoreline-swim-escape",
        "kind": "safety",
        "description": "Confirmed-water start near a recoverable Geyser shoreline.",
        "atomic_skill": None,
        "objective_stage": None,
        "savestate_path": None,
        "expected_telemetry": [
            "jak_water_uturns",
            "jak_learning_water_escape_events_v22",
        ],
    },
    {
        "id": "blue-eco-door-run",
        "kind": "objective",
        "description": "Blue Eco acquired or immediately available before the Precursor door run.",
        "atomic_skill": None,
        "objective_stage": "blue_eco_door",
        "savestate_path": None,
        "expected_telemetry": [
            "jak_objective_stage",
            "jak_goal_progress_events",
        ],
    },
)


@dataclass(frozen=True)
class CurriculumChallenge:
    id: str
    kind: str
    description: str
    savestate_path: str | None
    atomic_skill: str | None = None
    objective_stage: str | None = None
    expected_telemetry: tuple[str, ...] = ()
    required: bool = True


@dataclass(frozen=True)
class CurriculumManifest:
    schema_version: int
    game: str
    challenges: tuple[CurriculumChallenge, ...]


def default_manifest() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "game": "jak_and_daxter_the_precursor_legacy",
        "notes": (
            "Fill savestate_path only with real PCSX2 savestates captured from the "
            "calibrated live build. Do not invent coordinates, RAM addresses, or states."
        ),
        "challenges": [dict(item) for item in DEFAULT_CHALLENGES],
    }


def _as_nonempty_string(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must be a non-empty string")
    return text


def parse_manifest(raw: Any) -> CurriculumManifest:
    if not isinstance(raw, dict):
        raise ValueError("manifest root must be an object")
    try:
        version = int(raw.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("schema_version must be an integer") from exc
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version={version}; expected {SCHEMA_VERSION}"
        )

    game = _as_nonempty_string(raw.get("game"), field="game")
    values = raw.get("challenges")
    if not isinstance(values, list) or not values:
        raise ValueError("challenges must be a non-empty array")

    seen_ids: set[str] = set()
    challenges: list[CurriculumChallenge] = []
    for index, value in enumerate(values):
        if not isinstance(value, dict):
            raise ValueError(f"challenges[{index}] must be an object")
        challenge_id = _as_nonempty_string(
            value.get("id"), field=f"challenges[{index}].id"
        )
        if challenge_id in seen_ids:
            raise ValueError(f"duplicate challenge id: {challenge_id}")
        seen_ids.add(challenge_id)

        kind = _as_nonempty_string(
            value.get("kind"), field=f"challenges[{index}].kind"
        )
        if kind not in KNOWN_CHALLENGE_KINDS:
            raise ValueError(
                f"challenge {challenge_id}: unknown kind {kind!r}; "
                f"expected one of {sorted(KNOWN_CHALLENGE_KINDS)}"
            )

        description = _as_nonempty_string(
            value.get("description"), field=f"challenges[{index}].description"
        )
        raw_path = value.get("savestate_path")
        savestate_path = None
        if raw_path is not None and str(raw_path).strip():
            savestate_path = str(raw_path).strip()

        raw_skill = value.get("atomic_skill")
        atomic_skill = None if raw_skill is None else str(raw_skill).strip() or None
        if kind == "atomic_skill":
            if atomic_skill not in KNOWN_ATOMIC_SKILLS:
                raise ValueError(
                    f"challenge {challenge_id}: atomic_skill must be one of "
                    f"{sorted(KNOWN_ATOMIC_SKILLS)}"
                )
        elif atomic_skill is not None:
            raise ValueError(
                f"challenge {challenge_id}: atomic_skill is only valid for atomic_skill challenges"
            )

        raw_stage = value.get("objective_stage")
        objective_stage = None if raw_stage is None else str(raw_stage).strip() or None

        telemetry = value.get("expected_telemetry", [])
        if not isinstance(telemetry, list):
            raise ValueError(
                f"challenge {challenge_id}: expected_telemetry must be an array"
            )
        expected_telemetry = tuple(
            _as_nonempty_string(item, field=f"challenge {challenge_id} telemetry key")
            for item in telemetry
        )
        if not expected_telemetry:
            raise ValueError(
                f"challenge {challenge_id}: expected_telemetry cannot be empty"
            )

        challenges.append(
            CurriculumChallenge(
                id=challenge_id,
                kind=kind,
                description=description,
                savestate_path=savestate_path,
                atomic_skill=atomic_skill,
                objective_stage=objective_stage,
                expected_telemetry=expected_telemetry,
                required=bool(value.get("required", True)),
            )
        )

    return CurriculumManifest(
        schema_version=version,
        game=game,
        challenges=tuple(challenges),
    )


def load_manifest(path: Path) -> CurriculumManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read manifest: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    return parse_manifest(raw)


def _resolve_savestate(manifest_path: Path, value: str | None) -> Path | None:
    if not value:
        return None
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = manifest_path.parent / candidate
    return candidate


def check_manifest(path: Path) -> dict[str, Any]:
    manifest = load_manifest(path)
    rows: list[dict[str, Any]] = []
    required_total = 0
    required_ready = 0

    for challenge in manifest.challenges:
        resolved = _resolve_savestate(path, challenge.savestate_path)
        exists = bool(resolved is not None and resolved.is_file())
        if challenge.required:
            required_total += 1
            required_ready += int(exists)
        rows.append(
            {
                "id": challenge.id,
                "kind": challenge.kind,
                "required": challenge.required,
                "ready": exists,
                "atomic_skill": challenge.atomic_skill,
                "objective_stage": challenge.objective_stage,
                "savestate_path": challenge.savestate_path,
                "resolved_savestate_path": None if resolved is None else str(resolved),
                "expected_telemetry": list(challenge.expected_telemetry),
                "status": (
                    "ready"
                    if exists
                    else "savestate-not-configured"
                    if resolved is None
                    else "savestate-file-missing"
                ),
            }
        )

    ready = bool(required_total > 0 and required_ready == required_total)
    return {
        "manifest": str(path),
        "schema_version": manifest.schema_version,
        "game": manifest.game,
        "ready": ready,
        "required_total": required_total,
        "required_ready": required_ready,
        "missing_required": required_total - required_ready,
        "challenges": rows,
    }


def _write_template(path: Path, *, force: bool) -> None:
    if path.exists() and not force:
        raise ValueError(f"refusing to overwrite existing file: {path}; use --force")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(default_manifest(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ps2-autopilot-jak-curriculum",
        description=(
            "Create or validate a deterministic Jak skill-gym savestate manifest."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    template_parser = subparsers.add_parser(
        "template",
        help="print the default curriculum manifest without inventing savestate paths",
    )
    template_parser.add_argument("--output", type=Path)
    template_parser.add_argument("--force", action="store_true")

    check_parser = subparsers.add_parser(
        "check",
        help="validate a manifest and verify every required savestate file exists",
    )
    check_parser.add_argument("manifest", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "template":
            if args.output is None:
                _print_json(default_manifest())
            else:
                _write_template(args.output, force=args.force)
                _print_json({"written": str(args.output), "ready": False})
            return 0

        report = check_manifest(args.manifest)
        _print_json(report)
        return 0 if report["ready"] else 1
    except ValueError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
