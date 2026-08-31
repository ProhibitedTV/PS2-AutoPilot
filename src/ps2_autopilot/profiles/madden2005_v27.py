from __future__ import annotations

import math

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005 import PlayIntent
from .madden2005_v26 import Madden2005V26Profile


class Madden2005V27Profile(Madden2005V26Profile):
    """Make live passing and defense look like deliberate Madden football.

    V26 makes the play-call screen semantic and teaches the runner patience. V27
    attacks the remaining early-prototype behavior after the snap:

    * pass timing now follows the selected concept instead of one random release
      window for every play;
    * quick/intermediate concepts use a firmer receiver-button hold while shot
      concepts use a tap, matching Madden 2005's bullet-vs-lob mechanic;
    * the QB remains planted in a small pocket instead of drifting downfield;
    * verified defense closes space first and reserves Square dive tackles for
      genuinely close short-yardage situations;
    * routine verified contact no longer rolls into strip attempts, pass-ball
      attempts, rush moves, or Hit Stick actions without evidence supporting them.

    The policy intentionally does not claim to identify which receiver is open.
    Receiver choice still has bounded variation until perception can associate a
    passing icon with a tracked eligible receiver.
    """

    name = "madden2005"

    QUICK_PASS_TERMS = {
        "SCREEN",
        "SLANT",
        "SLANTS",
        "DRAG",
        "FLAT",
        "HITCH",
        "QUICK",
        "SPOT",
    }
    SHOT_PASS_TERMS = {
        "STREAK",
        "VERT",
        "VERTS",
        "VERTICAL",
        "POST",
        "CORNER",
        "FADE",
        "SEAM",
        "GO",
    }

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.pass_quick_release_seconds = max(
            0.70, min(1.35, float(cfg.get("pass_quick_release_seconds", 0.96)))
        )
        self.pass_intermediate_release_seconds = max(
            0.90, min(1.80, float(cfg.get("pass_intermediate_release_seconds", 1.28)))
        )
        self.pass_shot_release_seconds = max(
            1.10, min(2.30, float(cfg.get("pass_shot_release_seconds", 1.62)))
        )
        self.pass_bullet_hold_seconds = max(
            0.10, min(0.24, float(cfg.get("pass_bullet_hold_seconds", 0.16)))
        )
        self.pass_lob_tap_seconds = max(
            0.04, min(0.10, float(cfg.get("pass_lob_tap_seconds", 0.06)))
        )
        self.defense_secure_tackle_distance = max(
            0.10, min(0.30, float(cfg.get("defense_secure_tackle_distance", 0.18)))
        )
        self.defense_disciplined_action_seconds = max(
            0.28, min(1.10, float(cfg.get("defense_disciplined_action_seconds", 0.52)))
        )

        self.last_pass_concept = "unknown"
        self.last_pass_receiver: str | None = None
        self.pass_concept_throws = {"quick": 0, "intermediate": 0, "shot": 0}
        self.pass_receiver_history: list[str] = []
        self.defense_disciplined_sprints = 0
        self.defense_secure_tackles = 0
        self.defense_high_risk_actions_suppressed = 0

    def _pass_concept(self) -> str:
        label = self._compact_play_text(self.playcall_selected_label)
        if not label:
            return "intermediate"
        if any(term in label for term in self.QUICK_PASS_TERMS):
            return "quick"
        if any(term in label for term in self.SHOT_PASS_TERMS):
            return "shot"
        return "intermediate"

    def _pass_release_seconds(self, concept: str) -> float:
        if concept == "quick":
            return self.pass_quick_release_seconds
        if concept == "shot":
            return self.pass_shot_release_seconds
        return self.pass_intermediate_release_seconds

    def _pass_button_duration(self, concept: str) -> float:
        # Madden 2005: tap a receiver icon for a lob, hold it for a bullet.
        if concept == "shot":
            return self.pass_lob_tap_seconds
        return self.pass_bullet_hold_seconds

    def _choose_pass_receiver(self) -> str:
        buttons = ["cross", "square", "circle", "l1", "r1"]
        if self.last_pass_receiver in buttons and len(buttons) > 1:
            buttons.remove(self.last_pass_receiver)
        receiver = self.rng.choice(buttons)
        self.last_pass_receiver = receiver
        self.pass_receiver_history.append(receiver)
        if len(self.pass_receiver_history) > 8:
            self.pass_receiver_history.pop(0)
        return receiver

    def _pass_live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        elapsed = max(0.0, now - self.play_started_at)
        concept = self._pass_concept()
        self.last_pass_concept = concept

        # Small lateral pocket movement only. Do not turn a designed pass into an
        # accidental QB draw while the routes are developing.
        center = self._clamp(obs.field_center_x * 0.10, -0.10, 0.10)
        pocket_x = math.sin(now * 0.85) * 0.055
        controller.set_left_stick(self._clamp(center + pocket_x, -0.14, 0.14), 0.02)
        controller.set_right_stick(0.0, 0.0)

        if now < self.next_action_at:
            return self.current_action

        if not self.pass_icons_requested and elapsed >= 0.45:
            controller.tap("cross", 0.055)
            self.pass_icons_requested = True
            self.live_action_count += 1
            self.next_action_at = now + 0.20
            self.current_action = f"offense pass: show icons / hold pocket ({concept})"
            return self.current_action

        release_at = self._pass_release_seconds(concept)
        if self.pass_icons_requested and not self.pass_thrown and elapsed >= release_at:
            receiver = self._choose_pass_receiver()
            duration = self._pass_button_duration(concept)
            controller.tap(receiver, duration)
            self.receiver_button = receiver
            self.pass_thrown = True
            self.pass_attempts += 1
            self.live_action_count += 1
            self.pass_concept_throws[concept] += 1
            self.next_action_at = now + 0.28
            throw_style = "lob" if concept == "shot" else "bullet"
            self.current_action = (
                f"offense pass: {concept} {throw_style} -> {receiver} at {elapsed:.2f}s"
            )
            return self.current_action

        # Keep scanning rather than mashing unrelated ballcarrier buttons while the
        # designed concept develops.
        self.next_action_at = now + 0.12
        self.current_action = f"offense pass: scan {concept} concept ({elapsed:.2f}s)"
        return self.current_action

    def _offense_live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        if self.special_return_active or self.planned_play == PlayIntent.RUN:
            return super()._offense_live(controller, obs, now)
        if self.planned_play == PlayIntent.PASS and not self.pass_thrown:
            return self._pass_live(controller, obs, now)
        # Once the ball is out, retain the mature receiver-control/catch policy.
        return super()._offense_live(controller, obs, now)

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
        dx = spatial.target_x - controlled.x
        dy = spatial.target_y - controlled.y
        distance = max(0.001, math.hypot(dx, dy))
        stick_x = self._clamp(dx / distance * 0.92, -0.95, 0.95)
        stick_y = self._clamp(-dy / distance * 0.92, -0.95, 0.95)
        controller.set_left_stick(stick_x, stick_y)
        controller.set_right_stick(0.0, 0.0)
        self.spatial_policy_mode = "defense-vector-disciplined"
        self.spatial_policy_reason = (
            f"controlled#{controlled.track_id} -> verified target; distance={distance:.2f}"
        )
        self.spatial_overrides += 1

        if distance > self.defense_contact_distance:
            self.defense_cadence_mode = "far-pursuit"
            self.defense_cadence_reason = (
                f"verified distance {distance:.2f} above contact gate "
                f"{self.defense_contact_distance:.2f}"
            )
            self.defense_contact_suppressed_ticks += 1
            if now >= self.next_action_at:
                controller.tap("circle", 0.055)
                self.defense_disciplined_sprints += 1
                self.live_action_count += 1
                self.next_action_at = now + self.defense_disciplined_action_seconds
                self.current_action = "defense: close pursuit / sprint"
            return self.current_action

        self.defense_contact_authorized_ticks += 1
        self.defense_cadence_mode = "contact-authorized-disciplined"
        self.defense_cadence_reason = f"verified contact distance {distance:.2f}"
        if now < self.next_action_at:
            return self.current_action

        short_yardage = bool(
            self.situation.goal_to_go
            or (
                self.situation.down in {3, 4}
                and self.situation.distance is not None
                and self.situation.distance <= 2
            )
        )
        if distance <= self.defense_secure_tackle_distance and short_yardage:
            controller.tap("square", 0.055)
            self.tackle_attempts += 1
            self.defense_secure_tackles += 1
            action = "defense: secure short-yardage tackle"
        else:
            # Madden 2005 already permits contact tackles while pursuing. Circle is
            # the sprint/shove action; this avoids gambling on Square dives, R2
            # strips, Triangle ball plays, rush moves, or Hit Stick without evidence.
            controller.tap("circle", 0.055)
            self.defense_disciplined_sprints += 1
            self.defense_high_risk_actions_suppressed += 1
            action = "defense: run through contact; suppress gamble"

        self.live_action_count += 1
        self.next_action_at = now + self.defense_disciplined_action_seconds
        self.current_action = action
        return action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "madden_policy_version": "v27",
                "pass_concept": self.last_pass_concept,
                "pass_last_receiver": self.last_pass_receiver,
                "pass_receiver_history": list(self.pass_receiver_history),
                "pass_concept_throws": dict(self.pass_concept_throws),
                "pass_quick_release_seconds": round(self.pass_quick_release_seconds, 2),
                "pass_intermediate_release_seconds": round(
                    self.pass_intermediate_release_seconds, 2
                ),
                "pass_shot_release_seconds": round(self.pass_shot_release_seconds, 2),
                "defense_secure_tackle_distance": round(
                    self.defense_secure_tackle_distance, 3
                ),
                "defense_disciplined_sprints": self.defense_disciplined_sprints,
                "defense_secure_tackles": self.defense_secure_tackles,
                "defense_high_risk_actions_suppressed": (
                    self.defense_high_risk_actions_suppressed
                ),
            }
        )
        return state
