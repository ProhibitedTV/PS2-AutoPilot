from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import statistics
from typing import Any, Iterable

from .observability import read_jsonl


@dataclass
class SessionAnalysis:
    root: Path
    game: str
    profile: str
    samples: int
    duration_seconds: float
    games_started: int
    games_completed: int
    hard_recoveries: int
    semantic_recoveries: int
    unknown_captures: int
    failure_bundles: int
    unresolved_seconds: float
    unresolved_reasons: Counter[str]
    loop_ms: list[float]
    capture_ms: list[float]
    policy_ms: list[float]
    loop_overruns: int
    loop_samples: int
    ocr_result_age_ms: list[float]
    ocr_completion_age_ms: list[float]
    ocr_dropped_frames: int

    @property
    def unresolved_pct(self) -> float:
        if self.duration_seconds <= 0.0:
            return 0.0
        return min(100.0, self.unresolved_seconds / self.duration_seconds * 100.0)

    @property
    def loop_overrun_pct(self) -> float:
        if self.loop_samples <= 0:
            return 0.0
        return self.loop_overruns / self.loop_samples * 100.0


def _finite_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _percentile(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * max(0.0, min(1.0, percentile))
    lo = int(math.floor(rank))
    hi = int(math.ceil(rank))
    if lo == hi:
        return ordered[lo]
    weight = rank - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def _read_log_series(root: Path, name: str) -> list[dict[str, Any]]:
    """Read the retained rotated log followed by the current log.

    JsonlWriter keeps one ``.1`` generation. Overnight acceptance should not silently
    ignore the older half merely because the log rotated while the process kept running.
    """

    rows: list[dict[str, Any]] = []
    for path in (root / f"{name}.1", root / name):
        rows.extend(read_jsonl(path))
    return rows


def _row_time(row: dict[str, Any]) -> float | None:
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    stamp = _finite_number(state.get("timestamp"))
    if stamp is not None:
        return stamp
    text = str(row.get("utc") or "")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _sample_durations(rows: list[dict[str, Any]], max_gap_seconds: float) -> list[float]:
    if not rows:
        return []
    times = [_row_time(row) for row in rows]
    positive: list[float] = []
    for current, following in zip(times, times[1:]):
        if current is None or following is None:
            continue
        delta = following - current
        if 0.0 < delta <= max_gap_seconds:
            positive.append(delta)
    fallback = statistics.median(positive) if positive else min(1.0, max_gap_seconds)

    result: list[float] = []
    for index, current in enumerate(times):
        if index + 1 >= len(times):
            result.append(fallback)
            continue
        following = times[index + 1]
        if current is None or following is None:
            result.append(fallback)
            continue
        result.append(max(0.0, min(max_gap_seconds, following - current)))
    return result


def _counter_total(states: list[dict[str, Any]], key: str) -> int:
    """Count monotonic counter increments while surviving an occasional reset."""

    total = 0
    previous: int | None = None
    for state in states:
        try:
            value = max(0, int(state.get(key, 0) or 0))
        except (TypeError, ValueError):
            continue
        if previous is None:
            total += value
        elif value >= previous:
            total += value - previous
        else:
            total += value
        previous = value
    return total


def _counter_max(states: list[dict[str, Any]], *keys: str) -> int:
    result = 0
    for state in states:
        for key in keys:
            try:
                result = max(result, int(state.get(key, 0) or 0))
            except (TypeError, ValueError):
                continue
    return result


def _game_identity(states: list[dict[str, Any]]) -> tuple[str, str]:
    game = "unknown"
    profile = "unknown"
    for state in states:
        profile = str(state.get("profile") or profile)
        game = str(
            state.get("game_display_name")
            or state.get("game_id")
            or state.get("pine_game_title")
            or game
        )
        if game != "unknown" and profile != "unknown":
            break
    return game, profile


def _unresolved_reason(state: dict[str, Any]) -> str | None:
    explicit_flags = (
        ("navigation_unresolved", "navigation-unresolved"),
        ("jak_goal_replan_due", "objective-stall"),
        ("local_stuck_active", "local-stuck"),
        ("jak_local_stuck_active", "local-stuck"),
        ("water_escape_active", "water-escape"),
        ("jak_water_escape_active", "water-escape"),
    )
    for key, reason in explicit_flags:
        if bool(state.get(key)):
            return reason

    recovery_reason = str(state.get("progress_recovery_reason") or "").strip()
    if recovery_reason:
        return "progress-recovery"

    navigation_reason = str(state.get("jak_navigation_commit_reason") or "").lower()
    if navigation_reason in {
        "generic-stagnation",
        "loop-sweep-preempted",
        "ambiguous-route-scan",
        "learned-danger",
        "episode-danger",
    } and bool(state.get("jak_navigation_commit_active")):
        return navigation_reason

    action = str(state.get("action") or "").lower()
    markers = (
        ("fail closed", "fail-closed"),
        ("stagnation", "stagnation"),
        ("stuck", "stuck"),
        ("replan", "replan"),
        ("water escape", "water-escape"),
        ("escape water", "water-escape"),
        ("route scan", "route-scan"),
        ("break contact", "break-contact"),
    )
    for marker, reason in markers:
        if marker in action:
            return reason

    phase = str(state.get("game_phase") or state.get("jak_phase") or state.get("phase") or "").lower()
    if phase == "unknown" and ("hold input" in action or "unknown" in action):
        return "unknown-state"
    return None


def analyze_session(root: str | Path, *, max_gap_seconds: float = 5.0) -> SessionAnalysis:
    root = Path(root)
    verbose = _read_log_series(root, "verbose.jsonl")
    states = [row.get("state") for row in verbose if isinstance(row.get("state"), dict)]
    states = [state for state in states if isinstance(state, dict)]
    game, profile = _game_identity(states)
    durations = _sample_durations(verbose, max(0.25, float(max_gap_seconds)))

    unresolved_seconds = 0.0
    unresolved_reasons: Counter[str] = Counter()
    for index, state in enumerate(states):
        reason = _unresolved_reason(state)
        if reason is None:
            continue
        duration = durations[index] if index < len(durations) else 0.0
        unresolved_seconds += duration
        unresolved_reasons[reason] += 1

    loop_ms: list[float] = []
    capture_ms: list[float] = []
    policy_ms: list[float] = []
    ocr_result_age_ms: list[float] = []
    ocr_completion_age_ms: list[float] = []
    loop_overruns = 0
    loop_samples = 0
    for state in states:
        loop = _finite_number(state.get("last_loop_ms"))
        budget = _finite_number(state.get("loop_budget_ms"))
        capture = _finite_number(state.get("capture_ms"))
        policy = _finite_number(state.get("policy_ms"))
        result_age = _finite_number(state.get("ocr_result_age_ms"))
        completion_age = _finite_number(state.get("ocr_completion_age_ms"))
        if loop is not None:
            loop_ms.append(loop)
        if capture is not None:
            capture_ms.append(capture)
        if policy is not None:
            policy_ms.append(policy)
        if result_age is not None:
            ocr_result_age_ms.append(result_age)
        if completion_age is not None:
            ocr_completion_age_ms.append(completion_age)
        if loop is not None and budget is not None and budget > 0.0:
            loop_samples += 1
            loop_overruns += int(loop > budget)

    events = _read_log_series(root, "events.jsonl")
    failure_bundles = sum(row.get("kind") == "failure_bundle" for row in events)

    # Session game counters are lifecycle-level counters and should be read by maximum,
    # while recovery/unknown counters may reset after escalation and therefore need the
    # reset-aware delta accumulator.
    games_started = _counter_max(states, "session_games_started", "games_started")
    games_completed = _counter_max(states, "session_games_completed", "games_completed")
    hard_recoveries = _counter_total(states, "recoveries")
    semantic_recoveries = _counter_total(states, "session_progress_recoveries")
    unknown_captures = _counter_total(states, "session_unknown_captures")
    ocr_dropped_frames = _counter_total(states, "ocr_dropped_frames")

    return SessionAnalysis(
        root=root,
        game=game,
        profile=profile,
        samples=len(states),
        duration_seconds=sum(durations[: len(states)]),
        games_started=games_started,
        games_completed=games_completed,
        hard_recoveries=hard_recoveries,
        semantic_recoveries=semantic_recoveries,
        unknown_captures=unknown_captures,
        failure_bundles=failure_bundles,
        unresolved_seconds=unresolved_seconds,
        unresolved_reasons=unresolved_reasons,
        loop_ms=loop_ms,
        capture_ms=capture_ms,
        policy_ms=policy_ms,
        loop_overruns=loop_overruns,
        loop_samples=loop_samples,
        ocr_result_age_ms=ocr_result_age_ms,
        ocr_completion_age_ms=ocr_completion_age_ms,
        ocr_dropped_frames=ocr_dropped_frames,
    )


def discover_sessions(paths: Iterable[str | Path]) -> list[Path]:
    sessions: set[Path] = set()
    for raw in paths:
        path = Path(raw)
        if path.is_file() and path.name in {"verbose.jsonl", "verbose.jsonl.1"}:
            sessions.add(path.parent)
            continue
        if not path.exists() or not path.is_dir():
            continue
        if (path / "verbose.jsonl").exists() or (path / "verbose.jsonl.1").exists():
            sessions.add(path)
        for candidate in path.rglob("verbose.jsonl"):
            sessions.add(candidate.parent)
        for candidate in path.rglob("verbose.jsonl.1"):
            sessions.add(candidate.parent)
    return sorted(sessions, key=lambda value: str(value).lower())


def _metric_block(analyses: list[SessionAnalysis]) -> dict[str, Any]:
    duration = sum(item.duration_seconds for item in analyses)
    unresolved = sum(item.unresolved_seconds for item in analyses)
    loop_ms = [value for item in analyses for value in item.loop_ms]
    capture_ms = [value for item in analyses for value in item.capture_ms]
    policy_ms = [value for item in analyses for value in item.policy_ms]
    ocr_age = [value for item in analyses for value in item.ocr_result_age_ms]
    ocr_completion = [value for item in analyses for value in item.ocr_completion_age_ms]
    loop_samples = sum(item.loop_samples for item in analyses)
    loop_overruns = sum(item.loop_overruns for item in analyses)
    unresolved_reasons: Counter[str] = Counter()
    for item in analyses:
        unresolved_reasons.update(item.unresolved_reasons)

    started = sum(item.games_started for item in analyses)
    completed = sum(item.games_completed for item in analyses)
    return {
        "sessions": len(analyses),
        "samples": sum(item.samples for item in analyses),
        "duration_seconds": round(duration, 3),
        "games_started": started,
        "games_completed": completed,
        "game_completion_pct": round(0.0 if started <= 0 else completed / started * 100.0, 3),
        "hard_recoveries": sum(item.hard_recoveries for item in analyses),
        "semantic_recoveries": sum(item.semantic_recoveries for item in analyses),
        "unknown_captures": sum(item.unknown_captures for item in analyses),
        "failure_bundles": sum(item.failure_bundles for item in analyses),
        "unresolved_navigation_seconds": round(unresolved, 3),
        "unresolved_navigation_pct": round(0.0 if duration <= 0.0 else unresolved / duration * 100.0, 3),
        "unresolved_reasons": dict(unresolved_reasons.most_common()),
        "loop_p50_ms": round(_percentile(loop_ms, 0.50), 3),
        "loop_p95_ms": round(_percentile(loop_ms, 0.95), 3),
        "capture_p50_ms": round(_percentile(capture_ms, 0.50), 3),
        "capture_p95_ms": round(_percentile(capture_ms, 0.95), 3),
        "policy_p50_ms": round(_percentile(policy_ms, 0.50), 3),
        "policy_p95_ms": round(_percentile(policy_ms, 0.95), 3),
        "loop_overrun_pct": round(0.0 if loop_samples <= 0 else loop_overruns / loop_samples * 100.0, 3),
        # Result age is end-to-end OCR staleness from the submitted frame; unlike raw
        # inference duration it is available for both MaddenOCR and SemanticOCR today.
        "ocr_result_age_p50_ms": round(_percentile(ocr_age, 0.50), 3),
        "ocr_result_age_p95_ms": round(_percentile(ocr_age, 0.95), 3),
        "ocr_completion_age_p50_ms": round(_percentile(ocr_completion, 0.50), 3),
        "ocr_completion_age_p95_ms": round(_percentile(ocr_completion, 0.95), 3),
        "ocr_dropped_frames": sum(item.ocr_dropped_frames for item in analyses),
    }


def build_report(paths: Iterable[str | Path], *, max_gap_seconds: float = 5.0) -> dict[str, Any]:
    roots = discover_sessions(paths)
    analyses = [analyze_session(root, max_gap_seconds=max_gap_seconds) for root in roots]
    by_game: dict[str, list[SessionAnalysis]] = {}
    for analysis in analyses:
        by_game.setdefault(analysis.game, []).append(analysis)

    sessions = []
    for item in analyses:
        block = _metric_block([item])
        block.update({"root": str(item.root), "game": item.game, "profile": item.profile})
        sessions.append(block)

    return {
        "schema": "ps2-autopilot-soak-report-v1",
        "session_count": len(analyses),
        "game_count": len(by_game),
        "overall": _metric_block(analyses),
        "games": {name: _metric_block(items) for name, items in sorted(by_game.items())},
        "sessions": sessions,
    }


def evaluate_acceptance(
    report: dict[str, Any],
    *,
    min_games_completed: int | None = None,
    max_unresolved_pct: float | None = None,
    max_loop_overrun_pct: float | None = None,
    max_unknown_captures: int | None = None,
    max_hard_recoveries: int | None = None,
) -> list[str]:
    overall = report.get("overall") if isinstance(report.get("overall"), dict) else {}
    failures: list[str] = []
    if min_games_completed is not None and int(overall.get("games_completed", 0)) < min_games_completed:
        failures.append(
            f"games_completed {overall.get('games_completed', 0)} < required {min_games_completed}"
        )
    if max_unresolved_pct is not None and float(overall.get("unresolved_navigation_pct", 0.0)) > max_unresolved_pct:
        failures.append(
            f"unresolved_navigation_pct {float(overall.get('unresolved_navigation_pct', 0.0)):.2f} > {max_unresolved_pct:.2f}"
        )
    if max_loop_overrun_pct is not None and float(overall.get("loop_overrun_pct", 0.0)) > max_loop_overrun_pct:
        failures.append(
            f"loop_overrun_pct {float(overall.get('loop_overrun_pct', 0.0)):.2f} > {max_loop_overrun_pct:.2f}"
        )
    if max_unknown_captures is not None and int(overall.get("unknown_captures", 0)) > max_unknown_captures:
        failures.append(
            f"unknown_captures {overall.get('unknown_captures', 0)} > {max_unknown_captures}"
        )
    if max_hard_recoveries is not None and int(overall.get("hard_recoveries", 0)) > max_hard_recoveries:
        failures.append(
            f"hard_recoveries {overall.get('hard_recoveries', 0)} > {max_hard_recoveries}"
        )
    return failures


def _duration(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _print_block(title: str, block: dict[str, Any]) -> None:
    print(title)
    print("-" * 72)
    print(
        f"sessions={block['sessions']}  duration={_duration(float(block['duration_seconds']))}  "
        f"games={block['games_completed']}/{block['games_started']}  "
        f"unknown={block['unknown_captures']}  recoveries={block['hard_recoveries']}+{block['semantic_recoveries']}"
    )
    print(
        f"loop p50/p95={block['loop_p50_ms']:.1f}/{block['loop_p95_ms']:.1f} ms  "
        f"overrun={block['loop_overrun_pct']:.1f}%  "
        f"capture p95={block['capture_p95_ms']:.1f} ms  policy p95={block['policy_p95_ms']:.1f} ms"
    )
    print(
        f"OCR result-age p50/p95={block['ocr_result_age_p50_ms']:.1f}/{block['ocr_result_age_p95_ms']:.1f} ms  "
        f"dropped={block['ocr_dropped_frames']}"
    )
    print(
        f"unresolved navigation={_duration(float(block['unresolved_navigation_seconds']))} "
        f"({block['unresolved_navigation_pct']:.1f}%)"
    )
    reasons = block.get("unresolved_reasons") or {}
    if reasons:
        compact = ", ".join(f"{name}:{count}" for name, count in list(reasons.items())[:6])
        print(f"unresolved reasons: {compact}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate multi-game PS2 AutoPilot unattended-soak acceptance telemetry"
    )
    parser.add_argument(
        "roots",
        nargs="*",
        default=["runtime"],
        help="Runtime dir(s) or parent dir(s) containing sessions; recursively discovered",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument(
        "--max-gap-seconds",
        type=float,
        default=5.0,
        help="Maximum time attributed to one telemetry sample across logging gaps",
    )
    parser.add_argument("--min-games-completed", type=int)
    parser.add_argument("--max-unresolved-pct", type=float)
    parser.add_argument("--max-loop-overrun-pct", type=float)
    parser.add_argument("--max-unknown-captures", type=int)
    parser.add_argument("--max-hard-recoveries", type=int)
    args = parser.parse_args(argv)

    report = build_report(args.roots, max_gap_seconds=args.max_gap_seconds)
    failures = evaluate_acceptance(
        report,
        min_games_completed=args.min_games_completed,
        max_unresolved_pct=args.max_unresolved_pct,
        max_loop_overrun_pct=args.max_loop_overrun_pct,
        max_unknown_captures=args.max_unknown_captures,
        max_hard_recoveries=args.max_hard_recoveries,
    )
    thresholded = any(
        value is not None
        for value in (
            args.min_games_completed,
            args.max_unresolved_pct,
            args.max_loop_overrun_pct,
            args.max_unknown_captures,
            args.max_hard_recoveries,
        )
    )
    report["acceptance"] = {
        "thresholds_enabled": thresholded,
        "passed": bool(thresholded and not failures),
        "failures": failures,
    }

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("PS2 AUTOPILOT MULTI-GAME SOAK REPORT")
        print("=" * 72)
        print(
            f"sessions={report['session_count']}  games={report['game_count']}  "
            f"schema={report['schema']}"
        )
        print()
        _print_block("OVERALL", report["overall"])
        for game, block in report["games"].items():
            print()
            _print_block(game, block)
        print()
        if not thresholded:
            print("ACCEPTANCE: INFORMATIONAL (no thresholds supplied)")
        elif failures:
            print("ACCEPTANCE: FAIL")
            for failure in failures:
                print(f"  - {failure}")
        else:
            print("ACCEPTANCE: PASS")

    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
