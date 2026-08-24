from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any, Iterable

from .runtime_evidence import build_soak_report, discover_sessions, overall_metrics, read_log_series


MADDEN_PROFILE = "madden2005"
MADDEN_GAME = "Madden NFL 2005"

COUNTER_KEYS = (
    "plays_started",
    "plays_completed",
    "pass_attempts",
    "tackle_attempts",
    "kicks",
    "defense_action_holds",
    "defense_uncertain_ticks",
    "defense_uncertain_sprints",
    "defense_contact_authorized_ticks",
    "defense_contact_suppressed_ticks",
    "special_return_holds",
    "special_return_sprints",
    "special_teams_handoffs",
    "special_teams_recognitions",
    "special_teams_unknown_kicking_ticks",
    "special_teams_scoring_ambiguities",
)

EVENT_KEYS = (
    "touchdown",
    "interception",
    "fumble",
    "first_down",
    "incomplete",
    "field_goal",
    "penalty",
    "sack",
    "punt",
    "kickoff",
)


def _states(root: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in read_log_series(root, "verbose.jsonl"):
        state = row.get("state") if isinstance(row.get("state"), dict) else row
        if isinstance(state, dict):
            output.append(state)
    return output


def _is_madden(states: list[dict[str, Any]]) -> bool:
    return any(
        str(state.get("profile") or "") == MADDEN_PROFILE
        or str(state.get("game_display_name") or "") == MADDEN_GAME
        for state in states
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(int(value))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _counter_peak(states: list[dict[str, Any]], key: str) -> int:
    values = [_number(state.get(key)) for state in states if key in state]
    values = [value for value in values if value is not None]
    return int(max(values, default=0.0))


def _event_peaks(states: list[dict[str, Any]]) -> dict[str, int]:
    peaks: dict[str, int] = {key: 0 for key in EVENT_KEYS}
    for state in states:
        raw = state.get("game_event_counts")
        if not isinstance(raw, dict):
            continue
        for key in EVENT_KEYS:
            value = _number(raw.get(key))
            if value is not None:
                peaks[key] = max(peaks[key], int(value))
    return peaks


def _pct(numerator: int | float, denominator: int | float) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator) * 100.0, 3)


def _confidence_rate(states: list[dict[str, Any]], key: str, threshold: float) -> float:
    hits = 0
    for state in states:
        value = _number(state.get(key))
        if value is not None and value >= threshold:
            hits += 1
    return _pct(hits, len(states))


def session_policy_metrics(root: Path, states: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    values = list(states if states is not None else _states(root))
    counters = {key: _counter_peak(values, key) for key in COUNTER_KEYS}
    events = _event_peaks(values)
    live = [state for state in values if str(state.get("phase") or "") == "live"]

    unknown_possession = sum(
        1 for state in live if str(state.get("possession") or "") == "unknown"
    )
    spatial_available = sum(1 for state in live if bool(state.get("spatial_enabled")))
    policy_versions = sorted(
        {
            str(state.get("madden_policy_version") or "").strip()
            for state in values
            if str(state.get("madden_policy_version") or "").strip()
        }
    )

    return {
        "root": str(root),
        "samples": len(values),
        "live_samples": len(live),
        "policy_versions": policy_versions,
        "counters": counters,
        "events": events,
        "live_quality": {
            "unknown_possession_pct": _pct(unknown_possession, len(live)),
            "spatial_available_pct": _pct(spatial_available, len(live)),
            "controlled_confidence_ge_050_pct": _confidence_rate(
                live, "spatial_controlled_confidence", 0.50
            ),
            "target_confidence_ge_050_pct": _confidence_rate(
                live, "spatial_target_confidence", 0.50
            ),
            "ball_confidence_ge_050_pct": _confidence_rate(
                live, "spatial_ball_confidence", 0.50
            ),
            "open_space_confidence_ge_050_pct": _confidence_rate(
                live, "spatial_open_confidence", 0.50
            ),
        },
    }


def build_policy_report(paths: Iterable[str | Path]) -> dict[str, Any]:
    sessions: list[tuple[Path, list[dict[str, Any]]]] = []
    for root in discover_sessions(paths):
        states = _states(root)
        if _is_madden(states):
            sessions.append((root, states))
    if not sessions:
        raise ValueError("no retained Madden sessions found")

    per_session = [session_policy_metrics(root, states) for root, states in sessions]
    counters: Counter[str] = Counter()
    events: Counter[str] = Counter()
    total_live = 0
    weighted_rates: Counter[str] = Counter()
    versions: set[str] = set()
    for item in per_session:
        counters.update(item["counters"])
        events.update(item["events"])
        live_samples = int(item["live_samples"])
        total_live += live_samples
        versions.update(item["policy_versions"])
        for key, value in item["live_quality"].items():
            weighted_rates[key] += float(value) * live_samples

    live_quality = {
        key: round(value / total_live, 3) if total_live else 0.0
        for key, value in weighted_rates.items()
    }
    for key in (
        "unknown_possession_pct",
        "spatial_available_pct",
        "controlled_confidence_ge_050_pct",
        "target_confidence_ge_050_pct",
        "ball_confidence_ge_050_pct",
        "open_space_confidence_ge_050_pct",
    ):
        live_quality.setdefault(key, 0.0)

    plays_started = counters["plays_started"]
    plays_completed = counters["plays_completed"]
    defensive_ticks = (
        counters["defense_uncertain_ticks"] + counters["defense_contact_authorized_ticks"]
    )

    soak = build_soak_report([root for root, _states_value in sessions])
    soak_overall = overall_metrics(soak)

    return {
        "schema": "madden-policy-quality-v1",
        "profile": MADDEN_PROFILE,
        "policy_versions": sorted(versions),
        "session_count": len(per_session),
        "samples": sum(int(item["samples"]) for item in per_session),
        "live_samples": total_live,
        "football": {
            "plays_started": plays_started,
            "plays_completed": plays_completed,
            "play_completion_pct": _pct(plays_completed, plays_started),
            "pass_attempts": counters["pass_attempts"],
            "tackle_attempts": counters["tackle_attempts"],
            "kicks": counters["kicks"],
            "scoring_events": events["touchdown"] + events["field_goal"],
            "turnover_events": events["interception"] + events["fumble"],
            "events": {key: int(events[key]) for key in EVENT_KEYS},
        },
        "live_quality": live_quality,
        "defense": {
            "uncertain_ticks": counters["defense_uncertain_ticks"],
            "uncertain_sprints": counters["defense_uncertain_sprints"],
            "contact_authorized_ticks": counters["defense_contact_authorized_ticks"],
            "contact_suppressed_ticks": counters["defense_contact_suppressed_ticks"],
            "legacy_far_action_holds": counters["defense_action_holds"],
            "contact_authorized_pct_of_classified_ticks": _pct(
                counters["defense_contact_authorized_ticks"], defensive_ticks
            ),
        },
        "special_teams": {
            "recognitions": counters["special_teams_recognitions"],
            "handoffs": counters["special_teams_handoffs"],
            "return_holds": counters["special_return_holds"],
            "return_sprints": counters["special_return_sprints"],
            "unknown_kicking_ticks": counters["special_teams_unknown_kicking_ticks"],
            "scoring_ambiguities": counters["special_teams_scoring_ambiguities"],
        },
        "runtime": {
            "duration_seconds": soak_overall["duration_seconds"],
            "games_started": soak_overall["games_started"],
            "games_completed": soak_overall["games_completed"],
            "hard_recoveries": soak_overall.get("hard_recoveries", 0),
            "semantic_recoveries": soak_overall.get("semantic_recoveries", 0),
            "unknown_captures": soak_overall.get("unknown_captures", 0),
            "failure_bundles": soak_overall.get("failure_bundles", 0),
            "unresolved_navigation_seconds": soak_overall.get(
                "unresolved_navigation_seconds", 0.0
            ),
            "unresolved_navigation_pct": soak_overall["unresolved_navigation_pct"],
        },
        "sessions": per_session,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ps2-autopilot-madden-policy-report",
        description=(
            "Summarize Madden gameplay-policy quality from retained verbose runtime evidence."
        ),
    )
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    try:
        report = build_policy_report(args.paths)
    except ValueError as exc:
        parser.error(str(exc))
        return 2

    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
