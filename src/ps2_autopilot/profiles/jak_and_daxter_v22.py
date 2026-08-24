from __future__ import annotations

import math

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.jak_skill_engine import ActiveSkill, AtomicSkillEngine

from .base import ProfileContext
from .jak_and_daxter import JakPhase
from .jak_and_daxter_v21 import JakAndDaxterV21Profile


class JakAndDaxterV22Profile(JakAndDaxterV21Profile):
    """Execute platforming as bounded transactions instead of loose button timers.

    V21 gives route decisions memory. V22 gives the fast locomotion layer the same
    discipline: once a hop/jump/roll-jump/dive/platform chain begins, unrelated goal
    scans and route replans cannot steal the controller until VERIFY, timeout, or a
    higher-priority safety condition. Semantic XYZ/contact is used when it has earned
    trust; motion remains the conservative fallback while PINE calibration continues.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.atomic_skills = AtomicSkillEngine()

        self.v22_align_seconds = max(0.04, float(cfg.get("v22_skill_align_seconds", 0.10)))
        self.v22_verify_seconds = max(0.15, float(cfg.get("v22_skill_verify_seconds", 0.40)))
        self.v22_recover_seconds = max(0.10, float(cfg.get("v22_skill_recover_seconds", 0.24)))
        self.v22_skill_timeout = max(0.8, float(cfg.get("v22_skill_timeout_seconds", 2.8)))
        self.v22_skill_retry_limit = max(0, min(2, int(cfg.get("v22_skill_retry_limit", 1))))
        self.v22_verify_motion = max(
            0.002, min(0.08, float(cfg.get("v22_skill_verify_motion", 0.009)))
        )
        self.v22_jump_displacement = max(
            0.05, float(cfg.get("v22_jump_displacement", 0.18))
        )
        self.v22_double_jump_displacement = max(
            self.v22_jump_displacement,
            float(cfg.get("v22_double_jump_displacement", 0.30)),
        )
        self.v22_roll_jump_displacement = max(
            self.v22_double_jump_displacement,
            float(cfg.get("v22_roll_jump_displacement", 0.45)),
        )
        self.v22_platform_displacement = max(
            self.v22_jump_displacement,
            float(cfg.get("v22_platform_displacement", 0.28)),
        )
        self.v22_hop_displacement = max(
            0.03, float(cfg.get("v22_hop_displacement", 0.12))
        )
        self.v22_dive_displacement = max(
            0.03, float(cfg.get("v22_dive_displacement", 0.14))
        )

        self.v22_skill_preemptions = 0
        self.v22_skill_timeouts = 0
        self.v22_semantic_verifications = 0
        self.v22_motion_verifications = 0
        self.v22_grounded_verifications = 0
        self.v22_hop_upgrades = 0
        self.v22_target_jump_skills = 0
        self.v22_platform_chain_skills = 0
        self.v22_last_verification = "none"

    # ------------------------------------------------------------------
    # Evidence helpers

    def _atomic_position(self) -> tuple[float, float, float] | None:
        if not bool(getattr(self, "learning_position_validated", False)):
            return None
        position = getattr(self, "learning_current_position", None)
        if position is None:
            return None
        try:
            values = tuple(float(v) for v in position)
        except (TypeError, ValueError):
            return None
        if len(values) != 3 or not all(math.isfinite(v) for v in values):
            return None
        return values  # type: ignore[return-value]

    @staticmethod
    def _semantic_grounded(ctx: ProfileContext) -> bool | None:
        if not (
            ctx.semantic.get("pine_available")
            and ctx.semantic.get("pine_verified")
            and not ctx.semantic.get("pine_stale")
        ):
            return None
        for key in ("jak_grounded", "jak_on_ground", "jak_ground_contact", "grounded"):
            if key not in ctx.semantic:
                continue
            value = ctx.semantic.get(key)
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            text = str(value).strip().lower()
            if text in {"1", "true", "yes", "ground", "grounded"}:
                return True
            if text in {"0", "false", "no", "air", "airborne"}:
                return False
        return None

    def _atomic_motion(self, ctx: ProfileContext) -> float:
        return max(
            float(ctx.motion),
            float(getattr(self.scene_metrics, "center_motion", 0.0)),
            float(getattr(self.scene_metrics, "lower_motion", 0.0)),
        )

    def _skill_displacement(self, skill: ActiveSkill) -> float | None:
        current = self._atomic_position()
        if current is None or skill.start_position is None:
            return None
        return self._distance(skill.start_position, current)

    def _verify_atomic_skill(self, skill: ActiveSkill, ctx: ProfileContext) -> bool:
        threshold = float(skill.payload.get("min_displacement", self.v22_jump_displacement))
        displacement = self._skill_displacement(skill)
        grounded = self._semantic_grounded(ctx)

        if displacement is not None and displacement >= threshold:
            # If contact telemetry exists, wait for a landing before calling a platform
            # transaction complete. If it does not exist, XYZ displacement alone is
            # still much stronger evidence than screen motion.
            if grounded is False and not bool(skill.payload.get("allow_air_success", False)):
                return False
            self.v22_semantic_verifications += 1
            if grounded is True:
                self.v22_grounded_verifications += 1
            self.v22_last_verification = f"xyz:{displacement:.3f}"
            return True

        motion = self._atomic_motion(ctx)
        if motion >= float(skill.payload.get("min_motion", self.v22_verify_motion)):
            self.v22_motion_verifications += 1
            self.v22_last_verification = f"motion:{motion:.4f}"
            return True
        return False

    # ------------------------------------------------------------------
    # Transaction lifecycle

    def _begin_atomic(
        self,
        name: str,
        ctx: ProfileContext,
        *,
        heading: float,
        forward: float,
        reason: str,
        timeout: float | None = None,
        payload: dict | None = None,
    ) -> None:
        details = dict(payload or {})
        details["reason"] = reason
        details.setdefault("max_retries", self.v22_skill_retry_limit)
        details.setdefault("min_motion", self.v22_verify_motion)
        details["button_sent"] = False
        self.atomic_skills.start(
            name,
            now=ctx.now,
            phase="align",
            phase_seconds=self.v22_align_seconds,
            timeout_seconds=self.v22_skill_timeout if timeout is None else timeout,
            heading=heading,
            forward=forward,
            start_position=self._atomic_position(),
            payload=details,
        )
        # Cancel slower owners that would otherwise resume with stale timers after the
        # atomic transaction. Safety state itself is deliberately not cleared.
        self.land_scan_active = False
        self.target_resolution_active = False
        self.mobility_active = False
        self.second_jump_pending = False

    def _atomic_success(self, ctx: ProfileContext, result: str) -> ActiveSkill | None:
        item = self.atomic_skills.finish(now=ctx.now, success=True, result=result)
        if item is None:
            return None
        self.skills_completed += 1
        self.next_production_action_at = ctx.now + 0.05
        if item.name == "platform_chain":
            self.ledge_jump_successes += 1
        elif item.name in {"hop_step", "double_jump"} and item.payload.get("origin") == "mobility":
            self.mobility_successes += 1
            self.mobility_low_motion_since = None
        elif item.payload.get("origin") == "target":
            self.target_last_progress_at = ctx.now
            self.target_low_motion_since = None
        if item.name == "dive":
            self.scout_retry_cooldown_until = ctx.now + self.scout_retry_cooldown_seconds
            self.scout_stable_frames = 0
        return item

    def _atomic_failure(self, ctx: ProfileContext, result: str) -> ActiveSkill | None:
        item = self.atomic_skills.finish(now=ctx.now, success=False, result=result)
        if item is None:
            return None
        self.next_production_action_at = ctx.now + 0.05
        if item.name == "platform_chain":
            self.ledge_jump_failures += 1
        elif item.name in {"hop_step", "double_jump"} and item.payload.get("origin") == "mobility":
            self.mobility_low_motion_since = None
        if item.name == "dive":
            self.scout_retry_cooldown_until = ctx.now + self.scout_retry_cooldown_seconds
            self.scout_stable_frames = 0
        return item

    def _abort_atomic(self, ctx: ProfileContext, reason: str) -> None:
        item = self.atomic_skills.abort(now=ctx.now, reason=reason)
        if item is None:
            return
        self.v22_skill_preemptions += 1
        if item.name == "platform_chain":
            self.ledge_jump_failures += 1
        self.next_production_action_at = ctx.now + 0.05

    def _atomic_reflex_active(self) -> bool:
        return bool(self.atomic_skills.is_active or super()._atomic_reflex_active())

    def _abort_locomotion_for_menu(self) -> None:
        if self.atomic_skills.is_active:
            # This path does not have a ProfileContext. Menu ownership is the outcome;
            # use the last policy time for accounting and then let the inherited menu
            # reset clear every older locomotion owner.
            now = float(getattr(self, "_v16_now", 0.0))
            self.atomic_skills.abort(now=now, reason="menu-preempt")
            self.v22_skill_preemptions += 1
        super()._abort_locomotion_for_menu()

    # ------------------------------------------------------------------
    # Existing cues now launch V22 transactions

    def _start_roll_jump(self, ctx: ProfileContext, heading: float) -> None:
        self.roll_jump_attempts += 1
        self.next_roll_jump_at = ctx.now + self.production_random.uniform(
            self.roll_jump_min_seconds, self.roll_jump_max_seconds
        )
        self._begin_atomic(
            "roll_jump",
            ctx,
            heading=heading,
            forward=self.production_forward * self.safe_forward_scale,
            reason="dry-traversal",
            payload={
                "min_displacement": self.v22_roll_jump_displacement,
                "max_retries": 0,
            },
        )

    def _start_scout_dive(self, ctx: ProfileContext) -> None:
        self.scout_dive_attempts += 1
        heading = self._clamp(self.gameplay_cue.x * self.cue_turn_gain, -0.52, 0.52)
        self._begin_atomic(
            "dive",
            ctx,
            heading=heading,
            forward=0.48,
            reason="scout-box",
            payload={
                "min_displacement": self.v22_dive_displacement,
                "max_retries": 0,
                "cue_x": self.gameplay_cue.x,
            },
        )

    def _start_ledge_jump(self, controller: Controller, ctx: ProfileContext) -> str:
        del controller
        self._cancel_local_stuck_for_ledge()
        heading = self._clamp(
            self.visual_goal.x * 0.42 if self._visual_goal_actionable() else self.route_bias,
            -self.ledge_jump_turn,
            self.ledge_jump_turn,
        )
        use_double = bool(
            self.ledge_cue.confidence >= self.ledge_double_confidence
            or (
                self._visual_goal_actionable()
                and self.visual_goal.y <= self.goal_high_jump_y - 0.06
            )
        )
        self.ledge_jump_attempts += 1
        if use_double:
            self.ledge_jump_double_attempts += 1
        self.next_ledge_jump_at = ctx.now + self.ledge_jump_cooldown_seconds
        self.v22_platform_chain_skills += 1
        self._begin_atomic(
            "platform_chain",
            ctx,
            heading=heading,
            forward=self.ledge_jump_forward,
            reason="ledge-cue",
            payload={
                "double": use_double,
                "min_displacement": self.v22_platform_displacement,
                "max_retries": 1,
            },
        )
        return self._service_atomic_skill(controller=None, ctx=ctx)

    def _start_mobility_probe(self, controller: Controller, ctx: ProfileContext) -> str:
        del controller
        self.mobility_attempts += 1
        direction = -1.0 if self.route_bias > 0 else 1.0
        self._begin_atomic(
            "hop_step",
            ctx,
            heading=direction * 0.08,
            forward=self.mobility_jump_forward,
            reason="mobility-stall",
            payload={
                "origin": "mobility",
                "escape_direction": direction,
                "min_displacement": self.v22_hop_displacement,
                "max_retries": 0,
            },
        )
        return self._service_atomic_skill(controller=None, ctx=ctx)

    def _start_target_resolution(self, controller: Controller, ctx: ProfileContext) -> str:
        self.target_stalls += 1
        self.target_resolution_attempts += 1
        should_jump = bool(
            self.ledge_cue.confidence >= max(0.25, self.ledge_confidence_min - 0.15)
            or self.visual_goal.y <= 0.64
            or self.target_resolution_attempts == 1
        )
        if should_jump and self.target_resolution_attempts <= 2:
            name = "jump" if self.target_resolution_attempts == 1 else "double_jump"
            heading = self._clamp(self.visual_goal.x * 0.55, -0.35, 0.35)
            self.target_jump_resolutions += 1
            self.v22_target_jump_skills += 1
            self._begin_atomic(
                name,
                ctx,
                heading=heading,
                forward=self.mobility_jump_forward,
                reason="target-resolution",
                payload={
                    "origin": "target",
                    "min_displacement": (
                        self.v22_jump_displacement
                        if name == "jump"
                        else self.v22_double_jump_displacement
                    ),
                    "max_retries": 0,
                },
            )
            return self._service_atomic_skill(controller, ctx)

        self.target_resolution_active = True
        if self.target_resolution_attempts <= self.target_max_resolution_attempts:
            self.target_resolution_direction *= -1.0
            self.target_resolution_stage = "bypass"
            self.target_resolution_until = ctx.now + self.target_bypass_seconds
            self.target_bypasses += 1
            return super()._service_target_resolution(controller, ctx)

        self._blacklist_current_target(ctx)
        controller.set_left_stick(self.target_resolution_direction * 0.62, -0.42)
        controller.set_right_stick(-self.target_resolution_direction * 0.25, 0.0)
        self._neutralized = False
        self.current_action = "jak: V22 target rejected after bounded platforming/bypass attempts"
        return self.current_action

    # ------------------------------------------------------------------
    # Skill phase execution

    def _controller_or_none(self, controller: Controller | None) -> Controller | None:
        # Some inherited start methods are specified to return their first action and
        # call the service function immediately. Tests may invoke them without a real
        # controller; the normal runtime always supplies one on the next policy tick.
        return controller

    def _drive(self, controller: Controller | None, heading: float, forward: float) -> None:
        if controller is None:
            return
        controller.set_left_stick(heading, forward)
        controller.set_right_stick(-heading * 0.10, 0.0)
        self._neutralized = False

    def _tap_once(self, controller: Controller | None, skill: ActiveSkill, button: str, duration: float = 0.07) -> None:
        if bool(skill.payload.get("button_sent", False)):
            return
        if controller is not None:
            controller.tap(button, duration)
        skill.payload["button_sent"] = True

    def _reset_button_gate(self, skill: ActiveSkill) -> None:
        skill.payload["button_sent"] = False

    def _enter_verify(self, skill: ActiveSkill, ctx: ProfileContext) -> None:
        self._reset_button_gate(skill)
        self.atomic_skills.transition("verify", now=ctx.now, seconds=self.v22_verify_seconds)

    def _retry_or_fail(
        self, controller: Controller | None, ctx: ProfileContext, skill: ActiveSkill
    ) -> str:
        max_retries = int(skill.payload.get("max_retries", 0))
        if skill.retries < max_retries:
            self.atomic_skills.retry(now=ctx.now, phase="recover", seconds=self.v22_recover_seconds)
            self._reset_button_gate(skill)
            self.current_action = f"jak: V22 {skill.name} VERIFY failed -> bounded recovery"
            return self.current_action

        failed = self._atomic_failure(ctx, "verify-failed")
        if failed is not None and failed.name == "hop_step" and failed.payload.get("origin") == "mobility":
            self.v22_hop_upgrades += 1
            self.mobility_double_jumps += 1
            self._begin_atomic(
                "double_jump",
                ctx,
                heading=float(failed.heading),
                forward=self.mobility_jump_forward,
                reason="hop-step-failed",
                payload={
                    "origin": "mobility",
                    "escape_direction": failed.payload.get("escape_direction", 1.0),
                    "min_displacement": self.v22_double_jump_displacement,
                    "max_retries": 0,
                },
            )
            return self._service_atomic_skill(controller, ctx)

        if failed is not None and failed.name == "double_jump" and failed.payload.get("origin") == "mobility":
            self.mobility_failures += 1
            self.next_land_scan_at = min(self.next_land_scan_at, ctx.now)
            if controller is not None:
                return self._start_land_scan(controller, ctx, reason="v22-platform-skill-failed")

        self.current_action = f"jak: V22 {failed.name if failed else 'skill'} failed VERIFY; resume planner"
        return self.current_action

    def _service_atomic_skill(
        self, controller: Controller | None, ctx: ProfileContext
    ) -> str:
        skill = self.atomic_skills.active
        if skill is None:
            return self.current_action

        if self.atomic_skills.timed_out(ctx.now):
            self.v22_skill_timeouts += 1
            failed = self._atomic_failure(ctx, "timeout")
            self.current_action = f"jak: V22 {failed.name if failed else 'skill'} timeout; release ownership"
            return self.current_action

        heading = float(skill.heading)
        forward = float(skill.forward)
        phase = skill.phase

        if phase == "align":
            self._drive(controller, heading * 0.70, max(0.18, forward * 0.72))
            if not self.atomic_skills.phase_done(ctx.now):
                self.current_action = f"jak: V22 {skill.name} ALIGN"
                return self.current_action
            self._reset_button_gate(skill)
            self.atomic_skills.transition("commit", now=ctx.now, seconds=0.0)
            phase = "commit"

        if phase == "recover":
            direction = float(skill.payload.get("escape_direction", -1.0 if heading > 0 else 1.0))
            self._drive(controller, direction * 0.52, -0.20)
            if not self.atomic_skills.phase_done(ctx.now):
                self.current_action = f"jak: V22 {skill.name} RECOVER"
                return self.current_action
            # Retry from a fresh alignment while preserving the original start point;
            # VERIFY therefore demands that the whole bounded attempt actually moved.
            self.atomic_skills.transition("align", now=ctx.now, seconds=self.v22_align_seconds)
            self.current_action = f"jak: V22 {skill.name} RETRY ALIGN"
            return self.current_action

        if skill.name in {"jump", "hop_step"}:
            if phase == "commit":
                self._drive(controller, heading, forward)
                self._tap_once(controller, skill, "cross")
                self.atomic_skills.transition("airborne", now=ctx.now, seconds=0.30)
                self.current_action = f"jak: V22 {skill.name} COMMIT"
                return self.current_action
            if phase == "airborne":
                self._drive(controller, heading * 0.65, forward)
                if not self.atomic_skills.phase_done(ctx.now):
                    self.current_action = f"jak: V22 {skill.name} AIRBORNE"
                    return self.current_action
                self._enter_verify(skill, ctx)

        elif skill.name == "double_jump":
            if phase == "commit":
                self._drive(controller, heading, forward)
                self._tap_once(controller, skill, "cross")
                self._reset_button_gate(skill)
                self.atomic_skills.transition("first-air", now=ctx.now, seconds=0.18)
                self.current_action = "jak: V22 double_jump COMMIT first jump"
                return self.current_action
            if phase == "first-air":
                self._drive(controller, heading * 0.75, forward)
                if not self.atomic_skills.phase_done(ctx.now):
                    self.current_action = "jak: V22 double_jump FIRST AIR"
                    return self.current_action
                self._tap_once(controller, skill, "cross")
                self.atomic_skills.transition("airborne", now=ctx.now, seconds=0.36)
                self.current_action = "jak: V22 double_jump COMMIT second jump"
                return self.current_action
            if phase == "airborne":
                self._drive(controller, heading * 0.60, forward)
                if not self.atomic_skills.phase_done(ctx.now):
                    self.current_action = "jak: V22 double_jump AIRBORNE"
                    return self.current_action
                self._enter_verify(skill, ctx)

        elif skill.name == "roll_jump":
            if phase == "commit":
                self._drive(controller, heading, forward)
                self._tap_once(controller, skill, "l1", max(0.10, self.roll_jump_roll_seconds))
                self._reset_button_gate(skill)
                self.atomic_skills.transition("roll", now=ctx.now, seconds=self.roll_jump_roll_seconds)
                self.current_action = "jak: V22 roll_jump COMMIT roll"
                return self.current_action
            if phase == "roll":
                self._drive(controller, heading, forward)
                if not self.atomic_skills.phase_done(ctx.now):
                    self.current_action = "jak: V22 roll_jump ROLL"
                    return self.current_action
                self._tap_once(controller, skill, "cross")
                self.atomic_skills.transition("airborne", now=ctx.now, seconds=self.roll_jump_air_seconds)
                self.current_action = "jak: V22 roll_jump COMMIT jump"
                return self.current_action
            if phase == "airborne":
                self._drive(controller, heading, forward)
                if not self.atomic_skills.phase_done(ctx.now):
                    self.current_action = "jak: V22 roll_jump AIRBORNE"
                    return self.current_action
                self._enter_verify(skill, ctx)

        elif skill.name == "platform_chain":
            use_double = bool(skill.payload.get("double", False))
            if phase == "commit":
                self._drive(controller, heading, forward)
                self._tap_once(controller, skill, "cross")
                self._reset_button_gate(skill)
                self.atomic_skills.transition("first-air", now=ctx.now, seconds=self.ledge_first_air_seconds)
                self.current_action = "jak: V22 platform_chain COMMIT first jump"
                return self.current_action
            if phase == "first-air":
                self._drive(controller, heading, forward)
                if not self.atomic_skills.phase_done(ctx.now):
                    self.current_action = "jak: V22 platform_chain FIRST AIR"
                    return self.current_action
                if use_double:
                    self._tap_once(controller, skill, "cross")
                    self.atomic_skills.transition("airborne", now=ctx.now, seconds=self.ledge_second_air_seconds)
                    self.current_action = "jak: V22 platform_chain COMMIT second jump"
                    return self.current_action
                self.atomic_skills.transition("airborne", now=ctx.now, seconds=self.ledge_settle_seconds)
            if skill.phase == "airborne":
                self._drive(controller, heading, forward)
                if not self.atomic_skills.phase_done(ctx.now):
                    self.current_action = "jak: V22 platform_chain AIRBORNE/LANDING"
                    return self.current_action
                self._enter_verify(skill, ctx)

        elif skill.name == "dive":
            if phase == "commit":
                self._drive(controller, heading, forward)
                self._tap_once(controller, skill, "cross")
                self._reset_button_gate(skill)
                self.atomic_skills.transition("first-air", now=ctx.now, seconds=self.scout_jump_lead_seconds)
                self.current_action = "jak: V22 dive COMMIT jump"
                return self.current_action
            if phase == "first-air":
                self._drive(controller, heading * 0.55, 0.42)
                if not self.atomic_skills.phase_done(ctx.now):
                    self.current_action = "jak: V22 dive AIRBORNE"
                    return self.current_action
                self._tap_once(controller, skill, "square", 0.09)
                self.atomic_skills.transition("attack", now=ctx.now, seconds=self.scout_follow_seconds)
                self.current_action = "jak: V22 dive COMMIT attack"
                return self.current_action
            if phase == "attack":
                self._drive(controller, heading * 0.25, 0.28)
                if not self.atomic_skills.phase_done(ctx.now):
                    self.current_action = "jak: V22 dive FOLLOW THROUGH"
                    return self.current_action
                self._enter_verify(skill, ctx)

        skill = self.atomic_skills.active
        if skill is None:
            return self.current_action
        if skill.phase == "verify":
            # Hold a mild forward landing drive but do not inject another action.
            self._drive(controller, heading * 0.30, min(0.42, max(0.18, forward * 0.55)))
            if self._verify_atomic_skill(skill, ctx):
                finished = self._atomic_success(ctx, self.v22_last_verification)
                self.current_action = (
                    f"jak: V22 {finished.name if finished else 'skill'} VERIFY success "
                    f"({self.v22_last_verification})"
                )
                return self.current_action
            if not self.atomic_skills.phase_done(ctx.now):
                self.current_action = f"jak: V22 {skill.name} VERIFY waiting for displacement/landing"
                return self.current_action
            return self._retry_or_fail(controller, ctx, skill)

        self.current_action = f"jak: V22 {skill.name} phase={skill.phase}"
        return self.current_action

    def _service_skill(self, controller: Controller, ctx: ProfileContext) -> str:
        if self.atomic_skills.is_active:
            return self._service_atomic_skill(controller, ctx)
        return super()._service_skill(controller, ctx)

    def _on_foot(self, controller: Controller, ctx: ProfileContext) -> str:
        self._v16_now = ctx.now
        if self.atomic_skills.is_active:
            # Safety is the only subsystem allowed to preempt COMMIT -> VERIFY.
            self._refresh_water_state(ctx)
            if self.water_escape_active:
                self._abort_atomic(ctx, "water")
                return self._water_escape(controller, ctx)
            if self.shoreline_risk.active:
                self._abort_atomic(ctx, "shoreline")
                return self._service_shoreline_guard(controller, ctx)
            return self._service_atomic_skill(controller, ctx)
        return super()._on_foot(controller, ctx)

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        action = super().tick(controller, ctx)
        if self.atomic_skills.is_active and self.phase != JakPhase.GAMEPLAY:
            self._abort_atomic(ctx, f"phase:{self.phase.value}")
        return action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(self.atomic_skills.telemetry(ctx.now))
        state.update(
            {
                "jak_policy_version": "v22",
                "jak_skill_engine_version": "atomic-v1",
                "jak_skill_preemptions_v22": self.v22_skill_preemptions,
                "jak_skill_timeouts_v22": self.v22_skill_timeouts,
                "jak_skill_semantic_verifications_v22": self.v22_semantic_verifications,
                "jak_skill_motion_verifications_v22": self.v22_motion_verifications,
                "jak_skill_grounded_verifications_v22": self.v22_grounded_verifications,
                "jak_skill_hop_upgrades_v22": self.v22_hop_upgrades,
                "jak_skill_target_jumps_v22": self.v22_target_jump_skills,
                "jak_skill_platform_chains_v22": self.v22_platform_chain_skills,
                "jak_skill_last_verification_v22": self.v22_last_verification,
            }
        )
        return state

    def _v16_stream_intent(self) -> str:
        if self.atomic_skills.is_active:
            skill = self.atomic_skills.active
            return f"SKILL · {skill.name.upper()} · {skill.phase.upper()}"
        return super()._v16_stream_intent()
