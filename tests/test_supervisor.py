from __future__ import annotations

import json
from pathlib import Path

import pytest

from ps2_autopilot.config import AppConfig
from ps2_autopilot.supervisor import (
    AutopilotSupervisor,
    ChildOutcome,
    EmulatorLaunchConfig,
    ProbeResult,
    SupervisorConfig,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += float(seconds)


class FakeChild:
    def __init__(self, pid: int = 9001, exit_code: int | None = None) -> None:
        self.pid = pid
        self.exit_code = exit_code
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.exit_code

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        if self.exit_code is None:
            self.exit_code = 0
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True
        self.exit_code = 0

    def kill(self) -> None:
        self.killed = True
        self.exit_code = -9


class ProbeSequence:
    def __init__(self, *results: ProbeResult) -> None:
        self.results = list(results)
        self.last = results[-1] if results else ProbeResult(False, None, "empty")
        self.calls = 0

    def __call__(self) -> ProbeResult:
        self.calls += 1
        if self.results:
            self.last = self.results.pop(0)
        return self.last


def app_config(supervisor: dict | None = None) -> AppConfig:
    return AppConfig(
        raw={
            "window_title_contains": "PCSX2",
            "supervisor": supervisor or {},
        }
    )


def make_supervisor(
    tmp_path: Path,
    *,
    supervisor_cfg: dict | None = None,
    probe=None,
    clock: FakeClock | None = None,
    emulator_launcher=None,
    process_terminator=None,
) -> AutopilotSupervisor:
    clock = clock or FakeClock()
    return AutopilotSupervisor(
        "config/test.yaml",
        app_config(supervisor_cfg),
        tmp_path,
        child_factory=lambda _command: FakeChild(),
        probe=probe or (lambda: ProbeResult(True, 1234, None)),
        emulator_launcher=emulator_launcher,
        process_terminator=process_terminator,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


def enabled_emulator(**overrides) -> dict:
    cfg = {
        "restart_delay_seconds": 1.0,
        "poll_seconds": 0.25,
        "window_loss_grace_seconds": 1.0,
        "stable_run_seconds": 120.0,
        "emulator": {
            "enabled": True,
            "command": ["pcsx2-qt.exe", "game.iso"],
            "wait_seconds": 5.0,
            "failure_escalation_count": 3,
            "terminate_existing_on_escalation": False,
        },
    }
    for key, value in overrides.items():
        if key.startswith("emulator_"):
            cfg["emulator"][key.removeprefix("emulator_")] = value
        else:
            cfg[key] = value
    return cfg


def test_emulator_launch_is_disabled_by_default_and_shell_string_is_rejected():
    default = EmulatorLaunchConfig.from_raw(None)
    assert default.enabled is False
    assert default.command == ()

    with pytest.raises(ValueError, match="explicit command list"):
        EmulatorLaunchConfig.from_raw(
            {"enabled": True, "command": '"C:/PCSX2/pcsx2-qt.exe" game.iso'}
        )


def test_supervisor_config_clamps_recovery_windows():
    cfg = SupervisorConfig.from_app_config(
        app_config(
            {
                "restart_delay_seconds": 0,
                "poll_seconds": 0,
                "window_loss_grace_seconds": 0,
                "stable_run_seconds": 0,
            }
        )
    )
    assert cfg.restart_delay_seconds == 0.25
    assert cfg.poll_seconds == 0.20
    assert cfg.window_loss_grace_seconds == 1.0
    assert cfg.stable_run_seconds == 5.0


def test_window_loss_grace_stops_autopilot_and_marks_process_loss(tmp_path):
    clock = FakeClock()
    probe = lambda: ProbeResult(False, None, "window gone")
    supervisor = make_supervisor(
        tmp_path,
        supervisor_cfg={
            "poll_seconds": 0.25,
            "window_loss_grace_seconds": 1.0,
        },
        probe=probe,
        clock=clock,
    )
    child = FakeChild()

    outcome = supervisor._monitor_child(child)

    assert outcome.window_lost is True
    assert outcome.reason == "pcsx2-window-lost"
    assert outcome.runtime_seconds >= 1.0
    assert child.terminated is True


def test_emulator_launch_waits_for_render_window_and_counts_success(tmp_path):
    clock = FakeClock()
    probe = ProbeSequence(
        ProbeResult(False, None, "booting"),
        ProbeResult(False, None, "booting"),
        ProbeResult(True, 4242, None),
    )
    launches: list[tuple[tuple[str, ...], str | None]] = []

    class LaunchHandle:
        pid = 3333

    supervisor = make_supervisor(
        tmp_path,
        supervisor_cfg=enabled_emulator(),
        probe=probe,
        clock=clock,
        emulator_launcher=lambda command, cwd: (
            launches.append((command, cwd)) or LaunchHandle()
        ),
    )

    assert supervisor._launch_emulator("pcsx2-window-lost") is True
    assert launches == [(('pcsx2-qt.exe', 'game.iso'), None)]
    assert supervisor.telemetry.state["supervisor_emulator_launch_attempts"] == 1
    assert supervisor.telemetry.state["supervisor_emulator_restarts"] == 1
    assert supervisor.telemetry.state["supervisor_emulator_pid"] == 4242


def test_failed_emulator_launch_attempt_is_not_counted_as_restart(tmp_path):
    clock = FakeClock()
    supervisor = make_supervisor(
        tmp_path,
        supervisor_cfg=enabled_emulator(),
        probe=lambda: ProbeResult(False, None, "no window"),
        clock=clock,
        emulator_launcher=lambda _command, _cwd: (_ for _ in ()).throw(
            OSError("launch failed")
        ),
    )

    assert supervisor._launch_emulator("startup-window-missing") is False
    assert supervisor.telemetry.state["supervisor_emulator_launch_attempts"] == 1
    assert supervisor.telemetry.state["supervisor_emulator_restarts"] == 0
    assert supervisor.telemetry.state["supervisor_status"] == "emulator-launch-failed"


def test_repeated_failures_cannot_kill_existing_pcsx2_without_second_opt_in(tmp_path):
    terminated: list[int] = []
    supervisor = make_supervisor(
        tmp_path,
        supervisor_cfg=enabled_emulator(emulator_failure_escalation_count=2),
        probe=lambda: ProbeResult(True, 5151, None),
        process_terminator=lambda pid: (terminated.append(pid) or True),
    )
    supervisor.telemetry.state["supervisor_consecutive_autopilot_failures"] = 2

    supervisor._maybe_escalate_emulator(ChildOutcome(1, "autopilot-exit:1"))

    assert terminated == []
    events = supervisor.telemetry.events_path.read_text(encoding="utf-8")
    assert "emulator-escalation-blocked" in events


def test_opted_in_repeated_failure_escalation_terminates_and_relaunches(tmp_path):
    clock = FakeClock()
    probe = ProbeSequence(
        ProbeResult(True, 5151, None),
        ProbeResult(False, None, "restarting"),
        ProbeResult(True, 6262, None),
    )
    terminated: list[int] = []
    launched: list[tuple[str, ...]] = []

    class LaunchHandle:
        pid = 6161

    supervisor = make_supervisor(
        tmp_path,
        supervisor_cfg=enabled_emulator(
            emulator_failure_escalation_count=2,
            emulator_terminate_existing_on_escalation=True,
        ),
        probe=probe,
        clock=clock,
        emulator_launcher=lambda command, _cwd: (
            launched.append(command) or LaunchHandle()
        ),
        process_terminator=lambda pid: (terminated.append(pid) or True),
    )
    supervisor.telemetry.state["supervisor_consecutive_autopilot_failures"] = 2

    supervisor._maybe_escalate_emulator(ChildOutcome(1, "autopilot-exit:1"))

    assert terminated == [5151]
    assert launched == [('pcsx2-qt.exe', 'game.iso')]
    assert supervisor.telemetry.state["supervisor_emulator_restarts"] == 1
    assert supervisor.telemetry.state["supervisor_emulator_pid"] == 6262
    assert supervisor.telemetry.state["supervisor_consecutive_autopilot_failures"] == 0


def test_stable_run_resets_failure_streak_before_counting_new_crash(tmp_path):
    supervisor = make_supervisor(
        tmp_path,
        supervisor_cfg={"stable_run_seconds": 60.0},
    )
    supervisor.telemetry.state["supervisor_consecutive_autopilot_failures"] = 4

    restarts, failures = supervisor._record_autopilot_failure(
        ChildOutcome(1, "autopilot-exit:1", runtime_seconds=180.0)
    )

    assert restarts == 1
    assert failures == 1
    assert supervisor.telemetry.state["supervisor_consecutive_autopilot_failures"] == 1
    assert "autopilot-stability-reset" in supervisor.telemetry.events_path.read_text(
        encoding="utf-8"
    )


def test_stop_marker_is_consumed_after_supervisor_stop(tmp_path):
    supervisor = make_supervisor(tmp_path)
    supervisor.stop_path.write_text("stop", encoding="utf-8")

    assert supervisor.run() == 0
    assert not supervisor.stop_path.exists()
    state = json.loads(supervisor.telemetry.state_path.read_text(encoding="utf-8"))
    assert state["supervisor_status"] == "stopped"
