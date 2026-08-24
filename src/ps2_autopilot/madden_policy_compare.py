from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Literal


REPORT_SCHEMA = "madden-policy-quality-v1"
Preference = Literal["higher", "lower", "diagnostic"]


@dataclass(frozen=True)
class MetricSpec:
    name: str
    path: tuple[str, ...]
    preference: Preference
    unit: str


DIRECT_METRICS = (
    MetricSpec("play_completion_pct", ("football", "play_completion_pct"), "higher", "pct"),
    MetricSpec("unknown_possession_pct", ("live_quality", "unknown_possession_pct"), "lower", "pct"),
    MetricSpec("spatial_available_pct", ("live_quality", "spatial_available_pct"), "higher", "pct"),
    MetricSpec(
        "controlled_confidence_ge_050_pct",
        ("live_quality", "controlled_confidence_ge_050_pct"),
        "higher",
        "pct",
    ),
    MetricSpec(
        "target_confidence_ge_050_pct",
        ("live_quality", "target_confidence_ge_050_pct"),
        "higher",
        "pct",
    ),
    MetricSpec(
        "ball_confidence_ge_050_pct",
        ("live_quality", "ball_confidence_ge_050_pct"),
        "higher",
        "pct",
    ),
    MetricSpec(
        "open_space_confidence_ge_050_pct",
        ("live_quality", "open_space_confidence_ge_050_pct"),
        "higher",
        "pct",
    ),
    MetricSpec(
        "unresolved_navigation_pct",
        ("runtime", "unresolved_navigation_pct"),
        "lower",
        "pct",
    ),
    MetricSpec(
        "defense_contact_authorized_pct",
        ("defense", "contact_authorized_pct_of_classified_ticks"),
        "diagnostic",
        "pct",
    ),
)


class PolicyReportError(ValueError):
    pass


def _load(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PolicyReportError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PolicyReportError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyReportError(f"{path}: report root must be an object")
    if raw.get("schema") != REPORT_SCHEMA:
        raise PolicyReportError(
            f"{path}: unsupported schema {raw.get('schema')!r}; expected {REPORT_SCHEMA!r}"
        )
    return raw


def _get(report: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = report
    for part in path:
        if not isinstance(current, dict) or part not in current:
            raise PolicyReportError("report missing required metric: " + ".".join(path))
        current = current[part]
    return current


def _number(report: dict[str, Any], path: tuple[str, ...]) -> float:
    value = _get(report, path)
    if isinstance(value, bool):
        raise PolicyReportError("metric must be numeric: " + ".".join(path))
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise PolicyReportError("metric must be numeric: " + ".".join(path)) from exc


def _safe_rate(numerator: float, denominator: float, scale: float = 1.0) -> float | None:
    if denominator <= 0.0:
        return None
    return numerator / denominator * scale


def _derived(report: dict[str, Any]) -> dict[str, tuple[float | None, Preference, str]]:
    duration = _number(report, ("runtime", "duration_seconds"))
    hours = duration / 3600.0
    games = _number(report, ("runtime", "games_completed"))
    live_samples = _number(report, ("live_samples",))

    hard = _number(report, ("runtime", "hard_recoveries"))
    semantic = _number(report, ("runtime", "semantic_recoveries"))
    unknown_captures = _number(report, ("runtime", "unknown_captures"))
    failure_bundles = _number(report, ("runtime", "failure_bundles"))
    scoring = _number(report, ("football", "scoring_events"))
    turnovers = _number(report, ("football", "turnover_events"))
    unknown_kicks = _number(report, ("special_teams", "unknown_kicking_ticks"))
    scoring_ambiguities = _number(report, ("special_teams", "scoring_ambiguities"))

    return {
        "hard_recoveries_per_hour": (_safe_rate(hard, hours), "lower", "per_hour"),
        "semantic_recoveries_per_hour": (_safe_rate(semantic, hours), "lower", "per_hour"),
        "unknown_captures_per_hour": (_safe_rate(unknown_captures, hours), "lower", "per_hour"),
        "failure_bundles_per_hour": (_safe_rate(failure_bundles, hours), "lower", "per_hour"),
        "scoring_events_per_completed_game": (_safe_rate(scoring, games), "diagnostic", "per_game"),
        "turnover_events_per_completed_game": (
            _safe_rate(turnovers, games),
            "diagnostic",
            "per_game",
        ),
        "unknown_kicking_ticks_per_1000_live_samples": (
            _safe_rate(unknown_kicks, live_samples, 1000.0),
            "lower",
            "per_1000_live_samples",
        ),
        "scoring_ambiguities_per_1000_live_samples": (
            _safe_rate(scoring_ambiguities, live_samples, 1000.0),
            "lower",
            "per_1000_live_samples",
        ),
    }


def _movement(
    baseline: float | None,
    candidate: float | None,
    preference: Preference,
    tolerance: float,
) -> tuple[float | None, str]:
    if baseline is None or candidate is None:
        return None, "not-comparable"
    delta = candidate - baseline
    if abs(delta) <= tolerance:
        return delta, "unchanged"
    if preference == "diagnostic":
        return delta, "up" if delta > 0.0 else "down"
    improved = delta > 0.0 if preference == "higher" else delta < 0.0
    return delta, "improved" if improved else "regressed"


def compare_reports(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    tolerance: float = 0.0,
) -> dict[str, Any]:
    tolerance = max(0.0, float(tolerance))
    rows: list[dict[str, Any]] = []

    for spec in DIRECT_METRICS:
        before = _number(baseline, spec.path)
        after = _number(candidate, spec.path)
        delta, movement = _movement(before, after, spec.preference, tolerance)
        rows.append(
            {
                "metric": spec.name,
                "baseline": round(before, 6),
                "candidate": round(after, 6),
                "delta": None if delta is None else round(delta, 6),
                "preference": spec.preference,
                "movement": movement,
                "unit": spec.unit,
            }
        )

    before_derived = _derived(baseline)
    after_derived = _derived(candidate)
    for name in before_derived:
        before, preference, unit = before_derived[name]
        after, after_preference, after_unit = after_derived[name]
        if after_preference != preference or after_unit != unit:
            raise PolicyReportError(f"internal metric contract mismatch for {name}")
        delta, movement = _movement(before, after, preference, tolerance)
        rows.append(
            {
                "metric": name,
                "baseline": None if before is None else round(before, 6),
                "candidate": None if after is None else round(after, 6),
                "delta": None if delta is None else round(delta, 6),
                "preference": preference,
                "movement": movement,
                "unit": unit,
            }
        )

    directional = [row for row in rows if row["preference"] != "diagnostic"]
    improved = sum(row["movement"] == "improved" for row in directional)
    regressed = sum(row["movement"] == "regressed" for row in directional)
    unchanged = sum(row["movement"] == "unchanged" for row in directional)
    incomparable = sum(row["movement"] == "not-comparable" for row in directional)

    return {
        "schema": "madden-policy-comparison-v1",
        "baseline_policy_versions": list(baseline.get("policy_versions") or []),
        "candidate_policy_versions": list(candidate.get("policy_versions") or []),
        "tolerance": tolerance,
        "summary": {
            "directional_metrics": len(directional),
            "improved": improved,
            "regressed": regressed,
            "unchanged": unchanged,
            "not_comparable": incomparable,
            "overall_verdict": "not-scored",
        },
        "metrics": rows,
        "note": (
            "Directional labels are per-metric diagnostics, not an overall policy score. "
            "Raw football outcomes and contact cadence remain diagnostic because current "
            "telemetry does not always identify which team caused each event."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ps2-autopilot-madden-policy-compare",
        description="Compare two Madden policy-quality reports without inventing a composite score.",
    )
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.0,
        help="absolute delta treated as unchanged for directional metrics",
    )
    args = parser.parse_args(argv)

    try:
        report = compare_reports(
            _load(args.baseline),
            _load(args.candidate),
            tolerance=args.tolerance,
        )
    except PolicyReportError as exc:
        parser.error(str(exc))
        return 2

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
