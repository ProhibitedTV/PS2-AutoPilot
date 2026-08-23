from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any

from .observability import read_jsonl, summarize_runtime


def _clock(value: Any) -> str:
    text = str(value or "")
    if "T" in text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone().strftime(
                "%H:%M:%S"
            )
        except ValueError:
            pass
    return text[:8] or "--:--:--"


def _format_event(row: dict[str, Any]) -> str:
    stamp = _clock(row.get("utc"))
    kind = str(row.get("kind") or "event")
    if kind == "decision":
        state = row.get("state") or {}
        phase = str(state.get("phase") or "?").upper()
        screen = str(state.get("menu_screen") or state.get("game_state") or "?").upper()
        role = str(state.get("possession") or "").upper()
        action = str(row.get("action") or "")
        decision = row.get("decision_id")
        suffix = f" role={role}" if role and role != "UNKNOWN" else ""
        return f"[{stamp}] D{decision!s:<6} {phase:<10} {screen:<18} {action}{suffix}"
    if kind == "failure_bundle":
        return f"[{stamp}] FAILURE  {row.get('reason')} -> {row.get('path')}"
    return f"[{stamp}] {kind.upper():<8} {row}"


def _format_input(row: dict[str, Any]) -> str:
    stamp = _clock(row.get("utc"))
    decision = row.get("decision_id")
    kind = str(row.get("kind") or "input")
    action = row.get("action")
    if action:
        return f"[{stamp}] D{decision!s:<6} INPUT {kind:<10} {action}"
    x, y = row.get("x"), row.get("y")
    if x is not None or y is not None:
        return (
            f"[{stamp}] D{decision!s:<6} INPUT {kind:<10} "
            f"x={float(x or 0):+.2f} y={float(y or 0):+.2f}"
        )
    return f"[{stamp}] D{decision!s:<6} INPUT {kind}"


def _format_spatial(row: dict[str, Any]) -> str:
    stamp = _clock(row.get("utc"))
    decision = row.get("decision_id")
    phase = str(row.get("phase") or "?").upper()
    role = str(row.get("possession") or "unknown").upper()
    role_conf = float(row.get("possession_confidence") or 0.0)
    players = int(row.get("spatial_players") or 0)
    ball = float(row.get("spatial_ball_confidence") or 0.0)
    target = float(row.get("spatial_target_confidence") or 0.0)
    tx = float(row.get("spatial_target_x") or 0.0)
    ty = float(row.get("spatial_target_y") or 0.0)
    open_x = float(row.get("spatial_open_x") or 0.0)
    open_conf = float(row.get("spatial_open_confidence") or 0.0)
    mode = str(row.get("spatial_policy_mode") or "fallback")
    cpu = float(row.get("spatial_processing_ms") or 0.0)
    return (
        f"[{stamp}] D{decision!s:<6} SPATIAL {phase:<9} role={role}:{role_conf:.2f} "
        f"players={players:02d} ball={ball:.2f} target=({tx:+.2f},{ty:+.2f})/{target:.2f} "
        f"open={open_x:+.2f}/{open_conf:.2f} mode={mode} cpu={cpu:.1f}ms"
    )


def _format_verbose(row: dict[str, Any]) -> str:
    stamp = _clock(row.get("utc"))
    decision = row.get("decision_id")
    state = row.get("state") or {}
    phase = str(state.get("phase") or "?").upper()
    screen = str(state.get("menu_screen") or state.get("game_state") or "?").upper()
    role = str(state.get("possession") or "unknown").upper()
    role_conf = float(state.get("possession_confidence") or 0.0)
    action = str(state.get("action") or "")
    ocr = str(state.get("ocr_text") or "").replace("\n", " ")[:110]
    spatial = ""
    if state.get("spatial_enabled"):
        spatial = (
            f" | spatial p={int(state.get('spatial_players') or 0)} "
            f"ball={float(state.get('spatial_ball_confidence') or 0):.2f} "
            f"target={float(state.get('spatial_target_confidence') or 0):.2f} "
            f"mode={state.get('spatial_policy_mode') or 'fallback'}"
        )
    return (
        f"[{stamp}] D{decision!s:<6} VERBOSE {phase:<9} {screen:<16} "
        f"role={role}:{role_conf:.2f} | {action}{spatial} | OCR={ocr}"
    )


def _tail_lines(path: Path, last: int) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    return rows[-max(1, last) :]


def log_main() -> None:
    parser = argparse.ArgumentParser(description="Tail PS2 AutoPilot structured runtime logs")
    parser.add_argument("--root", default="runtime", help="Runtime directory")
    parser.add_argument("--last", type=int, default=40, help="Initial rows to print")
    parser.add_argument("--follow", "-f", action="store_true", help="Follow new events")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--inputs", action="store_true", help="Tail controller input log")
    mode.add_argument("--spatial", action="store_true", help="Tail spatial localization log")
    mode.add_argument("--verbose", action="store_true", help="Tail dense 1-second telemetry log")
    args = parser.parse_args()

    root = Path(args.root)
    if args.inputs:
        path, formatter = root / "input.jsonl", _format_input
    elif args.spatial:
        path, formatter = root / "spatial.jsonl", _format_spatial
    elif args.verbose:
        path, formatter = root / "verbose.jsonl", _format_verbose
    else:
        path, formatter = root / "events.jsonl", _format_event

    for row in _tail_lines(path, args.last):
        print(formatter(row))

    if not args.follow:
        return

    print(f"-- following {path} (Ctrl+C to stop) --")
    position = path.stat().st_size if path.exists() else 0
    try:
        while True:
            if not path.exists():
                time.sleep(0.5)
                continue
            size = path.stat().st_size
            if size < position:
                position = 0
            if size > position:
                with path.open("r", encoding="utf-8") as fh:
                    fh.seek(position)
                    for line in fh:
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(row, dict):
                            print(formatter(row), flush=True)
                    position = fh.tell()
            time.sleep(0.35)
    except KeyboardInterrupt:
        pass


def _transition_counts(events: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    previous: tuple[str, str] | None = None
    for row in events:
        if row.get("kind") != "decision":
            continue
        state = row.get("state") or {}
        current = (
            str(state.get("phase") or "?"),
            str(state.get("menu_screen") or state.get("game_state") or "?"),
        )
        if previous is not None and current != previous:
            counts[f"{previous[0]}/{previous[1]} -> {current[0]}/{current[1]}"] += 1
        previous = current
    return counts


def _spatial_summary(rows: list[dict[str, Any]]) -> dict[str, float | int]:
    if not rows:
        return {
            "samples": 0,
            "avg_players": 0.0,
            "ball_lock_pct": 0.0,
            "target_lock_pct": 0.0,
            "avg_cpu_ms": 0.0,
            "overrides": 0,
        }
    players = [float(row.get("spatial_players") or 0.0) for row in rows]
    ball_locks = sum(float(row.get("spatial_ball_confidence") or 0.0) >= 0.45 for row in rows)
    target_locks = sum(float(row.get("spatial_target_confidence") or 0.0) >= 0.50 for row in rows)
    cpu = [float(row.get("spatial_processing_ms") or 0.0) for row in rows]
    overrides = max((int(row.get("spatial_overrides") or 0) for row in rows), default=0)
    count = len(rows)
    return {
        "samples": count,
        "avg_players": sum(players) / count,
        "ball_lock_pct": ball_locks / count * 100.0,
        "target_lock_pct": target_locks / count * 100.0,
        "avg_cpu_ms": sum(cpu) / count,
        "overrides": overrides,
    }


def report_main() -> None:
    parser = argparse.ArgumentParser(description="Summarize a PS2 AutoPilot runtime session")
    parser.add_argument("--root", default="runtime", help="Runtime directory")
    args = parser.parse_args()
    root = Path(args.root)
    summary = summarize_runtime(root)
    events = read_jsonl(root / "events.jsonl")
    verbose_rows = read_jsonl(root / "verbose.jsonl")
    spatial_rows = read_jsonl(root / "spatial.jsonl")
    spatial = _spatial_summary(spatial_rows)
    transitions = _transition_counts(events)
    failure_rows = [row for row in events if row.get("kind") == "failure_bundle"]

    uptime = float(summary["uptime_seconds"])
    hours = int(uptime // 3600)
    minutes = int((uptime % 3600) // 60)
    seconds = int(uptime % 60)
    games_started = int(summary["games_started"])
    games_completed = int(summary["games_completed"])
    completion = 0.0 if not games_started else games_completed / games_started * 100.0

    print("PS2 AUTOPILOT SESSION REPORT")
    print("=" * 42)
    print(f"Observed uptime:       {hours:02d}:{minutes:02d}:{seconds:02d}")
    print(f"Games started:         {games_started}")
    print(f"Games completed:       {games_completed} ({completion:.1f}%)")
    print(f"Semantic recoveries:   {summary['progress_recoveries']}")
    print(f"Hard recoveries:       {summary['hard_recoveries']}")
    print(f"Unknown captures:      {summary['unknown_captures']}")
    print(f"Failure bundles:       {summary['failure_bundles']}")
    print(f"Decision events:       {summary['event_count']}")
    print(f"Heartbeats:            {summary['heartbeat_count']}")
    print(f"Controller log rows:   {summary['input_count']}")
    print(f"Verbose snapshots:     {len(verbose_rows)}")

    print("\nSpatial perception")
    print("-" * 42)
    print(f"Spatial samples:       {spatial['samples']}")
    print(f"Avg player candidates: {float(spatial['avg_players']):.1f}")
    print(f"Ball lock >= .45:      {float(spatial['ball_lock_pct']):.1f}%")
    print(f"Target lock >= .50:    {float(spatial['target_lock_pct']):.1f}%")
    print(f"Avg spatial CPU:       {float(spatial['avg_cpu_ms']):.2f} ms")
    print(f"Policy overrides:      {spatial['overrides']}")

    print("\nTop decisions")
    print("-" * 42)
    for action, count in summary["top_actions"]:
        print(f"{count:5d}  {action}")
    if not summary["top_actions"]:
        print("(no decision events yet)")

    print("\nMost common transitions")
    print("-" * 42)
    for transition, count in transitions.most_common(10):
        print(f"{count:5d}  {transition}")
    if not transitions:
        print("(no transitions yet)")

    print("\nController commands")
    print("-" * 42)
    for kind, count in summary["input_kinds"].most_common():
        print(f"{count:5d}  {kind}")
    if not summary["input_kinds"]:
        print("(no inputs logged yet)")

    print("\nRecent failures")
    print("-" * 42)
    for row in failure_rows[-8:]:
        print(f"{_clock(row.get('utc'))}  {row.get('reason')}  {row.get('path')}")
    if not failure_rows:
        print("(none recorded)")


if __name__ == "__main__":
    report_main()
