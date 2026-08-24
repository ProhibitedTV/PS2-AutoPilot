from __future__ import annotations

from pathlib import Path

from ps2_autopilot.config import AppConfig
from ps2_autopilot.recovery_escalation import EMULATOR_RESTART_EXIT_CODE
from ps2_autopilot.supervisor import ChildOutcome, ProbeResult
from ps2_autopilot.supervisor_escalation import EscalatingAutopilotSupervisor


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += float(seconds)


class ProbeSequence:
    def __init__(self, *results: ProbeResult) -> None:
        self.results = list(results)
        self.last = results[-1]

    def __call__(self) -> ProbeResult:
        if self.results:
            self.last = self.results.pop(0)
        return self.last


def _config(*, terminate: bool) -> AppConfig:
    return AppConfig(
        raw={
            "window_title_contains": "PCSX2",
            "supervisor": {
                "restart_delay_seconds": 0.25,
                "poll_seconds": 0.25,
                "window_loss_grace_seconds": 1.0,
                "emulator": {
                    "enabled": True,
                    "command": ["pcsx2-qt.exe", "game.iso"],
                    "wait_seconds": 5.0,
                    "failure_escalation_count": 3,
                    "terminate_existing_on_escalation": terminate,
                },
            },
        }
    )


def _supervisor(
    tmp_path: Path,
    *,
    terminate: bool,
    probe,
    clock: FakeClock,
    process_terminator,
    emulator_launcher,
) -> EscalatingAutopilotSupervisor:
    return EscalatingAutopilotSupervisor(
        "config/madden2005.yaml",
        _config(terminate=terminate),
        tmp_path,
        child_factory=lambda _command: None,  # not used by these focused tests
        probe=probe,
        emulator_launcher=emulator_launcher,
        process_terminator=process_terminator,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


def test_exit_75_promotes_failure_count_to_emulator_escalation_threshold(tmp_path: Path) -> None:
    clock = FakeClock()
    supervisor = _supervisor(
        tmp_path,
        terminate=True,
        probe=lambda: ProbeResult(True, 1111, None),
        clock=clock,
        process_terminator=lambda _pid: True,
        emulator_launcher=lambda _command, _cwd: object(),
    )

    _restarts, failures = supervisor._record_autopilot_failure(
        ChildOutcome(
            EMULATOR_RESTART_EXIT_CODE,
            f"autopilot-exit:{EMULATOR_RESTART_EXIT_CODE}",
            runtime_seconds=30.0,
        )
    )

    assert failures == 3
    assert supervisor.telemetry.state["supervisor_consecutive_autopilot_failures"] == 3
    assert "recovery-ladder-exhausted" in supervisor.telemetry.events_path.read_text(
        encoding="utf-8"
    )


def test_exit_75_still_cannot_kill_visible_pcsx2_without_termination_opt_in(tmp_path: Path) -> None:
    clock = FakeClock()
    terminated: list[int] = []
    supervisor = _supervisor(
        tmp_path,
        terminate=False,
        probe=lambda: ProbeResult(True, 1111, None),
        clock=clock,
        process_terminator=lambda pid: (terminated.append(pid) or True),
        emulator_launcher=lambda _command, _cwd: object(),
    )
    outcome = ChildOutcome(
        EMULATOR_RESTART_EXIT_CODE,
        f"autopilot-exit:{EMULATOR_RESTART_EXIT_CODE}",
        runtime_seconds=30.0,
    )
    supervisor._record_autopilot_failure(outcome)

    supervisor._maybe_escalate_emulator(outcome)

    assert terminated == []
    events = supervisor.telemetry.events_path.read_text(encoding="utf-8")
    assert "emulator-escalation-blocked" in events


def test_exit_75_uses_existing_supervisor_kill_relaunch_path_when_opted_in(tmp_path: Path) -> None:
    clock = FakeClock()
    probe = ProbeSequence(
        ProbeResult(True, 1111, None),
        ProbeResult(False, None, "restarting"),
        ProbeResult(True, 2222, None),
    )
    terminated: list[int] = []
    launched: list[tuple[str, ...]] = []

    class LaunchHandle:
        pid = 2111

    supervisor = _supervisor(
        tmp_path,
        terminate=True,
        probe=probe,
        clock=clock,
        process_terminator=lambda pid: (terminated.append(pid) or True),
        emulator_launcher=lambda command, _cwd: (
            launched.append(command) or LaunchHandle()
        ),
    )
    outcome = ChildOutcome(
        EMULATOR_RESTART_EXIT_CODE,
        f"autopilot-exit:{EMULATOR_RESTART_EXIT_CODE}",
        runtime_seconds=30.0,
    )
    supervisor._record_autopilot_failure(outcome)

    supervisor._maybe_escalate_emulator(outcome)

    assert terminated == [1111]
    assert launched == [('pcsx2-qt.exe', 'game.iso')]
    assert supervisor.telemetry.state["supervisor_emulator_pid"] == 2222
    assert supervisor.telemetry.state["supervisor_emulator_restarts"] == 1
    assert supervisor.telemetry.state["supervisor_consecutive_autopilot_failures"] == 0
