from __future__ import annotations

import os
import time

from .app import AutopilotApp
from .recovery_escalation import EmulatorRestartRequested, SavestateEscalationGate


class ManagedAutopilotApp(AutopilotApp):
    """AutopilotApp with an opt-in final rung for supervisor-owned recovery.

    `AutopilotApp` still owns ordinary motion recovery and F3 savestate reloads.
    This subclass only observes those reloads. If several happen inside a short
    failure window *and* the process was launched by the supervisor with explicit
    emulator launch + termination opt-ins, it exits with the dedicated escalation
    code instead of repeatedly recycling the same savestate forever.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        supervised = os.environ.get("PS2_AUTOPILOT_SUPERVISED") == "1"
        self.savestate_escalation = SavestateEscalationGate.from_config(
            self.config.raw,
            supervised=supervised,
        )

    def _load_savestate(self) -> None:
        # Preserve the existing app-owned emulator control first. A supervisor
        # restart is the final rung only after repeated reloads have demonstrably
        # failed to restore a stable run.
        super()._load_savestate()
        if not self.savestate_escalation.note_reload(time.monotonic()):
            return

        reason = self.savestate_escalation.reason()
        print(f"[recovery] {reason}; requesting supervisor PCSX2 restart", flush=True)
        raise EmulatorRestartRequested(reason)
