from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from ps2_autopilot.madden_acceptance import (
    evaluate_acceptance,
    evaluate_fresh_boots,
    evaluate_process_death,
    evaluate_soak,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _fresh_session(root: Path, *, complete: bool = True) -> None:
    state = {
        "timestamp": 100.0,
        "profile": "madden2005",
        "game_display_name": "Madden NFL 2005",
        "frontend_verified_crosses": 1,
        "team_rotation_games": 1,
        "team_rotation_steps": 4,
        "controlled_side_moves": 1,
        "session_games_started": 1,
        "session_games_completed": 0,
        "phase": "pre_snap",
        "menu_screen": "field",
    }
    if not complete:
        state["controlled_side_moves"] = 0
    _write_jsonl(root / "verbose.jsonl", [{"utc": "2026-08-24T12:00:00+00:00", "state": state}])


def test_fresh_boot_gate_requires_explicit_assertion_and_every_supplied_run(tmp_path: Path) -> None:
    roots = []
    for index in range(3):
        root = tmp_path / f"fresh-{index}"
        _fresh_session(root)
        roots.append(root)

    assert evaluate_fresh_boots(roots, asserted=False, required_runs=3)["passed"] is False
    assert evaluate_fresh_boots(roots, asserted=True, required_runs=3)["passed"] is True

    bad = tmp_path / "fresh-bad"
    _fresh_session(bad, complete=False)
    report = evaluate_fresh_boots([*roots, bad], asserted=True, required_runs=3)
    assert report["passed"] is False
    assert report["passing_runs"] == 3
    assert report["supplied_runs"] == 4


def test_process_death_gate_requires_ordered_real_supervisor_events_and_assertion(tmp_path: Path) -> None:
    base = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    rows = [
        {
            "utc": base.isoformat(),
            "kind": "autopilot-exit",
            "reason": "pcsx2-window-lost",
            "supervisor_pcsx2_window_lost": True,
        },
        {
            "utc": (base + timedelta(seconds=5)).isoformat(),
            "kind": "emulator-window-ready",
            "reason": "pcsx2-window-lost",
            "supervisor_emulator_pid": 2222,
        },
        {
            "utc": (base + timedelta(seconds=6)).isoformat(),
            "kind": "autopilot-start",
            "reason": "restart",
            "supervisor_autopilot_pid": 9002,
        },
    ]
    path = tmp_path / "supervisor.jsonl"
    _write_jsonl(path, rows)

    assert evaluate_process_death(path, asserted=False)["passed"] is False
    report = evaluate_process_death(path, asserted=True)
    assert report["passed"] is True
    assert report["ordered_recovery"] is True
    assert report["replacement_pid"] is True

    _write_jsonl(path, [rows[1], rows[0], rows[2]])
    assert evaluate_process_death(path, asserted=True)["passed"] is False


def test_soak_gate_rejects_short_run_and_unresolved_final_state(tmp_path: Path) -> None:
    root = tmp_path / "soak"
    rows = [
        {
            "utc": "2026-08-24T12:00:00+00:00",
            "state": {
                "timestamp": 100.0,
                "profile": "madden2005",
                "game_display_name": "Madden NFL 2005",
                "session_games_started": 2,
                "session_games_completed": 2,
                "phase": "live",
                "menu_screen": "field",
                "last_loop_ms": 50.0,
                "loop_budget_ms": 83.0,
            },
        },
        {
            "utc": "2026-08-24T12:00:05+00:00",
            "state": {
                "timestamp": 105.0,
                "profile": "madden2005",
                "game_display_name": "Madden NFL 2005",
                "session_games_started": 2,
                "session_games_completed": 2,
                "phase": "post_play",
                "menu_screen": "presentation",
                "last_loop_ms": 50.0,
                "loop_budget_ms": 83.0,
            },
        },
    ]
    _write_jsonl(root / "verbose.jsonl", rows)

    report = evaluate_soak(
        [root],
        min_hours=0.001,
        min_games_completed=2,
        max_unresolved_pct=1.0,
    )
    assert report["passed"] is True

    rows[-1]["state"]["progress_recovery_reason"] = "no semantic progress in menu"
    _write_jsonl(root / "verbose.jsonl", rows)
    report = evaluate_soak(
        [root],
        min_hours=0.001,
        min_games_completed=2,
        max_unresolved_pct=100.0,
    )
    assert report["passed"] is False
    assert report["final_unresolved"]


def test_overall_acceptance_refuses_partial_evidence(tmp_path: Path) -> None:
    fresh = []
    for index in range(3):
        root = tmp_path / f"fresh-{index}"
        _fresh_session(root)
        fresh.append(root)

    soak = tmp_path / "soak"
    _write_jsonl(
        soak / "verbose.jsonl",
        [
            {
                "utc": "2026-08-24T12:00:00+00:00",
                "state": {
                    "timestamp": 100.0,
                    "profile": "madden2005",
                    "game_display_name": "Madden NFL 2005",
                    "session_games_started": 2,
                    "session_games_completed": 2,
                    "phase": "live",
                    "menu_screen": "field",
                },
            },
            {
                "utc": "2026-08-24T12:00:05+00:00",
                "state": {
                    "timestamp": 105.0,
                    "profile": "madden2005",
                    "game_display_name": "Madden NFL 2005",
                    "session_games_started": 2,
                    "session_games_completed": 2,
                    "phase": "post_play",
                    "menu_screen": "presentation",
                },
            },
        ],
    )

    report = evaluate_acceptance(
        fresh_session_roots=fresh,
        soak_roots=[soak],
        supervisor_log=tmp_path / "missing-supervisor.jsonl",
        fresh_boots_asserted=True,
        process_death_asserted=False,
        required_fresh_boots=3,
        minimum_soak_hours=0.001,
        minimum_soak_games=2,
        maximum_unresolved_pct=1.0,
    )
    assert report["passed"] is False
    assert report["blockers"] == ["process_death_recovery"]
