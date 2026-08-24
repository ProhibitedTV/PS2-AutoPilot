from __future__ import annotations

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.jak_objectives import GeyserObjective

from .base import ProfileContext
from .jak_and_daxter_v15 import VisualGoal
from .jak_and_daxter_v17 import JakAndDaxterV17Profile


class JakAndDaxterV18Profile(JakAndDaxterV17Profile):
    """Make locomotion authoritative and visual rewards advisory by default.

    V17 fixed target identity, but the first live V17 soak exposed a more fundamental
    control problem: almost every bright patch was still allowed to become a steering
    command. Jak could therefore be *correctly* rejecting one false target while never
    really navigating, because another Orb/Cell hypothesis immediately took control.
    The result looked exactly like the live report: lots of telemetry, jumps and tiny
    stick corrections while Jak remained pressed against the same wall.

    V18 changes the hierarchy:

    * generic traversal owns locomotion; visual rewards must earn navigation authority;
    * Precursor Orbs are breadcrumbs only. They may influence route scoring but never
      directly steer Jak frame-by-frame;
    * Power Cells steer only during a Cell objective and only when the visual cue is
      sufficiently strong, central and near enough to be actionable;
    * Scout Fly boxes and Blue Eco retain direct control only when progression makes
      them useful (or, for a Scout box, when it is already close enough to transact);
    * route scans cannot be interrupted by the target resolver;
    * blacklisting an unreachable target starts a macro escape: back away, pivot, then
      commit to a sustained forward drive. That prevents "disengage" from meaning one
      reverse tick followed by another shiny-object lock;
    * a cooldown after the escape suppresses reward steering so ordinary navigation has
      time to establish a genuinely new view.

    Water, shoreline safety, menus/cutscenes and specialist modes remain inherited and
    higher priority than this navigation layer.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        # Reward authority. Orbs intentionally have no direct-control threshold: in
        # V18 they are route breadcrumbs only.
        self.reward_cell_confidence_min = max(
            0.46, min(0.95, float(cfg.get("reward_cell_confidence_min", 0.58)))
        )
        self.reward_cell_center_x = max(
            0.15, min(0.80, float(cfg.get("reward_cell_center_x", 0.50)))
        )
        self.reward_cell_near_y = max(
            0.25, min(0.90, float(cfg.get("reward_cell_near_y", 0.48)))
        )
        self.reward_scout_confidence_min = max(
            0.42, min(0.95, float(cfg.get("reward_scout_confidence_min", 0.52)))
        )
        self.reward_scout_center_x = max(
            0.20, min(0.90, float(cfg.get("reward_scout_center_x", 0.68)))
        )
        self.reward_scout_near_y = max(
            0.25, min(0.90, float(cfg.get("reward_scout_near_y", 0.42)))
        )
        self.reward_eco_confidence_min = max(
            0.38, min(0.95, float(cfg.get("reward_eco_confidence_min", 0.48)))
        )
        self.reward_eco_center_x = max(
            0.20, min(0.90, float(cfg.get("reward_eco_center_x", 0.62)))
        )
        self.reward_hint_scan_scale = max(
            0.0, min(0.40, float(cfg.get("reward_hint_scan_scale", 0.10)))
        )
        self.reward_control_cooldown_seconds = max(
            1.0, float(cfg.get("reward_control_cooldown_seconds", 5.0))
        )

        # Macro disengage. These are deliberately long relative to the 12 Hz control
        # loop: the user should be able to *see* Jak leave the bad geometry.
        self.nav_escape_backoff_seconds = max(
            0.20, float(cfg.get("nav_escape_backoff_seconds", 0.55))
        )
        self.nav_escape_pivot_seconds = max(
            0.25, float(cfg.get("nav_escape_pivot_seconds", 0.75))
        )
        self.nav_escape_drive_seconds = max(
            0.80, float(cfg.get("nav_escape_drive_seconds", 2.40))
        )
        self.nav_escape_back_speed = max(
            0.30, min(0.95, float(cfg.get("nav_escape_back_speed", 0.68)))
        )
        self.nav_escape_pivot_turn = max(
            0.45, min(1.0, float(cfg.get("nav_escape_pivot_turn", 0.92)))
        )
        self.nav_escape_drive_forward = max(
            0.45, min(0.95, float(cfg.get("nav_escape_drive_forward", 0.80)))
        )
        self.nav_escape_drive_turn = max(
            0.05, min(0.55, float(cfg.get("nav_escape_drive_turn", 0.22)))
        )

        self.reward_control_suppressed_until = 0.0
        self.navigation_commit_active = False
        self.navigation_commit_stage = "idle"
        self.navigation_commit_until = 0.0
        self.navigation_commit_direction = 1.0
        self.navigation_commit_reason = "none"
        self.navigation_commit_jump_sent = False

        self.reward_hint_frames = 0
        self.reward_direct_control_ticks = 0
        self.reward_orb_steering_suppressions = 0
        self.reward_nonobjective_suppressions = 0
        self.navigation_commits = 0
        self.navigation_commit_completions = 0
        self.navigation_commit_safety_aborts = 0

    def _goal_has_navigation_authority(
        self, goal: VisualGoal, *, ignore_cooldown: bool = False
    ) -> bool:
        if goal.kind == "none":
            return False
        now = getattr(self, "_v16_now", 0.0)
        if not ignore_cooldown and now < self.reward_control_suppressed_until:
            return False

        stage = self.objective.stage

        # Orbs are abundant and visually ambiguous. They are useful breadcrumbs but
        # not worth surrendering locomotion to; the live V17 run spent most of its
        # time oscillating between Orb hypotheses while stationary.
        if goal.kind == "orb":
            return False

        if goal.kind == "power_cell":
            if stage not in {GeyserObjective.FIRST_CELL, GeyserObjective.CLIFF_CELL}:
                return False
            return bool(
                goal.confidence >= self.reward_cell_confidence_min
                and abs(goal.x) <= self.reward_cell_center_x
                and goal.y >= self.reward_cell_near_y
            )

        if goal.kind == "scout_box":
            useful = stage == GeyserObjective.SCOUT_FLIES or goal.y >= self.scout_attack_close_y
            return bool(
                useful
                and goal.confidence >= self.reward_scout_confidence_min
                and abs(goal.x) <= self.reward_scout_center_x
                and goal.y >= self.reward_scout_near_y
            )

        if goal.kind == "blue_eco":
            return bool(
                stage == GeyserObjective.BLUE_ECO_DOOR
                and goal.confidence >= self.reward_eco_confidence_min
                and abs(goal.x) <= self.reward_eco_center_x
            )

        return False

    def _visual_goal_actionable(self) -> bool:
        # Keep V15/V17's detector stability/score gate, then apply V18's authority
        # gate. While a scan or macro route commitment owns control, visual targets are
        # explicitly advisory so another subsystem cannot interrupt the maneuver.
        if not super()._visual_goal_actionable():
            return False
        if self.navigation_commit_active or self.land_scan_active:
            return False
        return self._goal_has_navigation_authority(self.visual_goal)

    def _visual_interest_score(self, frame) -> float:
        # Route scans may still notice rewards, but untrusted hints must not outweigh
        # actual free-space geometry. This is the key separation between "interesting"
        # and "allowed to steer".
        best = self._best_visual_goal(frame)
        if best.kind == "none":
            return 0.0
        raw = self._clamp(best.score / 2.0, 0.0, 1.0)
        if self._goal_has_navigation_authority(best, ignore_cooldown=True):
            self.goal_scan_biases += 1
            return raw
        self.reward_hint_frames += 1
        return raw * self.reward_hint_scan_scale

    def _target_stalled(self, ctx: ProfileContext) -> bool:
        # A route scan already *is* an orientation decision. V17 could start target
        # resolution in the middle of that scan, producing competing left/right stick
        # commands in adjacent frames. Let each scan finish atomically.
        if self.land_scan_active or self.navigation_commit_active:
            return False
        return super()._target_stalled(ctx)

    def _escape_direction_for_target(self) -> float:
        x = self.visual_goal.x if self.visual_goal.kind != "none" else self.target_last_x
        if abs(x) >= 0.08:
            # Turn away from the failed target/wall region.
            return -1.0 if x > 0.0 else 1.0
        if abs(self.route_bias) >= 0.08:
            return -1.0 if self.route_bias > 0.0 else 1.0
        return 1.0 if self.production_random.random() >= 0.5 else -1.0

    def _begin_navigation_commit(
        self, ctx: ProfileContext, *, reason: str, direction: float | None = None
    ) -> None:
        self.navigation_commit_active = True
        self.navigation_commit_stage = "backoff"
        self.navigation_commit_until = ctx.now + self.nav_escape_backoff_seconds
        self.navigation_commit_direction = (
            self._escape_direction_for_target() if direction is None else (1.0 if direction >= 0 else -1.0)
        )
        self.navigation_commit_reason = reason
        self.navigation_commit_jump_sent = False
        self.reward_control_suppressed_until = max(
            self.reward_control_suppressed_until,
            ctx.now
            + self.nav_escape_backoff_seconds
            + self.nav_escape_pivot_seconds
            + self.nav_escape_drive_seconds
            + self.reward_control_cooldown_seconds,
        )
        # Do not leave half-finished steering owners around to fight the macro.
        self.land_scan_active = False
        self.target_resolution_active = False
        self.mobility_active = False
        self.navigation_commits += 1

    def _blacklist_current_target(self, ctx: ProfileContext) -> None:
        direction = self._escape_direction_for_target()
        super()._blacklist_current_target(ctx)
        self._begin_navigation_commit(
            ctx, reason="target-blacklisted", direction=direction
        )

    def _service_navigation_commit(self, controller: Controller, ctx: ProfileContext) -> str:
        d = self.navigation_commit_direction

        if self.navigation_commit_stage == "backoff":
            controller.set_left_stick(d * 0.28, -self.nav_escape_back_speed)
            controller.set_right_stick(-d * 0.18, 0.0)
            self._neutralized = False
            if ctx.now >= self.navigation_commit_until:
                self.navigation_commit_stage = "pivot"
                self.navigation_commit_until = ctx.now + self.nav_escape_pivot_seconds
            self.current_action = (
                f"jak: V18 break contact -> backoff {'R' if d > 0 else 'L'}"
            )
            return self.current_action

        if self.navigation_commit_stage == "pivot":
            controller.set_left_stick(d * self.nav_escape_pivot_turn, 0.06)
            controller.set_right_stick(-d * 0.30, 0.0)
            self._neutralized = False
            if ctx.now >= self.navigation_commit_until:
                self.navigation_commit_stage = "drive"
                self.navigation_commit_until = ctx.now + self.nav_escape_drive_seconds
                self.navigation_commit_jump_sent = False
            self.current_action = (
                f"jak: V18 break contact -> pivot {'R' if d > 0 else 'L'}"
            )
            return self.current_action

        # Sustained drive establishes a new camera/geometry context. One jump near the
        # beginning makes the escape robust to the small tutorial lips that motivated
        # V16, without turning this into another jump-spam policy.
        controller.set_left_stick(d * self.nav_escape_drive_turn, self.nav_escape_drive_forward)
        controller.set_right_stick(-d * 0.06, 0.0)
        if not self.navigation_commit_jump_sent:
            controller.tap("cross", 0.07)
            self.navigation_commit_jump_sent = True
        self._neutralized = False
        self.current_action = (
            f"jak: V18 route commit {'R' if d > 0 else 'L'}; sustained drive"
        )
        if ctx.now < self.navigation_commit_until:
            return self.current_action

        self.navigation_commit_active = False
        self.navigation_commit_stage = "idle"
        self.navigation_commit_completions += 1
        self.route_bias = d * 0.28
        self.next_route_bias_at = max(self.next_route_bias_at, ctx.now + 3.0)
        self.next_land_scan_at = max(self.next_land_scan_at, ctx.now + 1.5)
        self.current_action = "jak: V18 route commit complete; resume navigation"
        return self.current_action

    def _pursue_visual_goal(self, controller: Controller, ctx: ProfileContext) -> str:
        # This method should now only be reached for an authority-approved target.
        self.reward_direct_control_ticks += 1
        action = super()._pursue_visual_goal(controller, ctx)
        if action.startswith("jak: V15 pursue "):
            self.current_action = action.replace(
                "jak: V15 pursue ", "jak: V18 pursue confirmed ", 1
            )
            return self.current_action
        return action

    def _on_foot(self, controller: Controller, ctx: ProfileContext) -> str:
        self._v16_now = ctx.now
        if self.navigation_commit_active:
            # Refresh only enough safety state to let water/shoreline preempt the macro.
            if not self.land_scan_active:
                self._refresh_water_state(ctx)
            if self.water_escape_active or self.shoreline_risk.active:
                self.navigation_commit_active = False
                self.navigation_commit_stage = "idle"
                self.navigation_commit_safety_aborts += 1
                return super()._on_foot(controller, ctx)
            if self.skill_active:
                return super()._on_foot(controller, ctx)
            return self._service_navigation_commit(controller, ctx)
        return super()._on_foot(controller, ctx)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        authority = bool(
            self.visual_goal.kind != "none"
            and self._goal_has_navigation_authority(self.visual_goal)
        )
        state.update(
            {
                "jak_policy_version": "v18",
                "jak_reward_navigation_authority": authority,
                "jak_reward_hint_kind": self.visual_goal.kind,
                "jak_reward_control_suppressed": ctx.now < self.reward_control_suppressed_until,
                "jak_reward_control_suppressed_for": round(
                    max(0.0, self.reward_control_suppressed_until - ctx.now), 2
                ),
                "jak_reward_hint_frames": self.reward_hint_frames,
                "jak_reward_direct_control_ticks": self.reward_direct_control_ticks,
                "jak_navigation_commit_active": self.navigation_commit_active,
                "jak_navigation_commit_stage": self.navigation_commit_stage,
                "jak_navigation_commit_reason": self.navigation_commit_reason,
                "jak_navigation_commits": self.navigation_commits,
                "jak_navigation_commit_completions": self.navigation_commit_completions,
                "jak_navigation_commit_safety_aborts": self.navigation_commit_safety_aborts,
            }
        )
        return state

    def _v16_stream_intent(self) -> str:
        if self.navigation_commit_active:
            return f"NAV · V18 BREAK CONTACT ({self.navigation_commit_stage.upper()})"
        if self.visual_goal.kind == "orb":
            return "NAV · ORB BREADCRUMB (NO STEERING)"
        if self.visual_goal.kind != "none" and not self._visual_goal_actionable():
            return f"NAV · VISUAL HINT {self.visual_goal.kind.upper()}"
        if self._visual_goal_actionable():
            return f"TARGET · V18 CONFIRMED {self.visual_goal.kind.upper()}"
        return super()._v16_stream_intent()
