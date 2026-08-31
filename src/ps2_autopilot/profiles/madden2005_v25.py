from __future__ import annotations

from .base import ProfileContext
from .madden2005 import MaddenPhase
from .madden2005_v24 import Madden2005V24Profile


class Madden2005V25Profile(Madden2005V24Profile):
    """Keep timed kick-meter inputs owned by the KICKING phase.

    Madden's kick policy arms a three-Cross timed sequence for power/accuracy. The
    shared queue is evaluated after vision has already had a chance to transition
    phases, but the historical KICKING -> non-KICKING transition only disarmed the
    kick flag; it did not discard still-pending meter taps. A fast transition could
    therefore emit a stale kick-meter Cross into LIVE, POST_PLAY, or another screen.

    V25 makes the queue boundary symmetric with the existing PLAYCALL boundary: as
    soon as an accepted phase transition leaves KICKING, any remaining timed taps
    are discarded before the policy loop can execute them. V24 special-teams
    ownership, possession handoff, return behavior, and event attribution remain
    unchanged.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.kick_queue_phase_exits = 0
        self.kick_queue_clear_events = 0
        self.kick_queue_discarded_taps = 0
        self.kick_queue_last_clear_reason = "none"

    def _transition_phase(self, new_phase: MaddenPhase, now: float) -> None:
        old_phase = self.phase
        super()._transition_phase(new_phase, now)
        if self.phase == old_phase:
            return

        if old_phase == MaddenPhase.KICKING and self.phase != MaddenPhase.KICKING:
            self.kick_queue_phase_exits += 1
            discarded = len(self.queue)
            if discarded:
                self.queue.clear()
                self.kick_queue_clear_events += 1
                self.kick_queue_discarded_taps += discarded
            # The base transition already disarms this flag when leaving KICKING,
            # but keep the invariant explicit here because queue ownership and the
            # arming flag are one transaction in V25.
            self.kick_armed = False
            self.kick_queue_last_clear_reason = (
                f"{old_phase.value}->{self.phase.value}; discarded={discarded}"
            )

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "madden_policy_version": "v25",
                "kick_queue_phase_exits": self.kick_queue_phase_exits,
                "kick_queue_clear_events": self.kick_queue_clear_events,
                "kick_queue_discarded_taps": self.kick_queue_discarded_taps,
                "kick_queue_last_clear_reason": self.kick_queue_last_clear_reason,
            }
        )
        return state
