from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from .jak_curriculum import check_manifest as check_curriculum_manifest
from .jak_curriculum import load_manifest as load_curriculum_manifest
from .observability import read_jsonl


SCHEMA_VERSION = 1
DEFAULT_MAX_SECONDS = 60.0
SUPPORTED_RULE_OPS = {"increase", "equals", "not_equals"}


def _atomic_prefix(skill: str) -> str:
    return f"jak_skill_{skill}"


def _default_rules(kind: str, atomic_skill: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if kind == "atomic_skill" and atomic_skill:
        prefix = _atomic_prefix(atomic_skill)
        return (
            [
                {"key": f"{prefix}_successes", "op": "increase"},
                {"key": "jak_atomic_skill_active", "op": "equals", "value": False},
            ],
            [{"key": f"{prefix}_safety_aborts", "op": "increase"}],
        )
    if kind == "safety":
        # The hardened V22 wrapper increments this only after an active water escape
        # ends on dry land and the successful escape direction is persisted.
        return (
            [{"key": "jak_learning_water_escape_events_v22", "op": "increase"}],
            [],
        )
    # Objective completion signals are intentionally left manual. Progress/event
    # telemetry is useful evidence, but it is not yet unique enough to prove that an
    # arbitrary objective transaction completed rather than merely began.
    return ([], [])


def build_template(curriculum_manifest: str) -> dict[str, Any]:
    curriculum_path = Path(curriculum_manifest)
    curriculum = load_curriculum_manifest(curriculum_path)
    episodes: list[dict[str, Any]] = []
    for challenge in curriculum.challenges:
        success, failure = _default_rules(challenge.kind, challenge.atomic_skill)
        episodes.append(
            {
                "challenge_id": challenge.id,
                "required": challenge.required,
                "pine_slot": None,
                "max_seconds": DEFAULT_MAX_SECONDS,
                "success_rules": success,
                "failure_rules": failure,
                "notes": (
                    "success predicate derived from monotonic production telemetry"
                    if success
                    else "manual success predicate required before this episode is runnable"
                ),
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "game": curriculum.game,
        "curriculum_manifest": str(curriculum_manifest),
        "expected_game_ids": [],
        "expected_crcs": [],
        "expected_title_contains": "jak and daxter",
        "notes": (
            "PINE slots are machine-local lab bindings. Configure only slots backed by "
            "the real savestates named in the curriculum manifest. At least one exact "
            "game ID or CRC is required before any episode is runnable."
        ),
        "episodes": episodes,
    }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: root must be an object")
    return raw


def _resolve(parent: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = parent / path
    return path


def _strings(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    output = [str(item or "").strip() for item in value]
    if any(not item for item in output):
        raise ValueError(f"{field} cannot contain blank values")
    return output


def _parse_rule(raw: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError(f"{field} must be an object")
    key = str(raw.get("key") or "").strip()
    op = str(raw.get("op") or "").strip()
    if not key:
        raise ValueError(f"{field}.key must be non-empty")
    if op not in SUPPORTED_RULE_OPS:
        raise ValueError(f"{field}.op must be one of {sorted(SUPPORTED_RULE_OPS)}")
    if op in {"equals", "not_equals"} and "value" not in raw:
        raise ValueError(f"{field}.value is required for op={op}")
    rule = {"key": key, "op": op}
    if "value" in raw:
        rule["value"] = raw["value"]
    return rule


def _parse_rules(value: Any, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return [_parse_rule(item, field=f"{field}[{index}]") for index, item in enumerate(value)]


def _slot(value: Any, *, field: str) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        slot = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer or null") from exc
    if slot < 0 or slot > 255:
        raise ValueError(f"{field} must be in the PINE slot range 0..255")
    return slot


def _parse_manifest(path: Path) -> tuple[dict[str, Any], Path, Any, list[dict[str, Any]]]:
    raw = _load_json(path)
    try:
        schema_version = int(raw.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("schema_version must be an integer") from exc
    if schema_version != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version={schema_version}; expected {SCHEMA_VERSION}")

    curriculum_value = str(raw.get("curriculum_manifest") or "").strip()
    if not curriculum_value:
        raise ValueError("curriculum_manifest must be a non-empty path")
    curriculum_path = _resolve(path.parent, curriculum_value)
    curriculum = load_curriculum_manifest(curriculum_path)

    game = str(raw.get("game") or "").strip()
    if game != curriculum.game:
        raise ValueError(
            f"lab game {game!r} does not match curriculum game {curriculum.game!r}"
        )

    _strings(raw.get("expected_game_ids", []), field="expected_game_ids")
    _strings(raw.get("expected_crcs", []), field="expected_crcs")

    values = raw.get("episodes")
    if not isinstance(values, list) or not values:
        raise ValueError("episodes must be a non-empty array")

    known = {challenge.id: challenge for challenge in curriculum.challenges}
    seen: set[str] = set()
    parsed: list[dict[str, Any]] = []
    for index, item in enumerate(values):
        field = f"episodes[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{field} must be an object")
        challenge_id = str(item.get("challenge_id") or "").strip()
        if challenge_id not in known:
            raise ValueError(f"{field}.challenge_id is not present in the curriculum: {challenge_id!r}")
        if challenge_id in seen:
            raise ValueError(f"duplicate episode challenge_id: {challenge_id}")
        seen.add(challenge_id)

        try:
            max_seconds = float(item.get("max_seconds", DEFAULT_MAX_SECONDS))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field}.max_seconds must be numeric") from exc
        if max_seconds <= 0.0 or max_seconds > 3600.0:
            raise ValueError(f"{field}.max_seconds must be >0 and <=3600")

        parsed.append(
            {
                "challenge_id": challenge_id,
                "required": bool(item.get("required", known[challenge_id].required)),
                "pine_slot": _slot(item.get("pine_slot"), field=f"{field}.pine_slot"),
                "max_seconds": max_seconds,
                "success_rules": _parse_rules(
                    item.get("success_rules", []), field=f"{field}.success_rules"
                ),
                "failure_rules": _parse_rules(
                    item.get("failure_rules", []), field=f"{field}.failure_rules"
                ),
                "notes": str(item.get("notes") or "").strip(),
            }
        )

    missing_required = [
        challenge.id
        for challenge in curriculum.challenges
        if challenge.required and challenge.id not in seen
    ]
    if missing_required:
        raise ValueError(f"missing required curriculum episodes: {', '.join(missing_required)}")
    return raw, curriculum_path, curriculum, parsed


def check_manifest(path: Path) -> dict[str, Any]:
    raw, curriculum_path, curriculum, episodes = _parse_manifest(path)
    ids = _strings(raw.get("expected_game_ids", []), field="expected_game_ids")
    crcs = _strings(raw.get("expected_crcs", []), field="expected_crcs")
    identity_ready = bool(ids or crcs)

    curriculum_report = check_curriculum_manifest(curriculum_path)
    curriculum_rows = {row["id"]: row for row in curriculum_report["challenges"]}

    configured_slots = [item["pine_slot"] for item in episodes if item["pine_slot"] is not None]
    duplicate_slots = sorted({slot for slot in configured_slots if configured_slots.count(slot) > 1})

    rows: list[dict[str, Any]] = []
    required_total = 0
    required_ready = 0
    for episode in episodes:
        challenge = next(item for item in curriculum.challenges if item.id == episode["challenge_id"])
        curriculum_row = curriculum_rows[challenge.id]
        blockers: list[str] = []
        if not identity_ready:
            blockers.append("exact-game-identity-not-configured")
        if not curriculum_row["ready"]:
            blockers.append(curriculum_row["status"])
        if episode["pine_slot"] is None:
            blockers.append("pine-slot-not-configured")
        elif episode["pine_slot"] in duplicate_slots:
            blockers.append("pine-slot-duplicated")
        if not episode["success_rules"]:
            blockers.append("success-predicate-not-configured")

        ready = not blockers
        if episode["required"]:
            required_total += 1
            required_ready += int(ready)
        rows.append({**episode, "ready": ready, "blockers": blockers})

    return {
        "schema": "jak-deterministic-lab-v1",
        "manifest": str(path),
        "game": curriculum.game,
        "curriculum_manifest": str(curriculum_path),
        "identity_ready": identity_ready,
        "duplicate_slots": duplicate_slots,
        "required_total": required_total,
        "required_ready": required_ready,
        "ready": bool(required_total > 0 and required_ready == required_total),
        "episodes": rows,
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rule_result(rule: dict[str, Any], baseline: dict[str, Any], states: list[dict[str, Any]]) -> dict[str, Any]:
    key = rule["key"]
    op = rule["op"]
    final_value = states[-1].get(key)
    baseline_value = baseline.get(key)
    observed = [state.get(key) for state in states if key in state]
    passed = False

    if op == "increase":
        before = _number(baseline_value)
        numeric = [_number(value) for value in observed]
        numeric = [value for value in numeric if value is not None]
        passed = bool(before is not None and numeric and max(numeric) > before)
    elif op == "equals":
        passed = key in states[-1] and final_value == rule.get("value")
    elif op == "not_equals":
        passed = key in states[-1] and final_value != rule.get("value")

    return {
        **rule,
        "passed": passed,
        "baseline": baseline_value,
        "final": final_value,
        "observed_samples": len(observed),
    }


def _utc_timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _trace_states(path: Path) -> tuple[list[dict[str, Any]], float | None]:
    rows = read_jsonl(path)
    states: list[dict[str, Any]] = []
    timestamps: list[float] = []
    for row in rows:
        state = row.get("state") if isinstance(row.get("state"), dict) else row
        if isinstance(state, dict):
            states.append(state)
            timestamp = _utc_timestamp(row.get("utc"))
            if timestamp is not None:
                timestamps.append(timestamp)
    duration = None
    if len(timestamps) >= 2:
        duration = max(0.0, max(timestamps) - min(timestamps))
    return states, duration


def grade_trace(manifest_path: Path, challenge_id: str, trace_path: Path) -> dict[str, Any]:
    _raw, _curriculum_path, _curriculum, episodes = _parse_manifest(manifest_path)
    episode = next((item for item in episodes if item["challenge_id"] == challenge_id), None)
    if episode is None:
        raise ValueError(f"challenge_id not found in lab manifest: {challenge_id}")
    if not episode["success_rules"]:
        raise ValueError(f"challenge {challenge_id}: no success predicate configured")

    states, duration = _trace_states(trace_path)
    if not states:
        return {
            "schema": "jak-lab-grade-v1",
            "challenge_id": challenge_id,
            "trace": str(trace_path),
            "passed": False,
            "blockers": ["no-telemetry"],
            "samples": 0,
        }

    baseline = states[0]
    success = [_rule_result(rule, baseline, states) for rule in episode["success_rules"]]
    failures = [_rule_result(rule, baseline, states) for rule in episode["failure_rules"]]
    blockers: list[str] = []
    if duration is None:
        blockers.append("trace-duration-unknown")
    elif duration > episode["max_seconds"]:
        blockers.append("episode-time-budget-exceeded")
    if not all(item["passed"] for item in success):
        blockers.append("success-predicate-not-met")
    if any(item["passed"] for item in failures):
        blockers.append("failure-predicate-triggered")

    return {
        "schema": "jak-lab-grade-v1",
        "challenge_id": challenge_id,
        "trace": str(trace_path),
        "passed": not blockers,
        "blockers": blockers,
        "samples": len(states),
        "duration_seconds": None if duration is None else round(duration, 3),
        "maximum_seconds": episode["max_seconds"],
        "success_rules": success,
        "failure_rules": failures,
    }


def _write_json(path: Path, value: dict[str, Any], *, force: bool) -> None:
    if path.exists() and not force:
        raise ValueError(f"refusing to overwrite existing file: {path}; use --force")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _print(value: dict[str, Any]) -> None:
    print(json.dumps(value, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ps2-autopilot-jak-lab",
        description="Build, validate, and grade deterministic Jak skill-lab episode contracts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    template = subparsers.add_parser("template")
    template.add_argument("curriculum", type=Path)
    template.add_argument("--output", type=Path)
    template.add_argument("--force", action="store_true")

    check = subparsers.add_parser("check")
    check.add_argument("manifest", type=Path)

    grade = subparsers.add_parser("grade")
    grade.add_argument("manifest", type=Path)
    grade.add_argument("challenge_id")
    grade.add_argument("trace", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "template":
            value = build_template(str(args.curriculum))
            if args.output is None:
                _print(value)
            else:
                _write_json(args.output, value, force=args.force)
                _print({"written": str(args.output), "ready": False})
            return 0
        if args.command == "check":
            report = check_manifest(args.manifest)
            _print(report)
            return 0 if report["ready"] else 1
        report = grade_trace(args.manifest, args.challenge_id, args.trace)
        _print(report)
        return 0 if report["passed"] else 1
    except ValueError as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
