from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Protocol

from .config import AppConfig
from .window import PCSX2Window


@dataclass(frozen=True)
class ProbeResult:
    available: bool
    pid: int | None = None
    error: str | None = None


@dataclass(frozen=True)
class EmulatorLaunchConfig:
    enabled: bool = False
    command: tuple[str, ...] = ()
    cwd: str | None = None
    wait_seconds: float = 45.0
    failure_escalation_count: int = 3
    terminate_existing_on_escalation: bool = False

    @classmethod
    def from_raw(cls, raw: dict[str, Any] | None) -> "EmulatorLaunchConfig":
        data = dict(raw or {})
        enabled = bool(data.get("enabled", False))
        command_raw = data.get("command", [])
        if isinstance(command_raw, str):
            # A single shell string is intentionally rejected. Keeping argv explicit
            # avoids quoting surprises and prevents the supervisor from needing shell=True.
            command = ()
        else:
            try:
                command = tuple(str(part) for part in command_raw if str(part))
            except TypeError:
                command = ()
        if enabled and not command:
            raise ValueError(
                "supervisor.emulator.enabled=true requires an explicit command list"
            )
        return cls(
            enabled=enabled,
            command=command,
            cwd=(str(data["cwd"]) if data.get("cwd") else None),
            wait_seconds=max(5.0, float(data.get("wait_seconds", 45.0))),
            failure_escalation_count=max(
                1, int(data.get("failure_escalation_count", 3))
            ),
            terminate_existing_on_escalation=bool(
                data.get("terminate_existing_on_escalation", False)
            ),
        )


@dataclass(frozen=True)
class SupervisorConfig:
    restart_delay_seconds: float = 5.0
    poll_seconds: float = 1.0
    window_loss_grace_seconds: float = 8.0
    emulator: EmulatorLaunchConfig = EmulatorLaunchConfig()

    @classmethod
    def from_app_config(cls, config: AppConfig) -> "SupervisorConfig":
        raw = dict(config.raw.get("supervisor", {}) or {})
        return cls(
            restart_delay_seconds=max(
                0.25, float(raw.get("restart_delay_seconds", 5.0))
            ),
            poll_seconds=max(0.20, float(raw.get("poll_seconds", 1.0))),
            window_loss_grace_seconds=max(
                1.0, float(raw.get("window_loss_grace_seconds", 8.0))
            ),
            emulator=EmulatorLaunchConfig.from_raw(raw.get("emulator")),
        )


class ChildProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class SupervisorTelemetry:
    """Atomic supervisor state plus an append-only event stream.

    ``supervisor.json`` is consumed by the running AutoPilot so restart counts/reasons
    become ordinary overlay/verbose telemetry. ``supervisor.jsonl`` remains useful when
    AutoPilot itself is down and therefore cannot write its normal logs.
    """

    def __init__(self, root: Path, *, launch_enabled: bool) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = root / "supervisor.json"
        self.events_path = root / "supervisor.jsonl"
        self.state: dict[str, Any] = {
            "supervisor_status": "starting",
            "supervisor_autopilot_restarts": 0,
            "supervisor_emulator_restarts": 0,
            "supervisor_consecutive_autopilot_failures": 0,
            "supervisor_emulator_launch_enabled": bool(launch_enabled),
            "supervisor_emulator_pid": None,
            "supervisor_last_restart_reason": "startup",
            "supervisor_last_event_at": self._utc_now(),
        }
        self.write()

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def write(self) -> None:
        temporary = self.state_path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(self.state, indent=2, sort_keys=True), encoding="utf-8"
            )
            temporary.replace(self.state_path)
        except OSError:
            pass

    def event(self, kind: str, reason: str, **extra: Any) -> None:
        stamp = self._utc_now()
        self.state["supervisor_last_restart_reason"] = reason
        self.state["supervisor_last_event_at"] = stamp
        self.state.update(extra)
        self.write()
        row = {"utc": stamp, "kind": kind, "reason": reason, **extra}
        try:
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        except OSError:
            pass

    def increment(self, key: str) -> int:
        value = int(self.state.get(key, 0) or 0) + 1
        self.state[key] = value
        self.write()
        return value


class WindowProbe:
    def __init__(self, title_contains: str) -> None:
        self.window = PCSX2Window(title_contains)

    def __call__(self) -> ProbeResult:
        try:
            self.window.locate()
            return ProbeResult(True, self.window.pid, None)
        except Exception as exc:
            return ProbeResult(False, None, f"{type(exc).__name__}: {exc}")


@dataclass(frozen=True)
class ChildOutcome:
    exit_code: int
    reason: str
    window_lost: bool = False
    stop_requested: bool = False


class AutopilotSupervisor:
    """Supervise AutoPilot and optionally escalate to a PCSX2 relaunch.

    The emulator launch path is disabled by default and requires an explicit argv list.
    AutoPilot can still be restarted indefinitely exactly as the historical batch loop
    did. When emulator relaunch is enabled, missing render/process evidence can trigger
    the configured launch command; repeated AutoPilot failures may escalate to a full
    emulator restart only when the separate destructive termination opt-in is enabled.
    """

    def __init__(
        self,
        config_path: str | Path,
        app_config: AppConfig,
        project_root: Path,
        *,
        child_factory: Callable[[list[str]], ChildProcess] | None = None,
        probe: Callable[[], ProbeResult] | None = None,
        emulator_launcher: Callable[[tuple[str, ...], str | None], Any] | None = None,
        process_terminator: Callable[[int], bool] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config_path = Path(config_path)
        self.app_config = app_config
        self.project_root = project_root
        self.cfg = SupervisorConfig.from_app_config(app_config)
        self.runtime_root = project_root / "runtime"
        self.stop_path = self.runtime_root / "STOP24X7"
        self.telemetry = SupervisorTelemetry(
            self.runtime_root, launch_enabled=self.cfg.emulator.enabled
        )
        self.child_factory = child_factory or self._spawn_autopilot
        self.probe = probe or WindowProbe(app_config.window_title_contains)
        self.emulator_launcher = emulator_launcher or self._spawn_emulator
        self.process_terminator = process_terminator or self._terminate_pid
        self.monotonic = monotonic
        self.sleep = sleep
        self._stopping = False

    def _spawn_autopilot(self, command: list[str]) -> ChildProcess:
        return subprocess.Popen(command, cwd=str(self.project_root))

    def _spawn_emulator(self, command: tuple[str, ...], cwd: str | None) -> Any:
        launch_cwd = cwd
        if launch_cwd:
            launch_path = Path(launch_cwd)
            if not launch_path.is_absolute():
                launch_cwd = str((self.project_root / launch_path).resolve())
        kwargs: dict[str, Any] = {"cwd": launch_cwd or str(self.project_root)}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        return subprocess.Popen(list(command), **kwargs)

    @staticmethod
    def _terminate_pid(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            if os.name == "nt":
                completed = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return completed.returncode == 0
            os.kill(pid, signal.SIGTERM)
            return True
        except (OSError, ValueError):
            return False

    def _stop_requested(self) -> bool:
        return self._stopping or self.stop_path.exists()

    def _safe_stop_child(self, child: ChildProcess) -> None:
        if child.poll() is not None:
            return
        try:
            child.terminate()
            child.wait(timeout=5.0)
            return
        except Exception:
            pass
        try:
            child.kill()
            child.wait(timeout=3.0)
        except Exception:
            pass

    def _wait_for_window(self, timeout: float) -> ProbeResult:
        deadline = self.monotonic() + max(0.0, timeout)
        last = ProbeResult(False, None, "not checked")
        while not self._stop_requested():
            last = self.probe()
            if last.available:
                return last
            if self.monotonic() >= deadline:
                return last
            self.sleep(self.cfg.poll_seconds)
        return last

    def _launch_emulator(self, reason: str) -> bool:
        emulator = self.cfg.emulator
        if not emulator.enabled:
            self.telemetry.event("emulator-relaunch-skipped", reason)
            return False
        count = self.telemetry.increment("supervisor_emulator_restarts")
        self.telemetry.state["supervisor_status"] = "launching-emulator"
        self.telemetry.event(
            "emulator-launch",
            reason,
            supervisor_emulator_restarts=count,
        )
        try:
            self.emulator_launcher(emulator.command, emulator.cwd)
        except Exception as exc:
            self.telemetry.state["supervisor_status"] = "emulator-launch-failed"
            self.telemetry.event(
                "emulator-launch-failed",
                reason,
                supervisor_launch_error=f"{type(exc).__name__}: {exc}",
            )
            return False

        probe = self._wait_for_window(emulator.wait_seconds)
        if not probe.available:
            self.telemetry.state["supervisor_status"] = "emulator-window-timeout"
            self.telemetry.event(
                "emulator-window-timeout",
                reason,
                supervisor_probe_error=probe.error,
            )
            return False
        self.telemetry.state["supervisor_status"] = "running"
        self.telemetry.state["supervisor_emulator_pid"] = probe.pid
        self.telemetry.event(
            "emulator-window-ready",
            reason,
            supervisor_emulator_pid=probe.pid,
        )
        return True

    def _monitor_child(self, child: ChildProcess) -> ChildOutcome:
        missing_since: float | None = None
        while True:
            if self._stop_requested():
                self._safe_stop_child(child)
                return ChildOutcome(0, "operator-stop", stop_requested=True)

            exit_code = child.poll()
            if exit_code is not None:
                return ChildOutcome(int(exit_code), f"autopilot-exit:{int(exit_code)}")

            probe = self.probe()
            if probe.available:
                missing_since = None
                if probe.pid != self.telemetry.state.get("supervisor_emulator_pid"):
                    self.telemetry.state["supervisor_emulator_pid"] = probe.pid
                    self.telemetry.write()
            else:
                now = self.monotonic()
                if missing_since is None:
                    missing_since = now
                    self.telemetry.event(
                        "pcsx2-window-missing",
                        "pcsx2-window-missing",
                        supervisor_probe_error=probe.error,
                    )
                elif now - missing_since >= self.cfg.window_loss_grace_seconds:
                    self._safe_stop_child(child)
                    return ChildOutcome(
                        1, "pcsx2-window-lost", window_lost=True
                    )
            self.sleep(self.cfg.poll_seconds)

    def _maybe_escalate_emulator(self, outcome: ChildOutcome) -> None:
        emulator = self.cfg.emulator
        if not emulator.enabled:
            return

        probe = self.probe()
        if not probe.available:
            self._launch_emulator(outcome.reason)
            return

        failures = int(
            self.telemetry.state.get("supervisor_consecutive_autopilot_failures", 0)
            or 0
        )
        if failures < emulator.failure_escalation_count:
            return

        if not emulator.terminate_existing_on_escalation:
            self.telemetry.event(
                "emulator-escalation-blocked",
                "repeated-autopilot-failures",
                supervisor_emulator_pid=probe.pid,
                supervisor_escalation_failures=failures,
            )
            return

        if probe.pid is None or not self.process_terminator(probe.pid):
            self.telemetry.event(
                "emulator-terminate-failed",
                "repeated-autopilot-failures",
                supervisor_emulator_pid=probe.pid,
            )
            return

        self.telemetry.event(
            "emulator-terminated",
            "repeated-autopilot-failures",
            supervisor_emulator_pid=probe.pid,
        )
        self.sleep(min(3.0, self.cfg.restart_delay_seconds))
        if self._launch_emulator("repeated-autopilot-failures"):
            self.telemetry.state["supervisor_consecutive_autopilot_failures"] = 0
            self.telemetry.write()

    def run(self) -> int:
        self.telemetry.state["supervisor_status"] = "running"
        self.telemetry.write()

        # If explicit emulator relaunch is enabled, make startup self-contained too.
        startup_probe = self.probe()
        if self.cfg.emulator.enabled and not startup_probe.available:
            self._launch_emulator("startup-window-missing")
        elif startup_probe.available:
            self.telemetry.state["supervisor_emulator_pid"] = startup_probe.pid
            self.telemetry.write()

        try:
            while not self._stop_requested():
                command = [
                    sys.executable,
                    "-m",
                    "ps2_autopilot.cli",
                    "--config",
                    str(self.config_path),
                ]
                self.telemetry.state["supervisor_status"] = "starting-autopilot"
                self.telemetry.write()
                child = self.child_factory(command)
                self.telemetry.state["supervisor_status"] = "running"
                self.telemetry.event(
                    "autopilot-start",
                    "initial" if self.telemetry.state["supervisor_autopilot_restarts"] == 0 else "restart",
                    supervisor_autopilot_pid=getattr(child, "pid", None),
                )

                outcome = self._monitor_child(child)
                if outcome.stop_requested or outcome.exit_code == 0:
                    self.telemetry.state["supervisor_status"] = "stopped"
                    self.telemetry.event("supervisor-stop", outcome.reason)
                    return 0

                restarts = self.telemetry.increment("supervisor_autopilot_restarts")
                failures = self.telemetry.increment(
                    "supervisor_consecutive_autopilot_failures"
                )
                self.telemetry.state["supervisor_status"] = "recovering"
                self.telemetry.event(
                    "autopilot-exit",
                    outcome.reason,
                    supervisor_autopilot_restarts=restarts,
                    supervisor_consecutive_autopilot_failures=failures,
                )

                self._maybe_escalate_emulator(outcome)
                if self._stop_requested():
                    break
                self.sleep(self.cfg.restart_delay_seconds)
        except KeyboardInterrupt:
            self._stopping = True
            self.telemetry.state["supervisor_status"] = "stopped"
            self.telemetry.event("supervisor-stop", "keyboard-interrupt")
            return 0

        self.telemetry.state["supervisor_status"] = "stopped"
        self.telemetry.event("supervisor-stop", "stop-file")
        return 0
