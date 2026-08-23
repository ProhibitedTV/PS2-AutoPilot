import json
import time

import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.observability import RuntimeObserver, TracingController, summarize_runtime


class FakeController(Controller):
    def __init__(self):
        self.calls = []

    def tap(self, action: str, duration: float = 0.08) -> None:
        self.calls.append(("tap", action, duration))

    def hold(self, action: str) -> None:
        self.calls.append(("hold", action))

    def release(self, action: str) -> None:
        self.calls.append(("release", action))

    def release_all(self) -> None:
        self.calls.append(("release_all",))

    def set_left_stick(self, x: float, y: float) -> None:
        self.calls.append(("left_stick", x, y))

    def set_right_stick(self, x: float, y: float) -> None:
        self.calls.append(("right_stick", x, y))


def test_tracing_controller_correlates_inputs_with_decision(tmp_path):
    observer = RuntimeObserver({"console": False}, tmp_path)
    inner = FakeController()
    controller = TracingController(inner, observer.record_input, stick_interval_seconds=99.0)
    controller.set_decision_id(42)

    controller.tap("cross", 0.07)
    controller.set_left_stick(0.4, 0.9)
    controller.set_left_stick(0.41, 0.91)  # sampled out as near-identical

    rows = [json.loads(line) for line in (tmp_path / "input.jsonl").read_text().splitlines()]
    assert rows[0]["decision_id"] == 42
    assert rows[0]["kind"] == "tap"
    assert rows[0]["action"] == "cross"
    assert sum(1 for row in rows if row["kind"] == "left_stick") == 1
    assert inner.calls[-1][0] == "left_stick"


def test_observer_logs_changed_decisions_and_heartbeats(tmp_path):
    observer = RuntimeObserver(
        {"console": False, "heartbeat_seconds": 1.0},
        tmp_path,
    )
    frame = np.zeros((64, 96, 3), dtype=np.uint8)
    now = time.monotonic()
    state = {
        "status": "running",
        "phase": "playcall",
        "menu_screen": "playcall",
        "menu_confidence": 0.94,
        "possession": "defense",
        "plays_started": 1,
        "plays_completed": 0,
    }

    observer.record_cycle(1, frame, None, state, "playcall: cross", now)
    observer.record_cycle(2, frame, frame, state, "playcall: cross", now + 0.2)
    observer.record_cycle(3, frame, frame, state, "playcall: square", now + 1.2)

    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    heartbeats = [
        json.loads(line) for line in (tmp_path / "heartbeat.jsonl").read_text().splitlines()
    ]
    decisions = [row for row in events if row["kind"] == "decision"]
    assert len(decisions) == 2
    assert decisions[-1]["decision_id"] == 3
    assert len(heartbeats) == 2


def test_recovery_counter_creates_failure_bundle(tmp_path):
    observer = RuntimeObserver(
        {"console": False, "failure_bundle_cooldown_seconds": 1.0},
        tmp_path,
    )
    frame = np.zeros((80, 120, 3), dtype=np.uint8)
    now = time.monotonic()
    base = {
        "phase": "menu",
        "menu_screen": "main_menu",
        "recoveries": 0,
        "session_progress_recoveries": 0,
        "menu_transaction_failures": 0,
        "session_unknown_captures": 0,
    }
    observer.record_cycle(1, frame, None, base, "menu: observing", now)
    failed = dict(base)
    failed["menu_transaction_failures"] = 1
    observer.record_cycle(2, frame, frame, failed, "menu: safe backout", now + 2.0)

    bundles = [path for path in (tmp_path / "failures").iterdir() if path.is_dir()]
    assert len(bundles) == 1
    bundle = bundles[0]
    assert (bundle / "frame.png").exists()
    assert (bundle / "frame-before.png").exists()
    assert (bundle / "state.json").exists()
    assert (bundle / "recent-events.json").exists()
    assert (bundle / "recent-inputs.json").exists()
    payload = json.loads((bundle / "state.json").read_text())
    assert "menu-transition-failure" in payload["reason"]


def test_summary_reads_session_and_runtime_logs(tmp_path):
    (tmp_path / "session.json").write_text(
        json.dumps(
            {
                "games_started": 4,
                "games_completed": 3,
                "progress_recoveries": 2,
                "hard_recoveries": 1,
                "unknown_captures": 5,
            }
        )
    )
    (tmp_path / "events.jsonl").write_text(
        json.dumps({"kind": "decision", "action": "snap ball"}) + "\n"
    )
    (tmp_path / "heartbeat.jsonl").write_text(
        json.dumps({"kind": "heartbeat", "uptime_seconds": 3661}) + "\n"
    )
    (tmp_path / "input.jsonl").write_text(
        json.dumps({"kind": "tap", "action": "cross"}) + "\n"
    )
    (tmp_path / "failures").mkdir()
    (tmp_path / "failures" / "one").mkdir()

    summary = summarize_runtime(tmp_path)
    assert summary["games_started"] == 4
    assert summary["games_completed"] == 3
    assert summary["uptime_seconds"] == 3661
    assert summary["failure_bundles"] == 1
    assert summary["input_kinds"]["tap"] == 1
