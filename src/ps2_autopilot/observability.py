from __future__ import annotations

from collections import Counter, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
import traceback
from typing import Any, Callable

import cv2
import numpy as np

from .controllers.base import Controller


@dataclass(frozen=True)
class InputEvent:
    seq: int
    utc: str
    monotonic: float
    decision_id: int | None
    kind: str
    action: str | None = None
    duration: float | None = None
    x: float | None = None
    y: float | None = None


class JsonlWriter:
    def __init__(self, path: Path, max_bytes: int = 8_000_000) -> None:
        self.path = path
        self.max_bytes = max(256_000, int(max_bytes))
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _rotate(self) -> None:
        try:
            if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
                return
            backup = self.path.with_suffix(self.path.suffix + ".1")
            backup.unlink(missing_ok=True)
            self.path.replace(backup)
        except OSError:
            pass

    def write(self, row: dict[str, Any]) -> None:
        self._rotate()
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        except OSError:
            pass


class TracingController(Controller):
    """Controller proxy that records discrete inputs and sampled stick commands."""

    def __init__(
        self,
        inner: Controller,
        sink: Callable[[InputEvent], None],
        stick_interval_seconds: float = 0.50,
        stick_delta: float = 0.08,
    ) -> None:
        self.inner = inner
        self.sink = sink
        self.stick_interval_seconds = max(0.05, float(stick_interval_seconds))
        self.stick_delta = max(0.01, float(stick_delta))
        self.seq = 0
        self.decision_id: int | None = None
        self._last_stick: dict[str, tuple[float, float, float]] = {}

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def set_decision_id(self, value: int | None) -> None:
        self.decision_id = value

    def _emit(
        self,
        kind: str,
        action: str | None = None,
        duration: float | None = None,
        x: float | None = None,
        y: float | None = None,
    ) -> None:
        self.seq += 1
        self.sink(
            InputEvent(
                seq=self.seq,
                utc=self._utc_now(),
                monotonic=time.monotonic(),
                decision_id=self.decision_id,
                kind=kind,
                action=action,
                duration=duration,
                x=x,
                y=y,
            )
        )

    def _stick_should_log(self, key: str, x: float, y: float) -> bool:
        now = time.monotonic()
        last = self._last_stick.get(key)
        if last is None:
            self._last_stick[key] = (x, y, now)
            return True
        lx, ly, lt = last
        changed = abs(x - lx) >= self.stick_delta or abs(y - ly) >= self.stick_delta
        due = now - lt >= self.stick_interval_seconds
        if changed or due:
            self._last_stick[key] = (x, y, now)
            return True
        return False

    def tap(self, action: str, duration: float = 0.08) -> None:
        self._emit("tap", action=action, duration=float(duration))
        self.inner.tap(action, duration)

    def hold(self, action: str) -> None:
        self._emit("hold", action=action)
        self.inner.hold(action)

    def release(self, action: str) -> None:
        self._emit("release", action=action)
        self.inner.release(action)

    def release_all(self) -> None:
        self._emit("release_all")
        self.inner.release_all()

    def set_left_stick(self, x: float, y: float) -> None:
        x, y = float(x), float(y)
        if self._stick_should_log("left_stick", x, y):
            self._emit("left_stick", x=x, y=y)
        self.inner.set_left_stick(x, y)

    def set_right_stick(self, x: float, y: float) -> None:
        x, y = float(x), float(y)
        if self._stick_should_log("right_stick", x, y):
            self._emit("right_stick", x=x, y=y)
        self.inner.set_right_stick(x, y)


class RuntimeObserver:
    """Structured observability for long-running autonomous gameplay."""

    def __init__(self, cfg: dict, root: Path) -> None:
        self.cfg = dict(cfg)
        self.enabled = bool(cfg.get("enabled", True))
        self.console = bool(cfg.get("console", True))
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.failures_dir = self.root / "failures"
        self.failures_dir.mkdir(parents=True, exist_ok=True)

        max_log_bytes = int(cfg.get("max_log_bytes", 8_000_000))
        self.events = JsonlWriter(self.root / "events.jsonl", max_log_bytes)
        self.heartbeats = JsonlWriter(self.root / "heartbeat.jsonl", max_log_bytes)
        self.inputs = JsonlWriter(self.root / "input.jsonl", max_log_bytes)
        self.errors_path = self.root / "errors.log"

        self.heartbeat_seconds = max(1.0, float(cfg.get("heartbeat_seconds", 5.0)))
        self.history_size = max(50, int(cfg.get("history_size", 240)))
        self.input_history_size = max(50, int(cfg.get("input_history_size", 300)))
        self.failure_cooldown_seconds = max(
            1.0, float(cfg.get("failure_bundle_cooldown_seconds", 8.0))
        )
        self.max_failure_bundles = max(10, int(cfg.get("max_failure_bundles", 80)))

        self.event_history: deque[dict[str, Any]] = deque(maxlen=self.history_size)
        self.input_history: deque[dict[str, Any]] = deque(maxlen=self.input_history_size)
        self.decision_id = 0
        self.last_heartbeat = -1e9
        self.last_failure_at = -1e9
        self.last_state_signature: tuple[Any, ...] | None = None
        self.last_action: str | None = None
        self.last_counters: dict[str, int] = {}
        self.started_monotonic = time.monotonic()

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def next_decision_id(self) -> int:
        self.decision_id += 1
        return self.decision_id

    @staticmethod
    def _state_signature(state: dict[str, Any]) -> tuple[Any, ...]:
        return (
            state.get("phase"),
            state.get("menu_screen"),
            state.get("game_state"),
            state.get("possession"),
            state.get("play_intent"),
            state.get("down"),
            state.get("quarter"),
            state.get("plays_started"),
            state.get("plays_completed"),
        )

    @staticmethod
    def _compact_state(state: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "status",
            "profile",
            "phase",
            "phase_age",
            "menu_screen",
            "menu_confidence",
            "menu_reason",
            "menu_highlight",
            "menu_highlight_confidence",
            "menu_pending",
            "menu_expected",
            "game_state",
            "possession",
            "possession_confidence",
            "play_intent",
            "situation",
            "down",
            "distance",
            "quarter",
            "clock_seconds",
            "field_green",
            "field_center",
            "motion_target",
            "motion_target_y",
            "motion",
            "still_seconds",
            "semantic_still_seconds",
            "recoveries",
            "session_progress_recoveries",
            "progress_recovery_level",
            "progress_recovery_reason",
            "menu_transaction_retries",
            "menu_transaction_failures",
            "menu_verified_transitions",
            "session_games_started",
            "session_games_completed",
            "session_unknown_captures",
            "loop_budget_ms",
            "loop_p50_ms",
            "loop_p95_ms",
            "loop_overrun_ratio",
            "template_call_ms",
            "template_scan_ms",
            "template_result_age_ms",
            "template_scan_errors",
            "nfs_policy_version",
            "nfs_phase",
            "nfs_screen",
            "nfs_raw_screen",
            "nfs_road_confidence",
            "nfs_race_entries",
            "nfs_verified_race_entries",
            "nfs_gameplay_reacquisitions",
            "nfs_recoveries",
            "nfs_recovery_storm_count",
            "nfs_recovery_storm_limit",
            "nfs_recovery_storm_triggers",
            "nfs_hard_restart_stage",
            "nfs_hard_restart_attempts",
            "nfs_hard_restart_successes",
            "nfs_hard_restart_failures",
            "nfs_hard_quit_stage",
            "nfs_hard_quit_attempts",
            "nfs_hard_quit_successes",
            "nfs_hard_quit_failures",
            "ocr_text",
        )
        return {key: state.get(key) for key in keys if key in state}

    def _append_event(self, row: dict[str, Any]) -> None:
        self.event_history.append(row)
        if self.enabled:
            self.events.write(row)

    def record_input(self, event: InputEvent) -> None:
        row = {"kind": "input", **asdict(event)}
        self.input_history.append(row)
        if self.enabled:
            self.inputs.write(row)

    def _console_line(self, state: dict[str, Any], action: str) -> None:
        if not self.console:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        screen = str(state.get("menu_screen") or state.get("game_state") or "?").upper()
        phase = str(state.get("phase") or "?").upper()
        conf = state.get("menu_confidence")
        conf_text = "" if conf is None else f" {float(conf):.2f}"
        print(f"[{stamp}] {phase:<10} {screen}{conf_text} | {action}", flush=True)

    def record_cycle(
        self,
        decision_id: int,
        frame: np.ndarray,
        previous_frame: np.ndarray | None,
        state: dict[str, Any],
        action: str,
        now: float,
    ) -> None:
        if not self.enabled and not self.console:
            return

        signature = self._state_signature(state)
        state_changed = signature != self.last_state_signature
        action_changed = action != self.last_action
        if state_changed or action_changed:
            row = {
                "utc": self._utc_now(),
                "kind": "decision",
                "decision_id": decision_id,
                "action": action,
                "state_changed": state_changed,
                "state": self._compact_state(state),
            }
            self._append_event(row)
            self._console_line(state, action)
            self.last_state_signature = signature
            self.last_action = action

        if now - self.last_heartbeat >= self.heartbeat_seconds:
            heartbeat = {
                "utc": self._utc_now(),
                "kind": "heartbeat",
                "decision_id": decision_id,
                "uptime_seconds": round(now - self.started_monotonic, 1),
                "action": action,
                "state": self._compact_state(state),
            }
            if self.enabled:
                self.heartbeats.write(heartbeat)
            self.last_heartbeat = now

        self._check_failure_triggers(frame, previous_frame, state, action, now, decision_id)

    def _counter_increased(self, state: dict[str, Any], key: str) -> bool:
        try:
            value = int(state.get(key, 0) or 0)
        except (TypeError, ValueError):
            value = 0
        previous = self.last_counters.get(key, value)
        self.last_counters[key] = value
        return value > previous

    def _check_failure_triggers(
        self,
        frame: np.ndarray,
        previous_frame: np.ndarray | None,
        state: dict[str, Any],
        action: str,
        now: float,
        decision_id: int,
    ) -> None:
        reasons: list[str] = []
        for key, label in (
            ("recoveries", "motion-watchdog-recovery"),
            ("session_progress_recoveries", "semantic-progress-recovery"),
            ("menu_transaction_failures", "menu-transition-failure"),
            ("session_unknown_captures", "unknown-screen-capture"),
            ("nfs_recovery_storm_triggers", "nfs-recovery-storm"),
            ("nfs_hard_restart_attempts", "nfs-hard-restart"),
            ("nfs_hard_quit_attempts", "nfs-hard-quit"),
        ):
            if self._counter_increased(state, key):
                reasons.append(label)

        if state.get("progress_recovery_reason"):
            reasons.append(str(state["progress_recovery_reason"]))

        if not reasons or now - self.last_failure_at < self.failure_cooldown_seconds:
            return
        self.last_failure_at = now
        self.failure_bundle(
            reason="; ".join(dict.fromkeys(reasons)),
            frame=frame,
            previous_frame=previous_frame,
            state=state,
            decision_id=decision_id,
            action=action,
        )

    def _prune_failures(self) -> None:
        try:
            dirs = sorted(
                (path for path in self.failures_dir.iterdir() if path.is_dir()),
                key=lambda p: p.stat().st_mtime,
            )
        except OSError:
            return
        while len(dirs) > self.max_failure_bundles:
            victim = dirs.pop(0)
            try:
                for child in victim.iterdir():
                    child.unlink(missing_ok=True)
                victim.rmdir()
            except OSError:
                break

    def failure_bundle(
        self,
        reason: str,
        frame: np.ndarray | None,
        previous_frame: np.ndarray | None,
        state: dict[str, Any] | None,
        decision_id: int | None,
        action: str | None,
        exception: BaseException | None = None,
    ) -> Path | None:
        if not self.enabled:
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        folder = self.failures_dir / stamp
        try:
            folder.mkdir(parents=True, exist_ok=False)
            if frame is not None and frame.size:
                cv2.imwrite(str(folder / "frame.png"), frame)
            if previous_frame is not None and previous_frame.size:
                cv2.imwrite(str(folder / "frame-before.png"), previous_frame)
            payload = {
                "utc": self._utc_now(),
                "reason": reason,
                "decision_id": decision_id,
                "action": action,
                "state": self._compact_state(state or {}),
            }
            if exception is not None:
                payload["exception"] = {
                    "type": type(exception).__name__,
                    "message": str(exception),
                }
            (folder / "state.json").write_text(
                json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8"
            )
            (folder / "recent-events.json").write_text(
                json.dumps(list(self.event_history), indent=2, default=str), encoding="utf-8"
            )
            (folder / "recent-inputs.json").write_text(
                json.dumps(list(self.input_history), indent=2, default=str), encoding="utf-8"
            )
            (folder / "ocr.txt").write_text(
                str((state or {}).get("ocr_text") or ""), encoding="utf-8"
            )
            self._append_event(
                {
                    "utc": self._utc_now(),
                    "kind": "failure_bundle",
                    "decision_id": decision_id,
                    "reason": reason,
                    "path": str(folder),
                    "action": action,
                }
            )
            if self.console:
                print(f"[OBS] failure bundle: {folder} ({reason})", flush=True)
            self._prune_failures()
            return folder
        except OSError:
            return None

    def record_exception(
        self,
        exc: BaseException,
        frame: np.ndarray | None = None,
        previous_frame: np.ndarray | None = None,
        state: dict[str, Any] | None = None,
        decision_id: int | None = None,
        action: str | None = None,
    ) -> None:
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        try:
            with self.errors_path.open("a", encoding="utf-8") as fh:
                fh.write(f"\n[{self._utc_now()}] {text}\n")
        except OSError:
            pass
        self.failure_bundle(
            reason=f"exception: {type(exc).__name__}",
            frame=frame,
            previous_frame=previous_frame,
            state=state,
            decision_id=decision_id,
            action=action,
            exception=exc,
        )
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        return rows
    return rows


def summarize_runtime(root: Path) -> dict[str, Any]:
    events = read_jsonl(root / "events.jsonl")
    heartbeats = read_jsonl(root / "heartbeat.jsonl")
    inputs = read_jsonl(root / "input.jsonl")
    event_kinds = Counter(str(row.get("kind") or "unknown") for row in events)
    actions = Counter(
        str(row.get("action"))
        for row in events
        if row.get("kind") == "decision" and row.get("action")
    )
    input_kinds = Counter(str(row.get("kind") or "unknown") for row in inputs)

    session: dict[str, Any] = {}
    try:
        session = json.loads((root / "session.json").read_text(encoding="utf-8"))
    except Exception:
        pass

    uptime = 0.0
    if heartbeats:
        try:
            uptime = float(heartbeats[-1].get("uptime_seconds") or 0.0)
        except (TypeError, ValueError):
            pass

    failure_bundles = 0
    failures_dir = root / "failures"
    if failures_dir.exists():
        try:
            failure_bundles = sum(1 for path in failures_dir.iterdir() if path.is_dir())
        except OSError:
            pass

    return {
        "uptime_seconds": uptime,
        "games_started": int(session.get("games_started", 0) or 0),
        "games_completed": int(session.get("games_completed", 0) or 0),
        "progress_recoveries": int(session.get("progress_recoveries", 0) or 0),
        "hard_recoveries": int(session.get("hard_recoveries", 0) or 0),
        "unknown_captures": int(session.get("unknown_captures", 0) or 0),
        "failure_bundles": failure_bundles,
        "event_count": len(events),
        "heartbeat_count": len(heartbeats),
        "input_count": len(inputs),
        "event_kinds": event_kinds,
        "input_kinds": input_kinds,
        "top_actions": actions.most_common(8),
    }
