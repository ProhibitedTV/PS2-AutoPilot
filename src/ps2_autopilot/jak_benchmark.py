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
    visual_goals: Counter[str] = Counter()
    modes: Counter[str] = Counter()
    samples = 0
    max_completion = 0
    max_cells = max_orbs = max_flies = None
    max_position_buckets = 0
    max_no_progress = 0.0
    progress_events = replans = executed_replans = water_uturns = shore_hops = 0
    local_stuck_triggers = local_stuck_successes = 0
    scout_attempts = roll_jump_attempts = spin_attacks = 0
    goal_acquisitions = goal_pursuit_ticks = goal_scan_biases = 0
    orb_goal_cues = cell_goal_cues = 0
    ledge_attempts = ledge_double_attempts = ledge_successes = ledge_failures = 0
    target_stalls = target_blacklists = target_bypasses = target_jumps = target_progress = 0
    mobility_attempts = mobility_successes = mobility_failures = mobility_double = 0
    shoreline_ticks = shoreline_entries = shore_exit_commits = 0
    specialist_recoveries = zoomer_hops = zoomer_brakes = flut_flutters = 0
    cannon_shots = fishing_tracks = fishing_no_target = 0
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
        visual_goal = row.get("jak_visual_goal")
        if visual_goal and visual_goal != "none":
            visual_goals[str(visual_goal)] += 1
        mode = row.get("jak_control_mode") or row.get("jak_semantic_mode_hint")
        if mode:
            modes[str(mode)] += 1

        def max_int(current: int | None, key: str) -> int | None:
            value = row.get(key)
            if value is None:
                return current
            try:
                value = int(value)
            except (TypeError, ValueError):
                return current
            return value if current is None else max(current, value)

        def max_counter(current: int, key: str) -> int:
            try:
                return max(current, int(row.get(key) or 0))
            except (TypeError, ValueError):
                return current

        max_cells = max_int(max_cells, "jak_goal_cells_delta")
        max_orbs = max_int(max_orbs, "jak_goal_orbs_delta")
        max_flies = max_int(max_flies, "jak_goal_flies_delta")
        max_completion = max(max_completion, int(row.get("jak_goal_completion_percent") or 0))
        max_position_buckets = max(max_position_buckets, int(row.get("jak_distinct_position_buckets") or 0))
        max_no_progress = max(max_no_progress, float(row.get("jak_goal_no_progress_age") or 0.0))
        progress_events = max_counter(progress_events, "jak_goal_progress_events")
        replans = max_counter(replans, "jak_goal_replans")
        executed_replans = max_counter(executed_replans, "jak_executed_objective_replans")
        water_uturns = max_counter(water_uturns, "jak_water_uturns")
        shore_hops = max_counter(shore_hops, "jak_water_shore_hops")
        local_stuck_triggers = max_counter(local_stuck_triggers, "jak_local_stuck_triggers")
        local_stuck_successes = max_counter(local_stuck_successes, "jak_local_stuck_successes")
        scout_attempts = max_counter(scout_attempts, "jak_scout_dive_attempts")
        roll_jump_attempts = max_counter(roll_jump_attempts, "jak_roll_jump_attempts")
        spin_attacks = max_counter(spin_attacks, "jak_moving_spin_attacks")
        goal_acquisitions = max_counter(goal_acquisitions, "jak_visual_goal_acquisitions")
        goal_pursuit_ticks = max_counter(goal_pursuit_ticks, "jak_visual_goal_pursuit_ticks")
        goal_scan_biases = max_counter(goal_scan_biases, "jak_goal_scan_biases")
        orb_goal_cues = max_counter(orb_goal_cues, "jak_orb_goal_cues")
        cell_goal_cues = max_counter(cell_goal_cues, "jak_cell_goal_cues")
        ledge_attempts = max_counter(ledge_attempts, "jak_ledge_jump_attempts")
        ledge_double_attempts = max_counter(ledge_double_attempts, "jak_ledge_jump_double_attempts")
        ledge_successes = max_counter(ledge_successes, "jak_ledge_jump_successes")
        ledge_failures = max_counter(ledge_failures, "jak_ledge_jump_failures")

        target_stalls = max_counter(target_stalls, "jak_target_stalls")
        target_blacklists = max_counter(target_blacklists, "jak_target_blacklists")
        target_bypasses = max_counter(target_bypasses, "jak_target_bypasses")
        target_jumps = max_counter(target_jumps, "jak_target_jump_resolutions")
        target_progress = max_counter(target_progress, "jak_target_progress_events")
        mobility_attempts = max_counter(mobility_attempts, "jak_mobility_attempts")
        mobility_successes = max_counter(mobility_successes, "jak_mobility_successes")
        mobility_failures = max_counter(mobility_failures, "jak_mobility_failures")
        mobility_double = max_counter(mobility_double, "jak_mobility_double_jumps")
        shoreline_ticks = max_counter(shoreline_ticks, "jak_shoreline_guard_ticks")
        shoreline_entries = max_counter(shoreline_entries, "jak_shoreline_entries")
        shore_exit_commits = max_counter(shore_exit_commits, "jak_shore_exit_commits")
        specialist_recoveries = max_counter(specialist_recoveries, "jak_specialist_recoveries")
        zoomer_hops = max_counter(zoomer_hops, "jak_zoomer_hops")
        zoomer_brakes = max_counter(zoomer_brakes, "jak_zoomer_brakes")
        flut_flutters = max_counter(flut_flutters, "jak_flut_flutters")
        cannon_shots = max_counter(cannon_shots, "jak_cannon_shots")
        fishing_tracks = max_counter(fishing_tracks, "jak_fishing_tracks")
        fishing_no_target = max_counter(fishing_no_target, "jak_fishing_no_target")

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
        "control_mode_samples": modes.most_common(),
        "objective_stage_samples": stages.most_common(),
        "visual_goal_samples": visual_goals.most_common(),
        "max_completion_percent": max_completion,
        "max_geyser_cells": max_cells,
        "max_geyser_orbs": max_orbs,
        "max_geyser_scout_flies": max_flies,
        "objective_progress_events": progress_events,
        "objective_replans": replans,
        "executed_objective_replans": executed_replans,
        "max_no_progress_seconds": round(max_no_progress, 2),
        "distinct_position_buckets": max_position_buckets,
        "visual_goal_acquisitions": goal_acquisitions,
        "visual_goal_pursuit_ticks": goal_pursuit_ticks,
        "goal_scan_biases": goal_scan_biases,
        "orb_goal_cues": orb_goal_cues,
        "cell_goal_cues": cell_goal_cues,
        "ledge_jump_attempts": ledge_attempts,
        "ledge_double_jump_attempts": ledge_double_attempts,
        "ledge_jump_successes": ledge_successes,
        "ledge_jump_failures": ledge_failures,
        "target_stalls": target_stalls,
        "target_blacklists": target_blacklists,
        "target_bypasses": target_bypasses,
        "target_jump_resolutions": target_jumps,
        "target_progress_events": target_progress,
        "mobility_attempts": mobility_attempts,
        "mobility_successes": mobility_successes,
        "mobility_failures": mobility_failures,
        "mobility_double_jumps": mobility_double,
        "shoreline_guard_ticks": shoreline_ticks,
        "shoreline_entries": shoreline_entries,
        "shore_exit_commits": shore_exit_commits,
        "water_uturns": water_uturns,
        "water_shore_hops": shore_hops,
        "local_stuck_triggers": local_stuck_triggers,
        "local_stuck_successes": local_stuck_successes,
        "scout_dive_attempts": scout_attempts,
        "roll_jump_attempts": roll_jump_attempts,
        "moving_spin_attacks": spin_attacks,
        "specialist_recoveries": specialist_recoveries,
        "zoomer_hops": zoomer_hops,
        "zoomer_brakes": zoomer_brakes,
        "flut_flutters": flut_flutters,
        "cannon_shots": cannon_shots,
        "fishing_tracks": fishing_tracks,
        "fishing_no_target": fishing_no_target,
        "top_actions": actions.most_common(25),
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
