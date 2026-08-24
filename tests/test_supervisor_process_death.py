from __future__ import annotations

from pathlib import Path

from ps2_autopilot.config import AppConfig
from ps2_autopilot.supervisor import AutopilotSupervisor, ProbeResult


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += float(seconds)


class FakeChild:
    def __init__(self, pid: int, *, exit_code: int | None = None) -> None:
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
        return int(self.exit_code)

    def terminate(self) -> None:
        self.terminated = True
        self.exit_code = 0

    def kill(self) -> None:
        self.killed = True
        self.exit_code = -9


class ProbeSequence:
    def __init__(self, *results: ProbeResult) -> None:
        self.results = list(results)
        self.last = results[-1]
        self.calls = 0

    def __call__(self) -> ProbeResult:
        self.calls += 1
        if self.results:
            self.last = self.results.pop(0)
        return self.last


def test_full_supervisor_loop_relaunches_pcsx2_then_restarts_autopilot(tmp_path: Path) -> None:
    """Protect the complete process-death recovery wiring, not only helper methods.

    The first AutoPilot child is healthy while PCSX2 is visible. The render/process
    probe then disappears for longer than the configured grace period. The supervisor
    must stop that child, launch the explicit emulator command, wait for a *new* PCSX2
    window/PID, and start a second AutoPilot child. The second child exits cleanly so
    the otherwise-forever supervisor loop terminates inside the test.
    """

    cfg = AppConfig(
        raw={
            "window_title_contains": "PCSX2",
            "supervisor": {
                "restart_delay_seconds": 0.25,
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
            },
        }
    )
    clock = FakeClock()

    # Call order:
    #   1: pre-first-child readiness
    #   2-6: PCSX2 disappears until window-loss grace expires
    #   7: escalation confirms PCSX2 is still missing
    #   8-9: emulator boot wait observes missing -> new ready PID
    # Subsequent probes retain the final ready state.
    probe = ProbeSequence(
        ProbeResult(True, 1111, None),
        ProbeResult(False, None, "pcsx2 exited"),
        ProbeResult(False, None, "pcsx2 exited"),
        ProbeResult(False, None, "pcsx2 exited"),
        ProbeResult(False, None, "pcsx2 exited"),
        ProbeResult(False, None, "pcsx2 exited"),
        ProbeResult(False, None, "pcsx2 exited"),
        ProbeResult(False, None, "booting replacement"),
        ProbeResult(True, 2222, None),
    )

    first = FakeChild(9001)
    second = FakeChild(9002, exit_code=0)
    children = [first, second]
    child_commands: list[list[str]] = []

    def child_factory(command: list[str]) -> FakeChild:
        child_commands.append(list(command))
        return children[len(child_commands) - 1]

    launches: list[tuple[tuple[str, ...], str | None]] = []

    class LaunchHandle:
        pid = 2111

    def emulator_launcher(command: tuple[str, ...], cwd: str | None) -> LaunchHandle:
        launches.append((command, cwd))
        return LaunchHandle()

    supervisor = AutopilotSupervisor(
        "config/madden2005.yaml",
        cfg,
        tmp_path,
        child_factory=child_factory,
        probe=probe,
        emulator_launcher=emulator_launcher,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert supervisor.run() == 0

    assert first.terminated is True
    assert first.killed is False
    assert len(child_commands) == 2
    assert launches == [(('pcsx2-qt.exe', 'game.iso'), None)]

    state = supervisor.telemetry.state
    assert state["supervisor_autopilot_starts"] == 2
    assert state["supervisor_autopilot_restarts"] == 1
    assert state["supervisor_emulator_launch_attempts"] == 1
    assert state["supervisor_emulator_restarts"] == 1
    assert state["supervisor_emulator_pid"] == 2222
    assert state["supervisor_status"] == "stopped"

    events = supervisor.telemetry.events_path.read_text(encoding="utf-8")
    assert "pcsx2-window-missing" in events
    assert "pcsx2-window-lost" in events
    assert "emulator-launch" in events
    assert "emulator-window-ready" in events
    assert events.count('"kind": "autopilot-start"') == 2
