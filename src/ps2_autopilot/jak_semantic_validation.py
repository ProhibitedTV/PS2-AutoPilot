from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path
from typing import Any, Iterable

from .jak_benchmark import _records


POSITION_KEYS = ("jak_x", "jak_y", "jak_z")
VELOCITY_KEYS = ("jak_vx", "jak_vy", "jak_vz")
COUNTER_KEYS: dict[str, tuple[str, ...]] = {
    "power_cells": ("power_cells", "jak_power_cells"),
    "precursor_orbs": ("precursor_orbs", "jak_precursor_orbs"),
    "scout_flies": ("scout_flies", "jak_scout_flies"),
}
CONTACT_KEYS = (
    "jak_grounded",
    "jak_on_ground",
    "player_grounded",
    "grounded",
    "jak_contact",
    "player_contact",
)


def _finite_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _vector(row: dict[str, Any], keys: tuple[str, str, str]) -> tuple[float, float, float] | None:
    values = tuple(_finite_number(row.get(key)) for key in keys)
    if any(value is None for value in values):
        return None
    return float(values[0]), float(values[1]), float(values[2])


def _counter_value(row: dict[str, Any], aliases: tuple[str, ...]) -> int | None:
    for key in aliases:
        if key not in row:
            continue
        try:
            return int(row[key])
        except (TypeError, ValueError):
            return None
    return None


def _boolish(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "yes", "grounded", "contact", "1"}:
        return True
    if text in {"false", "no", "airborne", "none", "0"}:
        return False
    return None


def _trusted(row: dict[str, Any]) -> tuple[bool, str]:
    if row.get("pine_available") is not True:
        return False, "pine-unavailable"
    if row.get("pine_verified") is not True:
        return False, "identity-unverified"
    if row.get("pine_stale") is True:
        return False, "pine-stale"
    if row.get("pine_schema_verified") is not True:
        return False, "schema-unverified"
    return True, "trusted"


def _criterion(passed: bool, evidence: str, **details: Any) -> dict[str, Any]:
    return {"passed": bool(passed), "evidence": evidence, **details}


def _movement_span(positions: Iterable[tuple[float, float, float]]) -> float:
    values = list(positions)
    if not values:
        return 0.0
    mins = [min(item[index] for item in values) for index in range(3)]
    maxs = [max(item[index] for item in values) for index in range(3)]
    return math.sqrt(sum((maximum - minimum) ** 2 for minimum, maximum in zip(mins, maxs)))


def _max_speed(velocities: Iterable[tuple[float, float, float]]) -> float:
    maximum = 0.0
    for velocity in velocities:
        maximum = max(maximum, math.sqrt(sum(component * component for component in velocity)))
    return maximum


def _max_step(positions: list[tuple[float, float, float]]) -> float:
    maximum = 0.0
    for previous, current in zip(positions, positions[1:]):
        maximum = max(
            maximum,
            math.sqrt(sum((b - a) ** 2 for a, b in zip(previous, current))),
        )
    return maximum


def validate(
    path: Path,
    *,
    min_verified_samples: int = 5,
    min_movement_meters: float = 0.25,
    min_speed_mps: float = 0.01,
    max_step_meters: float = 250.0,
    expected_game_id: str | None = None,
    expected_crc: str | None = None,
) -> dict[str, Any]:
    min_verified_samples = max(2, int(min_verified_samples))
    min_movement_meters = max(0.0, float(min_movement_meters))
    min_speed_mps = max(0.0, float(min_speed_mps))
    max_step_meters = max(0.01, float(max_step_meters))

    all_rows = list(_records(path))
    rejected: Counter[str] = Counter()
    trusted_rows: list[dict[str, Any]] = []
    for row in all_rows:
        okay, reason = _trusted(row)
        if okay:
            trusted_rows.append(row)
        else:
            rejected[reason] += 1

    identities = {
        (str(row.get("pine_game_id") or "").upper(), str(row.get("pine_game_crc") or "").lower())
        for row in trusted_rows
        if row.get("pine_game_id") or row.get("pine_game_crc")
    }
    identities.discard(("", ""))
    stable_identity = len(identities) == 1
    identity = next(iter(identities), ("", ""))
    identity_matches = stable_identity and bool(identity[0] and identity[1])
    if expected_game_id:
        identity_matches = identity_matches and identity[0] == expected_game_id.strip().upper()
    if expected_crc:
        identity_matches = identity_matches and identity[1] == expected_crc.strip().lower()

    positions = [value for row in trusted_rows if (value := _vector(row, POSITION_KEYS)) is not None]
    velocities = [value for row in trusted_rows if (value := _vector(row, VELOCITY_KEYS)) is not None]
    span = _movement_span(positions)
    max_speed = _max_speed(velocities)
    max_step = _max_step(positions)

    counters: dict[str, dict[str, Any]] = {}
    counters_ok = True
    for name, aliases in COUNTER_KEYS.items():
        values = [value for row in trusted_rows if (value := _counter_value(row, aliases)) is not None]
        nonnegative = all(value >= 0 for value in values)
        monotonic = all(current >= previous for previous, current in zip(values, values[1:]))
        enough = len(values) >= min_verified_samples
        passed = bool(enough and nonnegative and monotonic)
        counters_ok = counters_ok and passed
        counters[name] = {
            "passed": passed,
            "samples": len(values),
            "minimum": min(values) if values else None,
            "maximum": max(values) if values else None,
            "nonnegative": nonnegative,
            "monotonic": monotonic,
        }

    contact_key = None
    contact_values: list[bool] = []
    for key in CONTACT_KEYS:
        values = [value for row in trusted_rows if (value := _boolish(row.get(key))) is not None]
        if values:
            contact_key = key
            contact_values = values
            break
    contact_present = bool(contact_key and len(contact_values) >= min_verified_samples)
    contact_transition = bool(contact_present and len(set(contact_values)) >= 2)

    sample_count_ok = len(trusted_rows) >= min_verified_samples
    positions_ok = len(positions) >= min_verified_samples and span >= min_movement_meters
    velocity_ok = len(velocities) >= min_verified_samples and max_speed >= min_speed_mps
    steps_ok = len(positions) >= 2 and max_step <= max_step_meters

    criteria = {
        "verified_samples": _criterion(
            sample_count_ok,
            f"trusted={len(trusted_rows)} required={min_verified_samples}",
            trusted=len(trusted_rows),
            required=min_verified_samples,
        ),
        "stable_identity": _criterion(
            identity_matches,
            f"observed={sorted(identities)}",
            identities=[list(item) for item in sorted(identities)],
        ),
        "xyz_motion": _criterion(
            positions_ok,
            f"samples={len(positions)} span={span:.3f}m required>={min_movement_meters:.3f}m",
            samples=len(positions),
            span_meters=round(span, 6),
        ),
        "velocity_motion": _criterion(
            velocity_ok,
            f"samples={len(velocities)} max_speed={max_speed:.3f} required>={min_speed_mps:.3f}",
            samples=len(velocities),
            max_speed=round(max_speed, 6),
        ),
        "position_step_sanity": _criterion(
            steps_ok,
            f"max_step={max_step:.3f}m limit={max_step_meters:.3f}m",
            max_step_meters=round(max_step, 6),
        ),
        "progression_counters": _criterion(
            counters_ok,
            "all three counters present, nonnegative, and monotonic",
            counters=counters,
        ),
        "contact_field": _criterion(
            contact_present,
            (
                f"field={contact_key} samples={len(contact_values)}"
                if contact_key
                else "no grounded/contact field observed"
            ),
            field=contact_key,
            samples=len(contact_values),
        ),
        "contact_transition": _criterion(
            contact_transition,
            (
                f"field={contact_key} states={sorted(set(contact_values))}"
                if contact_key
                else "no grounded/contact field observed"
            ),
            states=sorted(set(contact_values)),
        ),
    }

    core_keys = (
        "verified_samples",
        "stable_identity",
        "xyz_motion",
        "velocity_motion",
        "position_step_sanity",
        "progression_counters",
    )
    core_passed = all(criteria[key]["passed"] for key in core_keys)
    strict_passed = bool(
        core_passed
        and criteria["contact_field"]["passed"]
        and criteria["contact_transition"]["passed"]
    )

    return {
        "file": str(path),
        "passed": strict_passed,
        "core_passed": core_passed,
        "total_rows": len(all_rows),
        "trusted_rows": len(trusted_rows),
        "rejected_rows": dict(sorted(rejected.items())),
        "criteria": criteria,
        "guidance": (
            "Strict pass requires verified PINE identity/schema, real XYZ movement, nonzero velocity, "
            "sane monotonic progression counters, and a grounded/contact field observed in both "
            "grounded and airborne/contact states."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ps2-autopilot-jak-semantic-check",
        description="Validate live Jak PINE movement/contact/progression telemetry from verbose.jsonl.",
    )
    parser.add_argument("path", nargs="?", default="runtime/verbose.jsonl", type=Path)
    parser.add_argument("--min-samples", type=int, default=5)
    parser.add_argument("--min-movement", type=float, default=0.25)
    parser.add_argument("--min-speed", type=float, default=0.01)
    parser.add_argument("--max-step", type=float, default=250.0)
    parser.add_argument("--expected-game-id")
    parser.add_argument("--expected-crc")
    parser.add_argument(
        "--core-only",
        action="store_true",
        help="exit successfully after XYZ/velocity/counter validation even if contact telemetry is absent",
    )
    args = parser.parse_args(argv)
    if not args.path.exists():
        parser.error(f"file not found: {args.path}")

    report = validate(
        args.path,
        min_verified_samples=args.min_samples,
        min_movement_meters=args.min_movement,
        min_speed_mps=args.min_speed,
        max_step_meters=args.max_step,
        expected_game_id=args.expected_game_id,
        expected_crc=args.expected_crc,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if (report["core_passed"] if args.core_only else report["passed"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
