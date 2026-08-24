from __future__ import annotations

from dataclasses import dataclass
from typing import Any


EMULATOR_RESTART_EXIT_CODE = 75


class EmulatorRestartRequested(SystemExit):
    """Exit AutoPilot with a supervisor-recognized recovery escalation code."""

    def __init__(self, reason: str) -> None:
        self.reason = str(reason)
        super().__init__(EMULATOR_RESTART_EXIT_CODE)


@dataclass
class SavestateEscalationGate:
    """Promote repeated savestate reloads into a supervised emulator restart.

    The gate is deliberately inert unless all destructive lifecycle controls were
    explicitly opted in. A normal/manual AutoPilot process can therefore reload a
    savestate forever without ever asking for PCSX2 termination.
    """

    enabled: bool
    reload_threshold: int = 2
    failure_window_seconds: float = 120.0
    reloads_in_window: int = 0
    last_reload_at: float | None = None

    @classmethod
    def from_config(
        cls,
        raw: dict[str, Any],
        *,
        supervised: bool,
    ) -> "SavestateEscalationGate":
        watchdog = dict(raw.get("watchdog", {}) or {})
        supervisor = dict(raw.get("supervisor", {}) or {})
        emulator = dict(supervisor.get("emulator", {}) or {})

        threshold = max(
            1,
            int(watchdog.get("emulator_restart_after_savestate_reloads", 2)),
        )
        window = max(
            10.0,
            float(watchdog.get("emulator_restart_failure_window_seconds", 120.0)),
        )
        enabled = bool(
            supervised
            and emulator.get("enabled", False)
            and emulator.get("terminate_existing_on_escalation", False)
        )
        return cls(
            enabled=enabled,
            reload_threshold=threshold,
            failure_window_seconds=window,
        )

    def note_reload(self, now: float) -> bool:
        now = float(now)
        if (
            self.last_reload_at is None
            or now - self.last_reload_at > self.failure_window_seconds
        ):
            self.reloads_in_window = 0
        self.last_reload_at = now
        self.reloads_in_window += 1
        return bool(self.enabled and self.reloads_in_window >= self.reload_threshold)

    def reason(self) -> str:
        return (
            "recovery ladder exhausted: "
            f"{self.reloads_in_window} savestate reloads within "
            f"{self.failure_window_seconds:.0f}s"
        )
