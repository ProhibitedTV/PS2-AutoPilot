from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any, Iterable


def _records(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def summarize(path: Path) -> dict[str, Any]:
    actions: Counter[str] = Counter()
    stages: Counter[str] = Counter()
    policies: Counter[str] = Counter()
    samples = 0
    max_completion = 0
    max_cells = max_orbs = max_flies = None
    max_position_buckets = 0
    max_no_progress = 0.0
    progress_events = replans = water_uturns = shore_hops = 0
    local_stuck_triggers = local_stuck_successes = 0
    scout_attempts = roll_jump_attempts = spin_attacks = 0
    first_ts = last_ts = None

    for row in _records(path):
        samples += 1
        action = str(row.get("action") or "")
        if action:
            head = action.split(";", 1)[0]
            actions[head] += 1
        stage = row.get("jak_objective_stage")
        if stage:
            stages[str(stage)] += 1
        policy = row.get("jak_policy_version")
        if policy:
            policies[str(policy)] += 1

        def max_int(current: int | None, key: str) -> int | None:
            value = row.get(key)
            if value is None:
                return current
            try:
                value = int(value)
            except (TypeError, ValueError):
                return current
            return value if current is None else max(current, value)

        max_cells = max_int(max_cells, "jak_goal_cells_delta")
        max_orbs = max_int(max_orbs, "jak_goal_orbs_delta")
        max_flies = max_int(max_flies, "jak_goal_flies_delta")
        max_completion = max(max_completion, int(row.get("jak_goal_completion_percent") or 0))
        max_position_buckets = max(max_position_buckets, int(row.get("jak_distinct_position_buckets") or 0))
        max_no_progress = max(max_no_progress, float(row.get("jak_goal_no_progress_age") or 0.0))
        progress_events = max(progress_events, int(row.get("jak_goal_progress_events") or 0))
        replans = max(replans, int(row.get("jak_goal_replans") or 0))
        water_uturns = max(water_uturns, int(row.get("jak_water_uturns") or 0))
        shore_hops = max(shore_hops, int(row.get("jak_water_shore_hops") or 0))
        local_stuck_triggers = max(local_stuck_triggers, int(row.get("jak_local_stuck_triggers") or 0))
        local_stuck_successes = max(local_stuck_successes, int(row.get("jak_local_stuck_successes") or 0))
        scout_attempts = max(scout_attempts, int(row.get("jak_scout_dive_attempts") or 0))
        roll_jump_attempts = max(roll_jump_attempts, int(row.get("jak_roll_jump_attempts") or 0))
        spin_attacks = max(spin_attacks, int(row.get("jak_moving_spin_attacks") or 0))

        ts = row.get("timestamp")
        if isinstance(ts, (int, float)):
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)

    duration = None if first_ts is None or last_ts is None else max(0.0, last_ts - first_ts)
    return {
        "file": str(path),
        "samples": samples,
        "duration_seconds": duration,
        "policy_samples": policies.most_common(),
        "objective_stage_samples": stages.most_common(),
        "max_completion_percent": max_completion,
        "max_geyser_cells": max_cells,
        "max_geyser_orbs": max_orbs,
        "max_geyser_scout_flies": max_flies,
        "objective_progress_events": progress_events,
        "objective_replans": replans,
        "max_no_progress_seconds": round(max_no_progress, 2),
        "distinct_position_buckets": max_position_buckets,
        "water_uturns": water_uturns,
        "water_shore_hops": shore_hops,
        "local_stuck_triggers": local_stuck_triggers,
        "local_stuck_successes": local_stuck_successes,
        "scout_dive_attempts": scout_attempts,
        "roll_jump_attempts": roll_jump_attempts,
        "moving_spin_attacks": spin_attacks,
        "top_actions": actions.most_common(20),
        "graduated": bool(max_completion >= 100 or stages.get("complete", 0)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ps2-autopilot-jak-report",
        description="Summarize a Jak verbose.jsonl soak into objective/skill metrics.",
    )
    parser.add_argument("path", nargs="?", default="runtime/verbose.jsonl")
    args = parser.parse_args(argv)
    path = Path(args.path)
    if not path.exists():
        parser.error(f"file not found: {path}")
    print(json.dumps(summarize(path), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
