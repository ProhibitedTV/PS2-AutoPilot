from __future__ import annotations

import math

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.nfs_hp2_hazards import HazardObservation, estimate_near_hazard

from .base import ProfileContext
from .nfs_hot_pursuit_2 import NfsPhase
from .nfs_hot_pursuit_2_v2 import NfsScreen
from .nfs_hot_pursuit_2_v3 import NfsHotPursuit2V3Profile


class NfsHotPursuit2V4Profile(NfsHotPursuit2V3Profile):
    """Hot Pursuit-specific hazard, launch, police and recovery refinements.

    V4 keeps V3's evidence-stable menu and predictive road controller, then adds
    mechanics that matter specifically in the PS2 game:

    * optional near-field traffic/barricade perception;
    * positive-template roadblock/spike-strip/helicopter-hazard avoidance;
    * a pursuit-racer drive mode distinct from ordinary World Racing;
    * countdown throttle preload for arcade-race launches;
    * bounded, alternating recovery escalation for guardrail/wall traps;
    * template-gated BUSTED continuation rather than an infinite deadlock;
    * You're The Cop target refresh and support calls gated by HUD readiness.

    Image-only obstacle steering remains disabled by default until live footage
    validates it. Explicit `*_avoid_left` / `*_avoid_right` templates can safely
    authorize a short avoidance bias immediately because their direction is part
    of the calibration label rather than inferred from an ambiguous screen.
    """

    name = "nfs_hot_pursuit_2"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        # The PS2 build exposes Classic and Extreme handling. This setting describes
        # the in-game option selected by the operator and adjusts conservative control
        # gains without attempting to navigate the Options menu blindly.
        handling = str(cfg.get("handling_mode", "classic")).strip().lower()
        self.handling_mode = handling if handling in {"classic", "extreme"} else "classic"
        if self.handling_mode == "extreme":
            self.steering_gain *= max(0.65, min(1.0, float(cfg.get("extreme_steering_scale", 0.86))))
            self.curvature_gain *= max(0.65, min(1.0, float(cfg.get("extreme_curvature_scale", 0.90))))
            self.steering_smoothing = max(
                self.steering_smoothing,
                min(0.90, float(cfg.get("extreme_min_smoothing", 0.52))),
            )

        # Arcade launch behavior. HP2 does not use a clutch/false-start transaction,
        # so holding gas during a positively recognized countdown is useful. The hold
        # remains bounded in case the countdown template lingers incorrectly.
        self.countdown_preload_throttle = bool(cfg.get("countdown_preload_throttle", True))
        self.countdown_preload_max_seconds = max(
            1.0, float(cfg.get("countdown_preload_max_seconds", 6.0))
        )
        self.countdown_preload_ticks = 0

        # Vision hazard observation is always recorded, but steering from it is
        # calibration-gated. Explicit directional templates are a separate path.
        self.hazard = HazardObservation.unavailable()
        self.hazard_sample_width = max(240, int(cfg.get("hazard_sample_width", 360)))
        self.hazard_color_distance = max(20.0, float(cfg.get("hazard_color_distance", 52.0)))
        self.obstacle_avoid_enabled = bool(cfg.get("obstacle_avoid_enabled", False))
        self.hazard_confidence_threshold = max(
            0.25, min(1.0, float(cfg.get("hazard_confidence_threshold", 0.58)))
        )
        self.hazard_proximity_threshold = max(
            0.25, min(1.0, float(cfg.get("hazard_proximity_threshold", 0.55)))
        )
        self.hazard_steer_gain = max(0.05, min(1.0, float(cfg.get("hazard_steer_gain", 0.62))))
        self.hazard_steer_blend = max(0.10, min(1.0, float(cfg.get("hazard_steer_blend", 0.72))))
        self.hazard_coast_proximity = max(
            self.hazard_proximity_threshold,
            min(1.0, float(cfg.get("hazard_coast_proximity", 0.66))),
        )
        self.hazard_brake_proximity = max(
            self.hazard_coast_proximity,
            min(1.0, float(cfg.get("hazard_brake_proximity", 0.82))),
        )
        self.hazard_emergency_brake = bool(cfg.get("hazard_emergency_brake", True))
        self.hazard_candidates = 0
        self.hazard_avoid_ticks = 0
        self.hazard_brake_actions = 0

        # Explicit calibrated hazard templates are allowed by default. Labels encode
        # the safe direction, e.g. nfs_race_hud_roadblock_avoid_left.
        self.template_hazard_avoid_enabled = bool(cfg.get("template_hazard_avoid_enabled", True))
        self.template_hazard_hold_seconds = max(
            0.20, float(cfg.get("template_hazard_hold_seconds", 1.05))
        )
        self.template_hazard_strength = max(
            0.10, min(1.0, float(cfg.get("template_hazard_strength", 0.72)))
        )
        self.template_hazard_until = -1e9
        self.template_hazard_bias = 0.0
        self.template_hazard_kind: str | None = None
        self.template_hazard_events = 0

        # Guardrail/wall traps frequently need a different second attempt. V4
        # alternates the recovery steering side and modestly extends the maneuver.
        self.recovery_streak_window_seconds = max(
            3.0, float(cfg.get("recovery_streak_window_seconds", 14.0))
        )
        self.recovery_streak_reset_seconds = max(
            self.recovery_streak_window_seconds,
            float(cfg.get("recovery_streak_reset_seconds", 22.0)),
        )
        self.recovery_duration_step = max(
            0.0, min(0.50, float(cfg.get("recovery_duration_step", 0.22)))
        )
        self.recovery_max_scale = max(1.0, min(2.0, float(cfg.get("recovery_max_scale", 1.55))))
        self.recovery_early_exit_confidence = max(
            self.drive_confidence,
            min(1.0, float(cfg.get("recovery_early_exit_confidence", 0.58))),
        )
        self.recovery_streak = 0
        self.last_recovery_started_at = -1e9
        self.recovery_direction = 1.0
        self.recovery_escalations = 0
        self.recovery_early_exits = 0

        # Hot Pursuit racer failure lifecycle.
        self.busted_hold_seconds = max(0.5, float(cfg.get("busted_hold_seconds", 3.5)))
        self.busted_visible_since: float | None = None
        self.busted_continue_sent = False
        self.busted_continue_actions = 0

        # You're The Cop can require re-targeting after a suspect is arrested. Support
        # calls are safest when a dedicated HUD template says the resource is ready.
        self.cop_target_refresh_seconds = max(
            0.75, float(cfg.get("cop_target_refresh_seconds", 2.0))
        )
        self.cop_support_requires_template = bool(
            cfg.get("cop_support_requires_template", True)
        )
        self.last_cop_target_at = -1e9
        self.cop_target_refreshes = 0
        self.cop_roadblock_ready_sightings = 0
        self.cop_helicopter_ready_sightings = 0

    @classmethod
    def _screen_from_template(cls, name: str | None) -> NfsScreen:
        n = cls._norm(name)
        # Dedicated gameplay hazard crops may not literally contain "race_hud" in
        # their pixels, so the label itself owns the racing semantic.
        if any(
            marker in n
            for marker in (
                "roadblock_avoid_",
                "spike_strip_avoid_",
                "helicopter_hazard_avoid_",
                "mine_avoid_",
            )
        ):
            return NfsScreen.RACING
        if "hot_pursuit_hud" in n or "pursuit_hud" in n:
            return NfsScreen.RACING
        return super()._screen_from_template(name)

    def _observe_hazard(self, ctx: ProfileContext) -> None:
        self.hazard = estimate_near_hazard(
            ctx.frame,
            sample_width=self.hazard_sample_width,
            color_distance=self.hazard_color_distance,
        )
        if self.hazard.confidence >= self.hazard_confidence_threshold:
            self.hazard_candidates += 1

        if not self.template_hazard_avoid_enabled or ctx.template is None:
            return
        if ctx.template.score < self.template_threshold:
            return

        n = self._norm(ctx.template.name)
        kind: str | None = None
        if "roadblock" in n:
            kind = "roadblock"
        elif "spike_strip" in n:
            kind = "spike_strip"
        elif "helicopter_hazard" in n or "mine_avoid" in n:
            kind = "helicopter_hazard"
        if kind is None:
            return

        if "avoid_left" in n:
            bias = -1.0
        elif "avoid_right" in n:
            bias = 1.0
        elif "avoid_center" in n or "roadblock_center" in n:
            bias = 0.0
        else:
            return

        # Count a fresh event rather than every frame of the same template latch.
        if ctx.now > self.template_hazard_until or kind != self.template_hazard_kind:
            self.template_hazard_events += 1
        self.template_hazard_kind = kind
        self.template_hazard_bias = bias
        self.template_hazard_until = ctx.now + self.template_hazard_hold_seconds

    def _fallback_avoid_direction(self) -> float:
        for value in (self.predicted_center_x, self.predicted_curvature, self.road.center_x):
            if abs(value) >= 0.08:
                return 1.0 if value > 0.0 else -1.0
        if abs(self.last_steer) >= 0.08:
            return -1.0 if self.last_steer > 0.0 else 1.0
        return 1.0

    def _hazard_bias(self, now: float) -> tuple[float, float, str | None]:
        if self.template_hazard_avoid_enabled and now <= self.template_hazard_until:
            direction = self.template_hazard_bias
            if abs(direction) < 0.5:
                direction = self._fallback_avoid_direction()
            return direction, self.template_hazard_strength, self.template_hazard_kind

        if (
            self.obstacle_avoid_enabled
            and self.hazard.confidence >= self.hazard_confidence_threshold
            and self.hazard.proximity >= self.hazard_proximity_threshold
        ):
            if abs(self.hazard.center_x) >= 0.10:
                direction = -1.0 if self.hazard.center_x > 0.0 else 1.0
            else:
                direction = self._fallback_avoid_direction()
            severity = min(
                1.0,
                self.hazard.confidence
                * max(0.35, self.hazard.proximity)
                / max(0.01, self.hazard_confidence_threshold),
            )
            return direction, self.hazard_steer_gain * severity, "vision"

        return 0.0, 0.0, None

    def _maybe_cop_actions(self, controller: Controller, ctx: ProfileContext, steer: float) -> list[str]:
        if self.drive_mode != "cop":
            return []

        n = self._norm(self.last_template)
        tags: list[str] = []
        target_needed = "target_needed" in n or "siren_off" in n or "next_speeder" in n
        target_ready = ctx.now - self.last_cop_target_at >= self.cop_target_refresh_seconds
        if (not self.cop_siren_sent or target_needed) and target_ready:
            controller.tap(self.cop_siren_action, 0.05)
            if self.cop_siren_sent:
                self.cop_target_refreshes += 1
                tags.append("retarget")
            else:
                tags.append("siren/target")
            self.cop_siren_sent = True
            self.last_cop_target_at = ctx.now

        boost_ready = ctx.now - self.last_cop_boost_at >= self.cop_boost_interval_seconds
        if boost_ready and self.road.confidence >= 0.50 and abs(steer) <= 0.72:
            controller.tap(self.cop_boost_action, 0.05)
            self.last_cop_boost_at = ctx.now
            self.cop_boosts += 1
            tags.append("boost")

        roadblock_ready = "roadblock_ready" in n or "spike_strip_ready" in n
        helicopter_ready = "helicopter_ready" in n
        if roadblock_ready:
            self.cop_roadblock_ready_sightings += 1
        if helicopter_ready:
            self.cop_helicopter_ready_sightings += 1

        support_ready = ctx.now - self.last_cop_support_at >= self.cop_support_interval_seconds
        if self.cop_support_enabled and support_ready and self.road.confidence >= 0.50:
            action: str | None = None
            label: str | None = None
            if roadblock_ready:
                action, label = self.cop_roadblock_action, "roadblock"
            elif helicopter_ready:
                action, label = self.cop_helicopter_action, "helicopter"
            elif not self.cop_support_requires_template:
                if self._next_support_is_roadblock:
                    action, label = self.cop_roadblock_action, "roadblock"
                else:
                    action, label = self.cop_helicopter_action, "helicopter"
                self._next_support_is_roadblock = not self._next_support_is_roadblock

            if action is not None:
                controller.tap(action, 0.06)
                self.last_cop_support_at = ctx.now
                self.cop_support_calls += 1
                tags.append(label or "support")
        return tags

    def _start_recovery(self, controller: Controller, ctx: ProfileContext, reason: str) -> str:
        previous_start = self.last_recovery_started_at
        pre_steer = self.last_steer
        if ctx.now - previous_start <= self.recovery_streak_window_seconds:
            self.recovery_streak += 1
        else:
            self.recovery_streak = 1
        self.last_recovery_started_at = ctx.now

        if abs(pre_steer) >= 0.08:
            direction = -1.0 if pre_steer > 0.0 else 1.0
        else:
            direction = 1.0
        if self.recovery_streak % 2 == 0:
            direction *= -1.0
            self.recovery_escalations += 1
        self.recovery_direction = direction
        return super()._start_recovery(controller, ctx, reason)

    def _recovery_scale(self) -> float:
        return min(
            self.recovery_max_scale,
            1.0 + max(0, self.recovery_streak - 1) * self.recovery_duration_step,
        )

    def _tick_recovery(self, controller: Controller, ctx: ProfileContext) -> str:
        age = max(0.0, ctx.now - self.recovery_started_at)
        scale = self._recovery_scale()
        reverse_seconds = self.recovery_reverse_seconds * scale
        forward_seconds = self.recovery_forward_seconds * scale
        direction = self.recovery_direction * min(0.96, 0.80 + 0.05 * self.recovery_streak)

        early_ready = (
            age >= reverse_seconds + forward_seconds * 0.35
            and self.road.confidence >= self.recovery_early_exit_confidence
            and ctx.motion >= self.race_motion_threshold
        )
        if early_ready:
            controller.release_all()
            controller.neutral_sticks()
            self.recovery_reason = None
            self.recovery_early_exits += 1
            self._set_phase(NfsPhase.RACING, ctx.now)
            return "recovery early-exit: coherent moving road reacquired"

        if age < reverse_seconds:
            controller.release(self.accelerate_action)
            controller.hold(self.brake_action)
            controller.set_left_stick(direction, 0.0)
            return (
                f"recovery reverse x{scale:.2f}: "
                f"{self.recovery_reason or 'road loss'}"
            )

        if age < reverse_seconds + forward_seconds:
            controller.release(self.brake_action)
            controller.hold(self.accelerate_action)
            controller.set_left_stick(-direction * 0.76, 0.0)
            return (
                f"recovery forward x{scale:.2f}: "
                f"{self.recovery_reason or 'road loss'}"
            )

        controller.release_all()
        controller.neutral_sticks()
        self.recovery_reason = None
        if self.road.confidence >= self.drive_confidence:
            self._set_phase(NfsPhase.RACING, ctx.now)
            return "recovery complete: road reacquired"
        self._set_phase(NfsPhase.CALIBRATION, ctx.now)
        return "recovery complete: awaiting road/template evidence"

    def _special_screen_tick(
        self,
        controller: Controller,
        ctx: ProfileContext,
        screen: NfsScreen,
    ) -> str | None:
        if screen is NfsScreen.BUSTED:
            controller.release_all()
            controller.neutral_sticks()
            if self.busted_visible_since is None:
                self.busted_visible_since = ctx.now
            n = self._norm(self.last_template)
            continue_owned = "busted_continue" in n or "busted_confirm" in n
            if not continue_owned:
                return "busted: fail-closed awaiting continue evidence"
            if self.busted_continue_sent:
                return "busted: continue sent; awaiting visual progress"
            if ctx.now - self.busted_visible_since >= self.busted_hold_seconds:
                ready = ctx.now >= self.next_menu_action_at
                result = self._safe_menu_tap(controller, "confirm", ctx)
                if ready:
                    self.busted_continue_sent = True
                    self.busted_continue_actions += 1
                return result
            return "busted: preserve presentation before continue"
        return super()._special_screen_tick(controller, ctx, screen)

    def _reset_positive_progress_guards(self, screen: NfsScreen) -> None:
        super()._reset_positive_progress_guards(screen)
        if screen is not NfsScreen.BUSTED and screen is not NfsScreen.UNKNOWN:
            self.busted_visible_since = None
            self.busted_continue_sent = False

    def _drive(self, controller: Controller, ctx: ProfileContext) -> str:
        n = self._norm(self.last_template)
        if self.drive_mode != "cop" and ("hot_pursuit_hud" in n or "pursuit_hud" in n):
            self.drive_mode = "pursuit_racer"

        action = super()._drive(controller, ctx)
        if self.phase is not NfsPhase.RACING or self.road.confidence < self.drive_confidence:
            return action

        if ctx.now - self.last_recovery_started_at >= self.recovery_streak_reset_seconds:
            self.recovery_streak = 0

        direction, strength, source = self._hazard_bias(ctx.now)
        if source is None or abs(direction) < 0.5 or strength <= 0.0:
            return action

        target = self._clamp(self.last_steer + direction * strength, self.max_steer)
        steer = self._clamp(
            self.last_steer * (1.0 - self.hazard_steer_blend)
            + target * self.hazard_steer_blend,
            self.max_steer,
        )
        self.last_steer = steer
        controller.set_left_stick(steer, 0.0)
        self.hazard_avoid_ticks += 1

        proximity = self.hazard.proximity if source == "vision" else 1.0
        if source != "vision" or proximity >= self.hazard_coast_proximity:
            controller.release(self.accelerate_action)

        if (
            source == "vision"
            and self.hazard_emergency_brake
            and proximity >= self.hazard_brake_proximity
            and ctx.now - self.last_brake_at >= self.corner_brake_interval_seconds
        ):
            controller.tap(self.brake_action, 0.055)
            self.last_brake_at = ctx.now
            self.hazard_brake_actions += 1
            return f"{action} hazard={source} steer={steer:+.2f} emergency-brake"

        return f"{action} hazard={source} steer={steer:+.2f} avoid"

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        self._observe_hazard(ctx)
        action = super().tick(controller, ctx)

        if (
            self.countdown_preload_throttle
            and self.phase is NfsPhase.COUNTDOWN
            and 0.0 <= ctx.now - self.phase_since <= self.countdown_preload_max_seconds
        ):
            controller.release(self.brake_action)
            controller.hold(self.accelerate_action)
            self.countdown_preload_ticks += 1
            self.last_action = "countdown: preload throttle"
            return self.last_action
        return action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "nfs_policy_version": 4,
                "nfs_handling_mode": self.handling_mode,
                "nfs_countdown_preload_ticks": self.countdown_preload_ticks,
                "nfs_hazard_confidence": round(self.hazard.confidence, 3),
                "nfs_hazard_center_x": round(self.hazard.center_x, 3),
                "nfs_hazard_proximity": round(self.hazard.proximity, 3),
                "nfs_hazard_width": round(self.hazard.width, 3),
                "nfs_hazard_candidates": self.hazard_candidates,
                "nfs_hazard_avoid_ticks": self.hazard_avoid_ticks,
                "nfs_hazard_brake_actions": self.hazard_brake_actions,
                "nfs_template_hazard_kind": self.template_hazard_kind,
                "nfs_template_hazard_events": self.template_hazard_events,
                "nfs_template_hazard_active": ctx.now <= self.template_hazard_until,
                "nfs_recovery_streak": self.recovery_streak,
                "nfs_recovery_scale": round(self._recovery_scale(), 2),
                "nfs_recovery_escalations": self.recovery_escalations,
                "nfs_recovery_early_exits": self.recovery_early_exits,
                "nfs_busted_continue_actions": self.busted_continue_actions,
                "nfs_cop_target_refreshes": self.cop_target_refreshes,
                "nfs_cop_roadblock_ready_sightings": self.cop_roadblock_ready_sightings,
                "nfs_cop_helicopter_ready_sightings": self.cop_helicopter_ready_sightings,
            }
        )
        return state
