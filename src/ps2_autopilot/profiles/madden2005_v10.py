from __future__ import annotations

from ps2_autopilot.madden_vision import MaddenObservation

from .madden2005 import MaddenPhase
from .madden2005_v9 import Madden2005V9Profile


class Madden2005V10Profile(Madden2005V9Profile):
    """Harden replay/presentation handoff without skipping Madden's broadcast package.

    Real Madden replays can visually resemble LIVE_PLAY, while the first frame of a
    new pre-snap can briefly resemble FIELD_IDLE/POST_PLAY. v0.6.2 keeps the existing
    8/12 second presentation window, suppresses replay-shaped LIVE evidence during
    that window, and rejects a short stale POST_PLAY bounce immediately after the
    presentation has already released into a new play.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.presentation_reentry_guard_seconds = max(
            0.4, float(cfg.get("presentation_reentry_guard_seconds", 1.75))
        )
        self.presentation_live_guard_seconds = max(
            0.25, float(cfg.get("presentation_live_guard_seconds", 0.90))
        )
        self.last_presentation_exit_at = -1e9
        self.presentation_replay_live_suppressed = 0
        self.presentation_reentry_suppressed = 0

    def _transition_phase(self, new_phase: MaddenPhase, now: float) -> None:
        old = self.phase
        super()._transition_phase(new_phase, now)
        if self.phase == old:
            return

        if old == MaddenPhase.POST_PLAY and new_phase != MaddenPhase.POST_PLAY:
            self.last_presentation_exit_at = now
            # Presentation-specific bookkeeping must never leak into the next play.
            self.presentation_prompt_seen = False
            self.presentation_nudges = 0

            # Replace stale presentation text immediately so verbose logs never say
            # "watch replay" while the state machine is already PRE_SNAP/LIVE.
            if new_phase == MaddenPhase.PLAYCALL:
                self.current_action = "playcall: presentation released / acquire choices"
            elif new_phase == MaddenPhase.PRE_SNAP:
                self.current_action = "pre-snap: presentation released / acquire line"
            elif new_phase == MaddenPhase.KICKING:
                self.current_action = "kicking: presentation released / acquire meter"
            elif new_phase == MaddenPhase.LIVE:
                self.current_action = "live: presentation released / acquire play"

    def _stabilize_phase(
        self,
        raw: MaddenPhase,
        obs: MaddenObservation,
        now: float,
    ) -> MaddenPhase:
        # A replay/crowd cut can contain enough grass + motion to look like a live
        # snap. During the promised broadcast hold, keep treating it as presentation.
        # Explicit PLAYCALL/PRE_SNAP/KICKING evidence is *not* blocked and can release
        # the presentation naturally before the hold expires.
        if self.phase == MaddenPhase.POST_PLAY and raw == MaddenPhase.LIVE:
            age = max(0.0, now - self.phase_since)
            hold = self._presentation_hold_target(now)
            if age < hold:
                self.candidate_phase = None
                self.presentation_replay_live_suppressed += 1
                return self.phase

        # After presentation has legitimately released, one stale FIELD_IDLE sample
        # can otherwise bounce PRE_SNAP/LIVE back into POST_PLAY and reactivate the
        # replay hold. Reject that short re-entry window. A genuine completed play is
        # still accepted once the guard expires.
        since_exit = now - self.last_presentation_exit_at
        if raw == MaddenPhase.POST_PLAY and 0.0 <= since_exit < self.presentation_reentry_guard_seconds:
            if self.phase == MaddenPhase.PRE_SNAP:
                self.candidate_phase = None
                self.presentation_reentry_suppressed += 1
                return self.phase
            if (
                self.phase == MaddenPhase.LIVE
                and now - self.play_started_at < self.presentation_live_guard_seconds
            ):
                self.candidate_phase = None
                self.presentation_reentry_suppressed += 1
                return self.phase

        return super()._stabilize_phase(raw, obs, now)

    def telemetry(self, ctx):
        state = super().telemetry(ctx)
        state.update(
            {
                "presentation_last_exit_age": round(
                    max(0.0, ctx.now - self.last_presentation_exit_at), 2
                )
                if self.last_presentation_exit_at > -1e8
                else None,
                "presentation_replay_live_suppressed": self.presentation_replay_live_suppressed,
                "presentation_reentry_suppressed": self.presentation_reentry_suppressed,
                "presentation_reentry_guard_seconds": self.presentation_reentry_guard_seconds,
            }
        )
        return state
