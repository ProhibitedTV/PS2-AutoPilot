from __future__ import annotations

from .recovery_escalation import EMULATOR_RESTART_EXIT_CODE
from .supervisor import AutopilotSupervisor, ChildOutcome


class EscalatingAutopilotSupervisor(AutopilotSupervisor):
    """Treat the managed-app escalation exit as an immediate restart threshold.

    All destructive safeguards remain in `AutopilotSupervisor._maybe_escalate_emulator`:
    emulator launching must be enabled, and a visible PCSX2 process cannot be killed
    unless `terminate_existing_on_escalation` was separately enabled. This subclass
    merely converts the dedicated child exit into the same threshold the supervisor
    already uses for repeated AutoPilot failures.
    """

    def _record_autopilot_failure(self, outcome: ChildOutcome) -> tuple[int, int]:
        restarts, failures = super()._record_autopilot_failure(outcome)
        if outcome.exit_code != EMULATOR_RESTART_EXIT_CODE:
            return restarts, failures

        target = max(1, int(self.cfg.emulator.failure_escalation_count))
        promoted = max(failures, target)
        self.telemetry.state["supervisor_consecutive_autopilot_failures"] = promoted
        self.telemetry.event(
            "recovery-ladder-exhausted",
            "savestate-recovery-exhausted",
            supervisor_autopilot_exit_code=EMULATOR_RESTART_EXIT_CODE,
            supervisor_escalation_failures=promoted,
        )
        return restarts, promoted
