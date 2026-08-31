from __future__ import annotations

from ps2_autopilot.madden_vision import MaddenObservation, MaddenVisualState

from .base import ProfileContext
from .madden2005 import MaddenPhase
from .madden2005_v24 import SpecialTeamsIntent, SpecialTeamsSide
from .madden2005_v30 import Madden2005V30Profile


class Madden2005V31Profile(Madden2005V30Profile):
    """Keep special-teams controller ownership stable from play-call to live ball.

    A V29 live trace exposed a semantic ordering bug on kickoff returns. The return
    play-call OCR correctly contained ``KICKOFF`` plus ``RETURN LEFT/MIDDLE/RIGHT``,
    which proves AutoPilot owns the returning side. Once the play-call overlay closed,
    the field OCR simplified to bare ``KICKOFF``. V24 interpreted that generic marker
    as fresh kicking-side evidence, overwrote the return ownership, and the generic
    field classifier then mapped the static kickoff setup to PRE_SNAP.

    V31 treats the special-teams play-call as the ownership transaction. The selected
    side and intent remain latched through the on-field setup; generic later markers
    may confirm the play type but cannot flip which side owns the controller. A
    verified FIELD_IDLE setup reached from that armed play-call is promoted to
    KICKING so the existing V24 meter/return policies own the controller before the
    ball moves. KICKING -> LIVE then keeps the proven V24 possession handoff rules.
    """

    name = "madden2005"

    RETURN_INTENTS = {
        SpecialTeamsIntent.KICK_RETURN,
        SpecialTeamsIntent.PUNT_RETURN,
    }
    KICKING_INTENTS = {
        SpecialTeamsIntent.KICKOFF,
        SpecialTeamsIntent.PUNT,
        SpecialTeamsIntent.FIELD_GOAL,
        SpecialTeamsIntent.EXTRA_POINT,
    }

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.special_setup_armed = False
        self.special_setup_intent = SpecialTeamsIntent.UNKNOWN
        self.special_setup_side = SpecialTeamsSide.UNKNOWN
        self.special_setup_armed_at = -1e9
        self.special_setup_arms = 0
        self.special_setup_phase_promotions = 0
        self.special_setup_owner_preservations = 0
        self.special_setup_clears = 0

    def _arm_special_setup(self, now: float) -> None:
        intent = self.special_teams_intent
        side = self.special_teams_side
        if intent == SpecialTeamsIntent.UNKNOWN or side == SpecialTeamsSide.UNKNOWN:
            return
        changed = (
            not self.special_setup_armed
            or intent != self.special_setup_intent
            or side != self.special_setup_side
        )
        self.special_setup_armed = True
        self.special_setup_intent = intent
        self.special_setup_side = side
        self.special_setup_armed_at = now
        if changed:
            self.special_setup_arms += 1

    def _clear_special_setup(self) -> None:
        if self.special_setup_armed:
            self.special_setup_clears += 1
        self.special_setup_armed = False
        self.special_setup_intent = SpecialTeamsIntent.UNKNOWN
        self.special_setup_side = SpecialTeamsSide.UNKNOWN
        self.special_setup_armed_at = -1e9

    def _restore_armed_owner(self, now: float) -> None:
        if not self.special_setup_armed:
            return
        if now - self.special_setup_armed_at > self.special_teams_latch_seconds:
            self._clear_special_setup()
            return

        # Play-type OCR such as bare KICKOFF/PUNT is not controller-side evidence.
        # Once a RETURN play-call proved our side, keep that ownership until live.
        if self.special_setup_side == SpecialTeamsSide.RETURNING:
            if (
                self.special_teams_side != SpecialTeamsSide.RETURNING
                or self.special_teams_intent != self.special_setup_intent
            ):
                self.special_setup_owner_preservations += 1
            self.special_teams_side = SpecialTeamsSide.RETURNING
            self.special_teams_intent = self.special_setup_intent
            self.special_teams_confidence = max(self.special_teams_confidence, 0.99)
            self.special_teams_reason = (
                f"v31 preserve {self.special_setup_intent.value} ownership from playcall"
            )
            self.special_teams_last_seen_at = now
            return

        # Kicking-side ownership is likewise kept if OCR briefly drops to UNKNOWN.
        if self.special_setup_side == SpecialTeamsSide.KICKING:
            if self.special_teams_side == SpecialTeamsSide.UNKNOWN:
                self.special_setup_owner_preservations += 1
                self.special_teams_side = SpecialTeamsSide.KICKING
                self.special_teams_intent = self.special_setup_intent
                self.special_teams_confidence = max(self.special_teams_confidence, 0.96)
                self.special_teams_reason = (
                    f"v31 preserve {self.special_setup_intent.value} ownership from playcall"
                )
                self.special_teams_last_seen_at = now

    def _promote_special_setup_phase(
        self,
        obs: MaddenObservation,
        now: float,
    ) -> None:
        if not self.special_setup_armed:
            return
        if self.phase != MaddenPhase.PRE_SNAP:
            return
        if obs.state != MaddenVisualState.FIELD_IDLE:
            return
        if self.special_teams_side == SpecialTeamsSide.UNKNOWN:
            return
        if self.special_teams_intent == SpecialTeamsIntent.UNKNOWN:
            return

        self._set_phase_immediate(MaddenPhase.KICKING, now)
        self.special_setup_phase_promotions += 1
        self.special_teams_reason = (
            f"v31 armed {self.special_teams_intent.value} field setup -> kicking phase"
        )

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)

        # Special-teams selection screens are the strongest source of controller-side
        # ownership because RETURN language is present there and often disappears on
        # the field. Arm only while the policy still semantically owns PLAYCALL.
        if self.phase == MaddenPhase.PLAYCALL:
            self._arm_special_setup(ctx.now)
        else:
            self._restore_armed_owner(ctx.now)
            self._promote_special_setup_phase(obs, ctx.now)

        return obs

    def _transition_phase(self, new_phase: MaddenPhase, now: float) -> None:
        old_phase = self.phase
        super()._transition_phase(new_phase, now)
        if self.phase == old_phase:
            return

        # Do not clear before the inherited V24 KICKING -> LIVE handoff has consumed
        # the preserved special-teams side/intent.
        if old_phase == MaddenPhase.KICKING and self.phase == MaddenPhase.LIVE:
            self._clear_special_setup()
        elif self.phase in {MaddenPhase.GAME_OVER, MaddenPhase.MENU}:
            self._clear_special_setup()

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "madden_policy_version": "v31",
                "special_setup_armed": self.special_setup_armed,
                "special_setup_intent": self.special_setup_intent.value,
                "special_setup_side": self.special_setup_side.value,
                "special_setup_age": (
                    round(max(0.0, ctx.now - self.special_setup_armed_at), 2)
                    if self.special_setup_armed_at > -1e8
                    else None
                ),
                "special_setup_arms": self.special_setup_arms,
                "special_setup_phase_promotions": self.special_setup_phase_promotions,
                "special_setup_owner_preservations": self.special_setup_owner_preservations,
                "special_setup_clears": self.special_setup_clears,
            }
        )
        return state
