from __future__ import annotations

import math

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005_v22 import Madden2005V22Profile


class Madden2005V23Profile(Madden2005V22Profile):
    """Confidence-gate defensive contact moves while preserving pursuit.

    V18 already suppressed contact-button activity when a *high-confidence* controlled
    defender was visibly far from a high-confidence action target. The remaining noisy
    case was weaker spatial evidence: that path fell back to the older random defensive
    button cadence, so tackle/strip/play-ball inputs could still fire precisely when the
    agent knew least about where the play was.

    V23 makes the uncertainty policy explicit. When fresh spatial evidence cannot prove
    both a trustworthy controlled player and a trustworthy target, defense keeps steering
    from the proven motion/field fallback and permits only the one-time nearest-defender
    switch plus a throttled Circle sprint/close-space action. Contact moves are authorized
    only after both spatial confidences clear their existing gates; V18 then still blocks
    them while the verified defender is farther than ``defense_contact_distance``.

    Menu, presentation, lifecycle and low-confidence *steering* behavior are unchanged.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.defense_uncertain_action_delay_seconds = max(
            0.25, min(1.50, float(cfg.get("defense_uncertain_action_delay_seconds", 0.62)))
        )
        self.defense_cadence_mode = "inactive"
        self.defense_cadence_reason = "not in live defense"
        self.defense_uncertain_ticks = 0
        self.defense_uncertain_sprints = 0
        self.defense_contact_authorized_ticks = 0
        self.defense_contact_suppressed_ticks = 0

    def _fallback_defense_steering(
        self, controller: Controller, obs: MaddenObservation
    ) -> None:
        """Use the established V6 motion/field pursuit vector without contact inputs."""
        target_x = self._clamp(
            obs.motion_center_x * 0.68 + obs.field_center_x * 0.12,
            -0.88,
            0.88,
        )
        target_y = self._clamp(-self.motion_target_y * 0.60 + 0.34, -0.82, 1.0)
        controller.set_left_stick(target_x, target_y)
        controller.set_right_stick(0.0, 0.0)

    def _uncertain_defense_live(
        self,
        controller: Controller,
        obs: MaddenObservation,
        now: float,
        *,
        reason: str,
    ) -> str:
        self._fallback_defense_steering(controller, obs)
        self.defense_uncertain_ticks += 1
        self.defense_contact_suppressed_ticks += 1
        self.defense_cadence_mode = "uncertain-pursuit"
        self.defense_cadence_reason = reason

        elapsed = max(0.0, now - self.play_started_at)
        if elapsed < 0.55:
            self.current_action = "defense: uncertain spatial read -> steer / observe"
            return self.current_action
        if now < self.next_action_at:
            return self.current_action

        # Preserve the useful one-time nearest-ball switch from the proven V6 policy.
        if not self.defense_switched:
            controller.tap("cross", 0.05)
            self.defense_switched = True
            self.live_action_count += 1
            self.next_action_at = now + self.defense_uncertain_action_delay_seconds
            self.current_action = "defense: uncertain spatial read -> switch nearest once"
            return self.current_action

        # Circle is the low-risk close-space/sprint action in the existing Madden policy.
        # Crucially, Square/Triangle/R2/L1/R1 cannot be emitted from this uncertainty path.
        controller.tap("circle", 0.055)
        self.defense_uncertain_sprints += 1
        self.live_action_count += 1
        self.next_action_at = now + self.defense_uncertain_action_delay_seconds
        self.current_action = "defense: uncertain spatial read -> sprint / close space"
        return self.current_action

    def _defense_live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        spatial = self.last_spatial
        fresh = self._spatial_fresh(now)
        controlled = spatial.controlled if fresh else None
        target_ok = bool(fresh and spatial.target_confidence >= self.spatial_target_confidence)
        controlled_ok = bool(
            controlled is not None
            and controlled.confidence >= self.spatial_controlled_confidence
        )

        if not (target_ok and controlled_ok):
            if not fresh:
                reason = "spatial unavailable/stale"
            elif not target_ok:
                reason = (
                    f"target confidence {spatial.target_confidence:.2f} below "
                    f"{self.spatial_target_confidence:.2f}"
                )
            else:
                confidence = 0.0 if controlled is None else controlled.confidence
                reason = (
                    f"controlled confidence {confidence:.2f} below "
                    f"{self.spatial_controlled_confidence:.2f}"
                )
            return self._uncertain_defense_live(controller, obs, now, reason=reason)

        assert controlled is not None
        distance = math.hypot(
            spatial.target_x - controlled.x,
            spatial.target_y - controlled.y,
        )
        if distance > self.defense_contact_distance:
            self.defense_cadence_mode = "far-pursuit"
            self.defense_cadence_reason = (
                f"verified target distance {distance:.2f} above "
                f"contact gate {self.defense_contact_distance:.2f}"
            )
            self.defense_contact_suppressed_ticks += 1
        else:
            self.defense_cadence_mode = "contact-authorized"
            self.defense_cadence_reason = (
                f"verified defender/target distance {distance:.2f} within contact gate"
            )
            self.defense_contact_authorized_ticks += 1

        # V18 still owns the high-confidence distance gate and the inherited policy owns
        # actual steering/contact selection. V23 only removes contact RNG from uncertainty.
        return super()._defense_live(controller, obs, now)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "madden_policy_version": "v23",
                "defense_cadence_mode": self.defense_cadence_mode,
                "defense_cadence_reason": self.defense_cadence_reason,
                "defense_uncertain_action_delay_seconds": round(
                    self.defense_uncertain_action_delay_seconds, 3
                ),
                "defense_uncertain_ticks": self.defense_uncertain_ticks,
                "defense_uncertain_sprints": self.defense_uncertain_sprints,
                "defense_contact_authorized_ticks": self.defense_contact_authorized_ticks,
                "defense_contact_suppressed_ticks": self.defense_contact_suppressed_ticks,
            }
        )
        return state
