from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Iterable

from .observability import read_jsonl
from .runtime_evidence import (
    build_soak_report,
    discover_sessions,
    overall_metrics,
    read_log_series,
    unresolved_reason,
)


DEFAULT_FRESH_BOOT_RUNS = 3
DEFAULT_SOAK_HOURS = 8.0
DEFAULT_SOAK_GAMES = 2
DEFAULT_MAX_UNRESOLVED_PCT = 1.0


def _criterion(passed: bool, evidence: str, **details: Any) -> dict[str, Any]:
    return {"passed": bool(passed), "evidence": evidence, **details}


def _max_int(states: Iterable[dict[str, Any]], key: str) -> int:
    maximum = 0
    for state in states:
        try:
            maximum = max(maximum, int(state.get(key, 0) or 0))
        except (TypeError, ValueError):
            continue
    return maximum


def _fresh_session_evidence(root: Path) -> dict[str, Any]:
    rows = read_log_series(root, "verbose.jsonl")
    states = [row.get("state") for row in rows if isinstance(row.get("state"), dict)]
    states = [state for state in states if isinstance(state, dict)]
    evidence = {
        "samples": len(states),
        "frontend_verified_crosses": _max_int(states, "frontend_verified_crosses"),
        "team_rotation_games": _max_int(states, "team_rotation_games"),
        "team_rotation_steps": _max_int(states, "team_rotation_steps"),
        "controlled_side_moves": _max_int(states, "controlled_side_moves"),
        "games_started": max(
            _max_int(states, "session_games_started"),
            _max_int(states, "games_started"),
        ),
    }
    evidence["passed"] = bool(
        evidence["samples"] > 0
        and evidence["frontend_verified_crosses"] >= 1
        and evidence["team_rotation_games"] >= 1
        and evidence["team_rotation_steps"] >= 2
        and evidence["controlled_side_moves"] >= 1
        and evidence["games_started"] >= 1
    )
    evidence["root"] = str(root)
    return evidence


def evaluate_fresh_boots(
    roots: Iterable[str | Path],
    *,
    asserted: bool,
    required_runs: int = DEFAULT_FRESH_BOOT_RUNS,
) -> dict[str, Any]:
    required_runs = max(2, int(required_runs))
    sessions = discover_sessions(roots)
    evidence = [_fresh_session_evidence(root) for root in sessions]
    passing = sum(1 for item in evidence if item["passed"])
    all_supplied_pass = bool(evidence and all(item["passed"] for item in evidence))
    passed = bool(asserted and len(evidence) >= required_runs and all_supplied_pass)
    return _criterion(
        passed,
        (
            f"fresh_boot_asserted={asserted} passing={passing}/{len(evidence)} "
            f"required={required_runs}"
        ),
        fresh_boot_asserted=asserted,
        required_runs=required_runs,
        supplied_runs=len(evidence),
        passing_runs=passing,
        sessions=evidence,
    )


def _event_time(row: dict[str, Any]) -> float | None:
    text = str(row.get("utc") or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def evaluate_process_death(
    supervisor_log: str | Path,
    *,
    asserted: bool,
) -> dict[str, Any]:
    path = Path(supervisor_log)
    rows = read_jsonl(path)
    sequence: dict[str, dict[str, Any] | None] = {
        "loss": None,
        "ready": None,
        "autopilot_restart": None,
    }
    loss_index: int | None = None
    ready_index: int | None = None

    for index, row in enumerate(rows):
        if loss_index is None:
            if (
                row.get("kind") == "autopilot-exit"
                and row.get("reason") == "pcsx2-window-lost"
                and bool(row.get("supervisor_pcsx2_window_lost"))
            ):
                loss_index = index
                sequence["loss"] = row
            continue
        if ready_index is None and index > loss_index and row.get("kind") == "emulator-window-ready":
            ready_index = index
            sequence["ready"] = row
            continue
        if (
            ready_index is not None
            and index > ready_index
            and row.get("kind") == "autopilot-start"
            and row.get("reason") == "restart"
        ):
            sequence["autopilot_restart"] = row
            break

    ordered = all(sequence.values())
    pids_changed = False
    if ordered:
        loss = sequence["loss"] or {}
        ready = sequence["ready"] or {}
        old_pid = loss.get("supervisor_emulator_pid")
        new_pid = ready.get("supervisor_emulator_pid")
        # The loss event may not carry the old PID on older telemetry. A new ready
        # PID is still positive restart evidence; when both are present they must differ.
        pids_changed = bool(new_pid is not None and (old_pid is None or old_pid != new_pid))
    passed = bool(asserted and ordered and pids_changed)
    return _criterion(
        passed,
        (
            f"process_death_asserted={asserted} ordered_recovery={ordered} "
            f"replacement_pid={pids_changed}"
        ),
        process_death_asserted=asserted,
        supervisor_log=str(path),
        events=len(rows),
        ordered_recovery=ordered,
        replacement_pid=pids_changed,
        sequence=sequence,
    )


def evaluate_soak(
    roots: Iterable[str | Path],
    *,
    min_hours: float = DEFAULT_SOAK_HOURS,
    min_games_completed: int = DEFAULT_SOAK_GAMES,
    max_unresolved_pct: float = DEFAULT_MAX_UNRESOLVED_PCT,
) -> dict[str, Any]:
    # Production defaults remain deliberately strict (8 hours). Do not clamp
    # caller-supplied thresholds upward: tests, short qualification runs, and
    # future staged acceptance profiles need to be able to request smaller windows
    # explicitly without changing evaluator semantics.
    min_hours = max(0.0, float(min_hours))
    min_games_completed = max(1, int(min_games_completed))
    max_unresolved_pct = max(0.0, float(max_unresolved_pct))
    report = build_soak_report(roots)
    aggregate = overall_metrics(report)
    duration = float(aggregate.get("duration_seconds", 0.0) or 0.0)
    hours = duration / 3600.0
    games = int(aggregate.get("games_completed", 0) or 0)
    unresolved_pct = float(aggregate.get("unresolved_navigation_pct", 100.0) or 0.0)

    sessions = discover_sessions(roots)
    final_unresolved: list[dict[str, str]] = []
    for root in sessions:
        rows = read_log_series(root, "verbose.jsonl")
        states = [row.get("state") for row in rows if isinstance(row.get("state"), dict)]
        if not states:
            final_unresolved.append({"root": str(root), "reason": "no-telemetry"})
            continue
        reason = unresolved_reason(states[-1])
        if reason is not None:
            final_unresolved.append({"root": str(root), "reason": reason})

    passed = bool(
        sessions
        and hours >= min_hours
        and games >= min_games_completed
        and unresolved_pct <= max_unresolved_pct
        and not final_unresolved
    )
    return _criterion(
        passed,
        (
            f"duration={hours:.2f}h/{min_hours:.2f}h "
            f"games={games}/{min_games_completed} "
            f"unresolved={unresolved_pct:.3f}%<={max_unresolved_pct:.3f}% "
            f"final_unresolved={len(final_unresolved)}"
        ),
        report_schema_valid=True,
        minimum_hours=min_hours,
        duration_hours=round(hours, 4),
        minimum_games_completed=min_games_completed,
        games_completed=games,
        maximum_unresolved_pct=max_unresolved_pct,
        unresolved_pct=unresolved_pct,
        final_unresolved=final_unresolved,
        soak_report=report,
    )


def evaluate_acceptance(
    *,
    fresh_session_roots: Iterable[str | Path],
    soak_roots: Iterable[str | Path],
    supervisor_log: str | Path,
    fresh_boots_asserted: bool = False,
    process_death_asserted: bool = False,
    required_fresh_boots: int = DEFAULT_FRESH_BOOT_RUNS,
    minimum_soak_hours: float = DEFAULT_SOAK_HOURS,
    minimum_soak_games: int = DEFAULT_SOAK_GAMES,
    maximum_unresolved_pct: float = DEFAULT_MAX_UNRESOLVED_PCT,
) -> dict[str, Any]:
    sections = {
        "fresh_boot_selection": evaluate_fresh_boots(
            fresh_session_roots,
            asserted=fresh_boots_asserted,
            required_runs=required_fresh_boots,
        ),
        "process_death_recovery": evaluate_process_death(
            supervisor_log,
            asserted=process_death_asserted,
        ),
        "overnight_soak": evaluate_soak(
            soak_roots,
            min_hours=minimum_soak_hours,
            min_games_completed=minimum_soak_games,
            max_unresolved_pct=maximum_unresolved_pct,
        ),
    }
    passed = all(section["passed"] for section in sections.values())
    blockers = [name for name, section in sections.items() if not section["passed"]]
    return {
        "schema": "madden-final-acceptance-v1",
        "passed": passed,
        "blockers": blockers,
        "sections": sections,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ps2-autopilot-madden-acceptance",
        description="Evaluate the final Madden #3 live acceptance gates from retained evidence.",
    )
    parser.add_argument("--fresh-session", action="append", default=[])
    parser.add_argument("--soak-root", action="append", default=[])
    parser.add_argument("--supervisor-log", default="runtime/supervisor.jsonl")
    parser.add_argument("--fresh-boots", action="store_true")
    parser.add_argument("--process-death-test", action="store_true")
    parser.add_argument("--required-fresh-boots", type=int, default=DEFAULT_FRESH_BOOT_RUNS)
    parser.add_argument("--minimum-soak-hours", type=float, default=DEFAULT_SOAK_HOURS)
    parser.add_argument("--minimum-soak-games", type=int, default=DEFAULT_SOAK_GAMES)
    parser.add_argument(
        "--maximum-unresolved-pct",
        type=float,
        default=DEFAULT_MAX_UNRESOLVED_PCT,
    )
    args = parser.parse_args(argv)

    report = evaluate_acceptance(
        fresh_session_roots=args.fresh_session,
        soak_roots=args.soak_root,
        supervisor_log=args.supervisor_log,
        fresh_boots_asserted=args.fresh_boots,
        process_death_asserted=args.process_death_test,
        required_fresh_boots=args.required_fresh_boots,
        minimum_soak_hours=args.minimum_soak_hours,
        minimum_soak_games=args.minimum_soak_games,
        maximum_unresolved_pct=args.maximum_unresolved_pct,
    )
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
