from __future__ import annotations

from unittest.mock import patch

import pytest

from ps2_autopilot.app import AutopilotApp
from ps2_autopilot.managed_app import ManagedAutopilotApp
from ps2_autopilot.recovery_escalation import (
    EMULATOR_RESTART_EXIT_CODE,
    EmulatorRestartRequested,
    SavestateEscalationGate,
)


def _raw(*, emulator: bool = True, terminate: bool = True) -> dict:
    return {
        "watchdog": {
            "emulator_restart_after_savestate_reloads": 2,
            "emulator_restart_failure_window_seconds": 60,
        },
        "supervisor": {
            "emulator": {
                "enabled": emulator,
                "terminate_existing_on_escalation": terminate,
            }
        },
    }


def test_gate_requires_supervisor_launch_and_both_emulator_opt_ins() -> None:
    assert SavestateEscalationGate.from_config(_raw(), supervised=False).enabled is False
    assert (
        SavestateEscalationGate.from_config(
            _raw(emulator=False, terminate=True), supervised=True
        ).enabled
        is False
    )
    assert (
        SavestateEscalationGate.from_config(
            _raw(emulator=True, terminate=False), supervised=True
        ).enabled
        is False
    )
    assert SavestateEscalationGate.from_config(_raw(), supervised=True).enabled is True


def test_gate_escalates_only_repeated_reloads_inside_failure_window() -> None:
    gate = SavestateEscalationGate(
        enabled=True,
        reload_threshold=2,
        failure_window_seconds=60.0,
    )
    assert gate.note_reload(10.0) is False
    assert gate.note_reload(50.0) is True

    gate = SavestateEscalationGate(
        enabled=True,
        reload_threshold=2,
        failure_window_seconds=60.0,
    )
    assert gate.note_reload(10.0) is False
    assert gate.note_reload(80.1) is False
    assert gate.reloads_in_window == 1


def test_managed_app_preserves_savestate_reload_before_requesting_supervisor() -> None:
    app = ManagedAutopilotApp.__new__(ManagedAutopilotApp)
    app.savestate_escalation = SavestateEscalationGate(
        enabled=True,
        reload_threshold=2,
        failure_window_seconds=60.0,
    )

    with patch.object(AutopilotApp, "_load_savestate") as base_reload:
        with patch("ps2_autopilot.managed_app.time.monotonic", side_effect=[10.0, 20.0]):
            ManagedAutopilotApp._load_savestate(app)
            with pytest.raises(EmulatorRestartRequested) as exc:
                ManagedAutopilotApp._load_savestate(app)

    assert base_reload.call_count == 2
    assert exc.value.code == EMULATOR_RESTART_EXIT_CODE
    assert "2 savestate reloads" in exc.value.reason


def test_disabled_gate_never_turns_savestate_reloads_into_process_exit() -> None:
    app = ManagedAutopilotApp.__new__(ManagedAutopilotApp)
    app.savestate_escalation = SavestateEscalationGate(
        enabled=False,
        reload_threshold=1,
        failure_window_seconds=60.0,
    )

    with patch.object(AutopilotApp, "_load_savestate") as base_reload:
        with patch("ps2_autopilot.managed_app.time.monotonic", return_value=10.0):
            ManagedAutopilotApp._load_savestate(app)

    base_reload.assert_called_once_with()
