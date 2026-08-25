from __future__ import annotations

from enum import Enum
import re

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005 import MaddenPhase, PlayIntent, Possession
from .madden2005_v23 import Madden2005V23Profile


class SpecialTeamsIntent(str, Enum):
    UNKNOWN = "unknown"
    KICKOFF = "kickoff"
    KICK_RETURN = "kick_return"
    PUNT = "punt"
    PUNT_RETURN = "punt_return"
    FIELD_GOAL = "field_goal"
    EXTRA_POINT = "extra_point"


class SpecialTeamsSide(str, Enum):
    UNKNOWN = "unknown"
    KICKING = "kicking"
    RETURNING = "returning"


class Madden2005V24Profile(Madden2005V23Profile):
    """Give Madden special teams explicit intent and controller ownership.

    The historical Madden profile treats every KICKING phase as the same three-Cross
    meter transaction and unconditionally assumes KICKING -> LIVE means AutoPilot is
    now on defense. That is safe enough for an early autonomous prototype but wrong
    for kick/punt returns and ambiguous after a blocked or missed scoring kick.

    V24 keeps all proven V23 lifecycle behavior while adding a strict OCR-derived
    special-teams latch. Recognized return contexts never touch the kick meter; they
    wait for the CPU kick and enter a bounded run-only return policy when play becomes
    live. Recognized kickoff/punt contexts retain the proven kick meter and explicitly
    hand possession to defense afterward. Field-goal/PAT live transitions are treated
    as ambiguous rather than confidently inventing possession.

    Unknown special-teams text deliberately falls back to V23. The implementation is
    instrumented for live calibration but does not claim reliability from synthetic
    classification alone.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.special_teams_intent = SpecialTeamsIntent.UNKNOWN
        self.special_teams_side = SpecialTeamsSide.UNKNOWN
        self.special_teams_reason = "no special-teams evidence"
        self.special_teams_confidence = 0.0
        self.special_teams_last_seen_at = -1e9
        self.special_teams_latch_seconds = max(
            3.0, min(30.0, float(cfg.get("special_teams_latch_seconds", 12.0)))
        )
        self.special_return_action_seconds = max(
            0.35, min(1.50, float(cfg.get("special_return_action_seconds", 0.72)))
        )

        self.special_return_active = False
        self.special_return_holds = 0
        self.special_return_sprints = 0
        self.special_teams_handoffs = 0
        self.special_teams_recognitions = 0
        self.special_teams_unknown_kicking_ticks = 0
        self.special_teams_scoring_ambiguities = 0

        # Event ownership is diagnostic only; it never feeds controller decisions.
        # Attribute only when the possession state was already reasonably trusted
        # before the base V6 event handler performs interception/fumble mutations.
        self.event_attribution_min_confidence = max(
            0.50,
            min(0.95, float(cfg.get("event_attribution_min_confidence", 0.60))),
        )
        self.game_event_attribution_counts: dict[str, int] = {}
        self.last_game_event_attribution = "none"

    @staticmethod
    def _compact_special_teams(text: str | None) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())

    @classmethod
    def classify_special_teams(
        cls, text: str | None
    ) -> tuple[SpecialTeamsIntent, SpecialTeamsSide, float, str]:
        """Return only high-signal special-teams semantics from OCR text.

        Order matters: return phrases must win before generic KICK/PUNT markers so a
        KICK RETURN screen can never be misclassified as a kicking-side meter state.
        Short bare ``PAT`` is intentionally not accepted because it is too easy for
        OCR/player-name noise to produce accidentally.
        """

        compact = cls._compact_special_teams(text)
        if not compact:
            return (
                SpecialTeamsIntent.UNKNOWN,
                SpecialTeamsSide.UNKNOWN,
                0.0,
                "empty OCR",
            )

        if "PUNTRETURN" in compact or ("PUNT" in compact and "RETURN" in compact):
            return (
                SpecialTeamsIntent.PUNT_RETURN,
                SpecialTeamsSide.RETURNING,
                0.99,
                "punt return marker",
            )

        kick_return = any(
            marker in compact
            for marker in (
                "KICKRETURN",
                "KICKOFFRETURN",
                "RETURNKICK",
                "RETURNKICKOFF",
            )
        ) or ("KICK" in compact and "RETURN" in compact)
        if kick_return:
            return (
                SpecialTeamsIntent.KICK_RETURN,
                SpecialTeamsSide.RETURNING,
                0.99,
                "kick return marker",
            )

        if any(
            marker in compact
            for marker in ("EXTRAPOINT", "POINTAFTER", "PATATTEMPT", "PATKICK")
        ):
            return (
                SpecialTeamsIntent.EXTRA_POINT,
                SpecialTeamsSide.KICKING,
                0.98,
                "extra-point marker",
            )

        if "FIELDGOAL" in compact:
            return (
                SpecialTeamsIntent.FIELD_GOAL,
                SpecialTeamsSide.KICKING,
                0.98,
                "field-goal marker",
            )

        if "PUNT" in compact:
            return (
                SpecialTeamsIntent.PUNT,
                SpecialTeamsSide.KICKING,
                0.96,
                "punt marker",
            )

        if "KICKOFF" in compact or "ONSIDEKICK" in compact:
            return (
                SpecialTeamsIntent.KICKOFF,
                SpecialTeamsSide.KICKING,
                0.96,
                "kickoff marker",
            )

        return (
            SpecialTeamsIntent.UNKNOWN,
            SpecialTeamsSide.UNKNOWN,
            0.0,
            "no strict special-teams marker",
        )

    def _clear_special_teams(self, reason: str) -> None:
        self.special_teams_intent = SpecialTeamsIntent.UNKNOWN
        self.special_teams_side = SpecialTeamsSide.UNKNOWN
        self.special_teams_reason = reason
        self.special_teams_confidence = 0.0
        self.special_teams_last_seen_at = -1e9
        self.special_return_active = False

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)
        intent, side, confidence, reason = self.classify_special_teams(self.last_ocr.text)

        if intent != SpecialTeamsIntent.UNKNOWN:
            changed = intent != self.special_teams_intent or side != self.special_teams_side
            self.special_teams_intent = intent
            self.special_teams_side = side
            self.special_teams_reason = reason
            self.special_teams_confidence = confidence
            self.special_teams_last_seen_at = ctx.now
            if changed:
                self.special_teams_recognitions += 1
        elif (
            self.special_teams_last_seen_at > -1e8
            and ctx.now - self.special_teams_last_seen_at > self.special_teams_latch_seconds
            and self.phase not in {MaddenPhase.KICKING, MaddenPhase.LIVE}
        ):
            self._clear_special_teams("special-teams latch expired")

        return obs

    @staticmethod
    def attribute_game_event(
        event: str,
        possession: Possession,
        confidence: float,
        *,
        minimum_confidence: float = 0.60,
    ) -> str:
        """Classify a semantic football event relative to our controlled side.

        This consumes possession *before* the base event handler flips it after an
        interception or reduces confidence after a fumble. Low-confidence ownership
        remains explicit rather than turning an uncertain OCR event into a policy KPI.
        Punts, kickoffs and penalties are observed but intentionally left unowned.
        """

        event = str(event or "").strip().lower()
        if not event:
            return "none"
        if possession == Possession.UNKNOWN or float(confidence) < float(minimum_confidence):
            return f"{event}_ownership_unknown"

        by_role: dict[str, tuple[str, str]] = {
            "touchdown": ("touchdown_for", "touchdown_against"),
            "field_goal": ("field_goal_for", "field_goal_against"),
            "interception": ("interception_thrown", "interception_made"),
            "sack": ("sack_suffered", "sack_caused"),
            "first_down": ("first_down_gained", "first_down_allowed"),
            "incomplete": ("incomplete_on_offense", "incomplete_on_defense"),
            # FUMBLE OCR does not prove who recovered it. Record whose possession
            # context produced the banner without claiming a forced/won turnover.
            "fumble": ("fumble_on_offense", "opponent_fumble_observed"),
        }
        labels = by_role.get(event)
        if labels is None:
            return f"{event}_observed"
        return labels[0] if possession == Possession.OFFENSE else labels[1]

    def _note_game_event(self, now: float) -> None:
        before_counts = dict(self.game_event_counts)
        possession_before = self.possession
        confidence_before = self.possession_confidence
        super()._note_game_event(now)

        for event, count in self.game_event_counts.items():
            delta = int(count) - int(before_counts.get(event, 0))
            if delta <= 0:
                continue
            attribution = self.attribute_game_event(
                event,
                possession_before,
                confidence_before,
                minimum_confidence=self.event_attribution_min_confidence,
            )
            self.last_game_event_attribution = attribution
            self.game_event_attribution_counts[attribution] = (
                self.game_event_attribution_counts.get(attribution, 0) + delta
            )

    def _transition_phase(self, new_phase: MaddenPhase, now: float) -> None:
        old_phase = self.phase
        intent = self.special_teams_intent
        side = self.special_teams_side
        super()._transition_phase(new_phase, now)
        if self.phase == old_phase:
            return

        if old_phase == MaddenPhase.KICKING and new_phase == MaddenPhase.LIVE:
            if side == SpecialTeamsSide.RETURNING and intent in {
                SpecialTeamsIntent.KICK_RETURN,
                SpecialTeamsIntent.PUNT_RETURN,
            }:
                # V6 runtime semantics allow 0.95+ evidence to replace the base
                # KICKING->DEFENSE assumption immediately.
                self._set_possession(Possession.OFFENSE, 0.99)
                self.planned_play = PlayIntent.RUN
                self.special_return_active = True
                self.special_teams_handoffs += 1
                self.special_teams_reason = f"{intent.value} live handoff -> offense return"
            elif side == SpecialTeamsSide.KICKING and intent in {
                SpecialTeamsIntent.KICKOFF,
                SpecialTeamsIntent.PUNT,
            }:
                self._set_possession(Possession.DEFENSE, 0.99)
                self.special_return_active = False
                self.special_teams_handoffs += 1
                self.special_teams_reason = f"{intent.value} live handoff -> defense coverage"
            elif intent in {
                SpecialTeamsIntent.FIELD_GOAL,
                SpecialTeamsIntent.EXTRA_POINT,
            }:
                # A live frame after a scoring kick can be a block, miss, loose-ball
                # return or noisy transition. Do not retain the base class's invented
                # high-confidence DEFENSE assignment until real play evidence resolves it.
                self.possession = Possession.UNKNOWN
                self.possession_confidence = 0.0
                self.special_return_active = False
                self.special_teams_scoring_ambiguities += 1
                self.special_teams_reason = f"{intent.value} live handoff ambiguous"

        if old_phase == MaddenPhase.LIVE and new_phase == MaddenPhase.POST_PLAY:
            self.special_return_active = False

        # PLAYCALL is a clean boundary. The following _observe pass may immediately
        # replace this with a newly recognized special-teams call from current OCR.
        if new_phase == MaddenPhase.PLAYCALL and old_phase != MaddenPhase.KICKING:
            self._clear_special_teams("new playcall awaiting special-teams evidence")

    def _kicking(self, controller: Controller, now: float) -> str:
        if (
            self.special_teams_side == SpecialTeamsSide.RETURNING
            and self.special_teams_intent
            in {SpecialTeamsIntent.KICK_RETURN, SpecialTeamsIntent.PUNT_RETURN}
        ):
            # CPU owns the kick. Any meter Cross here is actively harmful because it
            # can become a stray return input after the ball is live.
            controller.neutral_sticks()
            self.queue.clear()
            self.kick_armed = False
            self.special_return_holds += 1
            self.next_action_at = max(self.next_action_at, now + 0.20)
            self.current_action = (
                f"special teams: {self.special_teams_intent.value} -> wait for CPU kick"
            )
            return self.current_action

        if self.special_teams_intent == SpecialTeamsIntent.UNKNOWN:
            self.special_teams_unknown_kicking_ticks += 1
        return super()._kicking(controller, now)

    def _return_live(
        self,
        controller: Controller,
        obs: MaddenObservation,
        now: float,
    ) -> str:
        controller.set_right_stick(0.0, 0.0)
        spatial = self.last_spatial
        if self._spatial_fresh(now) and spatial.open_space_confidence >= self.spatial_open_confidence:
            lateral = self._clamp(spatial.open_space_x * 0.62, -0.70, 0.70)
            reason = (
                f"open-space x={spatial.open_space_x:+.2f} "
                f"confidence={spatial.open_space_confidence:.2f}"
            )
        else:
            lateral = self._clamp(obs.field_center_x * 0.32, -0.36, 0.36)
            reason = "field-center fallback"
        controller.set_left_stick(lateral, 1.0)

        if now >= self.next_action_at:
            # Keep returns intentionally simple until footage calibrates juke/dive
            # timing. Cross is already the proven run speed-burst action.
            controller.tap("cross", 0.055)
            self.special_return_sprints += 1
            self.live_action_count += 1
            self.next_action_at = now + self.special_return_action_seconds

        self.current_action = (
            f"special teams: {self.special_teams_intent.value} run north/south; {reason}"
        )
        return self.current_action

    def _offense_live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        if self.special_return_active:
            return self._return_live(controller, obs, now)
        return super()._offense_live(controller, obs, now)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "madden_policy_version": "v24",
                "special_teams_intent": self.special_teams_intent.value,
                "special_teams_side": self.special_teams_side.value,
                "special_teams_reason": self.special_teams_reason,
                "special_teams_confidence": round(self.special_teams_confidence, 2),
                "special_teams_age": (
                    round(max(0.0, ctx.now - self.special_teams_last_seen_at), 2)
                    if self.special_teams_last_seen_at > -1e8
                    else None
                ),
                "special_return_active": self.special_return_active,
                "special_return_holds": self.special_return_holds,
                "special_return_sprints": self.special_return_sprints,
                "special_teams_handoffs": self.special_teams_handoffs,
                "special_teams_recognitions": self.special_teams_recognitions,
                "special_teams_unknown_kicking_ticks": self.special_teams_unknown_kicking_ticks,
                "special_teams_scoring_ambiguities": self.special_teams_scoring_ambiguities,
                "event_attribution_min_confidence": round(
                    self.event_attribution_min_confidence, 2
                ),
                "last_game_event_attribution": self.last_game_event_attribution,
                "game_event_attribution_counts": dict(self.game_event_attribution_counts),
            }
        )
        return state
