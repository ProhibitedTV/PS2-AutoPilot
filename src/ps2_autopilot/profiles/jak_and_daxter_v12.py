from __future__ import annotations

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .jak_and_daxter_v11 import JakAndDaxterV11Profile


class JakAndDaxterV12Profile(JakAndDaxterV11Profile):
    """Make water escape progress-aware instead of an infinite shoreline arc.

    The 2026-08-23 V11 soak finally proved gameplay ownership was fixed, then exposed
    the next bottleneck: once coherent water owned navigation, the inherited V7
    water routine performed one backtrack/turn pair and could then run the same
    shoreline-seek arc indefinitely. The live run spent ~15 minutes in water,
    generated three global watchdog recoveries and more than ten thousand water
    escape ticks. At one point the water ratio fell to ~0.06, but the policy did not
    recognize that as an opportunity to jump out and eventually steered back into
    deep water.

    V12 turns water escape into a closed-loop search:

    * keep a per-leg best water ratio and require measurable progress;
    * if the view stops improving or remains nearly static, perform a deliberate
      camera/heading U-turn rather than continuing the same arc forever;
    * when one side of the lower playfield becomes mostly dry and center water is
      low, switch to a bounded surface-jump shore-exit attempt (Cross is safe at the
      surface; Square/Circle dive and therefore remain forbidden while escaping);
    * if a shore-hop fails, pivot away and search a different heading;
    * global watchdog recovery while water owns the controller schedules the same
      water-aware U-turn instead of generic roll/double-jump recovery;
    * V9 local obstacle recovery is disarmed while coherent water owns navigation,
      because water animation can create enough optical motion to hide zero
      translation.

    The policy still fails closed around menus/cutscenes, and V10 object/skill logic
    remains lower priority than coherent-water safety.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        self.water_progress_epsilon = max(
            0.005, min(0.12, float(cfg.get("water_progress_epsilon", 0.025)))
        )
        self.water_progress_timeout = max(
            2.0, float(cfg.get("water_progress_timeout", 5.0))
        )
        self.water_stall_motion_max = max(
            0.0005, min(0.03, float(cfg.get("water_stall_motion_max", 0.0045)))
        )
        self.water_stall_seconds = max(
            1.0, float(cfg.get("water_stall_seconds", 3.0))
        )
        self.water_worsen_margin = max(
            0.04, min(0.60, float(cfg.get("water_worsen_margin", 0.14)))
        )
        self.water_direction_lock_seconds = max(
            1.0, float(cfg.get("water_direction_lock_seconds", 4.0))
        )

        self.water_near_shore_total = max(
            0.04, min(0.40, float(cfg.get("water_near_shore_total", 0.14)))
        )
        self.water_near_shore_center = max(
            self.water_near_shore_total,
            min(0.50, float(cfg.get("water_near_shore_center", 0.18))),
        )
        self.water_near_shore_side = max(
            0.0, min(0.30, float(cfg.get("water_near_shore_side", 0.08)))
        )
        self.water_shore_hop_seconds = max(
            1.0, float(cfg.get("water_shore_hop_seconds", 3.2))
        )
        self.water_shore_hop_interval = max(
            0.25, float(cfg.get("water_shore_hop_interval", 0.65))
        )
        self.water_shore_hop_forward = max(
            0.35, min(0.95, float(cfg.get("water_shore_hop_forward", 0.78)))
        )
        self.water_shore_hop_turn = max(
            0.0, min(0.60, float(cfg.get("water_shore_hop_turn", 0.22)))
        )

        self.water_uturn_seconds = max(
            0.5, float(cfg.get("water_uturn_seconds", 1.15))
        )
        self.water_uturn_turn = max(
            0.55, min(1.0, float(cfg.get("water_uturn_turn", 0.94)))
        )
        self.water_uturn_camera = max(
            0.15, min(0.80, float(cfg.get("water_uturn_camera", 0.52)))
        )
        self.water_seek_forward = max(
            0.35, min(0.95, float(cfg.get("water_seek_forward", 0.68)))
        )
        self.water_seek_turn = max(
            0.05, min(0.70, float(cfg.get("water_seek_turn", 0.30)))
        )
        self.water_seek_camera = max(
            0.0, min(0.50, float(cfg.get("water_seek_camera", 0.12)))
        )
        self.water_backtrack_v12_seconds = max(
            0.4, float(cfg.get("water_backtrack_v12_seconds", 1.25))
        )
        self.water_backtrack_v12_speed = max(
            0.35, min(0.95, float(cfg.get("water_backtrack_v12_speed", 0.76)))
        )

        self.water_nav_mode = "idle"
        self.water_nav_mode_until = 0.0
        self.water_nav_direction = 1.0
        self.water_direction_locked_until = 0.0
        self.water_escape_started_at_v12: float | None = None
        self.water_best_ratio = 1.0
        self.water_leg_best_ratio = 1.0
        self.water_best_at: float | None = None
        self.water_last_progress_at: float | None = None
        self.water_low_motion_since: float | None = None
        self.water_next_shore_hop_at = 0.0
        self.water_near_shore = False
        self.water_watchdog_uturn_pending = False

        self.water_uturns = 0
        self.water_shore_hops = 0
        self.water_progress_resets = 0
        self.water_watchdog_recoveries = 0
        self.water_failed_shore_hops = 0
        self.water_best_improvements = 0

    def _reset_v12_water_navigation(self) -> None:
        self.water_nav_mode = "idle"
        self.water_nav_mode_until = 0.0
        self.water_escape_started_at_v12 = None
        self.water_best_ratio = 1.0
        self.water_leg_best_ratio = 1.0
        self.water_best_at = None
        self.water_last_progress_at = None
        self.water_low_motion_since = None
        self.water_next_shore_hop_at = 0.0
        self.water_near_shore = False
        self.water_watchdog_uturn_pending = False

    def _dry_side_direction(self) -> float:
        # +1 means right; -1 means left. Retain the V7 convention.
        return 1.0 if self.water_ratio_right < self.water_ratio_left else -1.0

    def _start_water_leg(self, ctx: ProfileContext, *, direction: float | None = None) -> None:
        if direction is not None:
            self.water_nav_direction = 1.0 if direction >= 0.0 else -1.0
        self.water_leg_best_ratio = self.water_ratio_total
        self.water_last_progress_at = ctx.now
        self.water_low_motion_since = None
        self.water_direction_locked_until = ctx.now + self.water_direction_lock_seconds
        self.water_progress_resets += 1

    def _start_water_uturn(self, ctx: ProfileContext, *, reason: str) -> None:
        # Alternate the turn after each failed leg. If a dry-side cue is strong, use
        # it as the first preference; after a failure the alternation prevents a
        # deterministic wall lock.
        dry = self._dry_side_direction()
        if self.water_nav_mode == "shore-hop":
            self.water_failed_shore_hops += 1
            self.water_nav_direction *= -1.0
        elif abs(self.water_ratio_left - self.water_ratio_right) >= self.water_direction_flip_margin:
            self.water_nav_direction = dry
        else:
            self.water_nav_direction *= -1.0

        self.water_nav_mode = "u-turn"
        self.water_nav_mode_until = ctx.now + self.water_uturn_seconds
        self.water_direction_locked_until = self.water_nav_mode_until + self.water_direction_lock_seconds
        self.water_low_motion_since = None
        self.water_last_progress_at = ctx.now
        self.water_leg_best_ratio = self.water_ratio_total
        self.water_uturns += 1
        self.current_action = f"jak: V12 WATER U-turn ({reason})"

    def _update_water_progress(self, ctx: ProfileContext) -> None:
        if not self.water_escape_active:
            return

        ratio = self.water_ratio_total
        if self.water_escape_started_at_v12 is None:
            self.water_escape_started_at_v12 = ctx.now
            self.water_best_ratio = ratio
            self.water_leg_best_ratio = ratio
            self.water_best_at = ctx.now
            self.water_last_progress_at = ctx.now
            self.water_nav_direction = self._dry_side_direction()
            self.water_direction_locked_until = ctx.now + self.water_direction_lock_seconds

        if ratio + self.water_progress_epsilon < self.water_best_ratio:
            self.water_best_ratio = ratio
            self.water_best_at = ctx.now
            self.water_best_improvements += 1

        if ratio + self.water_progress_epsilon < self.water_leg_best_ratio:
            self.water_leg_best_ratio = ratio
            self.water_last_progress_at = ctx.now
            self.water_low_motion_since = None

        # Coherent water animation fooled the generic local-stuck detector because
        # ripples generate optical motion. V12 uses a stricter water-specific stall
        # timer based on the raw full-frame motion while a swim command is active.
        if float(ctx.motion) <= self.water_stall_motion_max:
            if self.water_low_motion_since is None:
                self.water_low_motion_since = ctx.now
        else:
            self.water_low_motion_since = None

        self.water_near_shore = bool(
            ratio <= self.water_near_shore_total
            and self.water_ratio_center <= self.water_near_shore_center
            and min(self.water_ratio_left, self.water_ratio_right) <= self.water_near_shore_side
        )

    def _refresh_water_state(self, ctx: ProfileContext) -> None:
        was_active = self.water_escape_active
        super()._refresh_water_state(ctx)

        if not self.water_escape_active:
            if was_active:
                self._reset_v12_water_navigation()
            return

        # Coherent water owns navigation. Do not let a previously armed land obstacle
        # transaction fire merely because water ripples happen to satisfy its motion
        # thresholds. Also cancel any V10 skill that could press Square/Circle after
        # entering water; those buttons dive while swimming.
        self.local_stuck_armed_at = None
        self.local_stuck_low_motion_since = None
        if self.local_stuck_active:
            self.local_stuck_active = False
            self.local_stuck_stage = "none"
            self.local_stuck_stage_until = 0.0
        if getattr(self, "skill_active", False):
            self.skill_active = False
            self.skill_name = "none"
            self.skill_stage = "none"
            self.skill_until = 0.0
            self.skill_button_sent = False

        self._update_water_progress(ctx)

        if not was_active:
            self.water_nav_mode = "backtrack"
            self.water_nav_mode_until = ctx.now + self.water_backtrack_v12_seconds
            self.water_nav_direction = self._dry_side_direction()
            self.water_direction_locked_until = self.water_nav_mode_until + self.water_direction_lock_seconds
            return

        # Once a promising heading is reducing water coverage, do not let the legacy
        # per-frame left/right heuristic throw that progress away. The live V11 run
        # improved from ~0.98 to ~0.06 while seeking right, then later flipped away.
        if ctx.now < self.water_direction_locked_until:
            self.water_escape_direction = self.water_nav_direction

    def _water_should_uturn(self, ctx: ProfileContext) -> str:
        if self.water_watchdog_uturn_pending:
            return "watchdog"
        if self.water_nav_mode in {"backtrack", "u-turn", "shore-hop"}:
            return ""
        if (
            self.water_low_motion_since is not None
            and ctx.now - self.water_low_motion_since >= self.water_stall_seconds
        ):
            return "wall-stall"
        if (
            self.water_last_progress_at is not None
            and ctx.now - self.water_last_progress_at >= self.water_progress_timeout
        ):
            return "no-water-progress"
        if self.water_ratio_total >= self.water_leg_best_ratio + self.water_worsen_margin:
            return "water-getting-worse"
        return ""

    def _service_water_shore_hop(self, controller: Controller, ctx: ProfileContext) -> str:
        direction = self._dry_side_direction()
        # A mostly dry side is a local exit cue. Keep forward pressure and tap Cross;
        # Jak can jump while surface-swimming, while Square/Circle would dive.
        controller.set_left_stick(direction * self.water_shore_hop_turn, self.water_shore_hop_forward)
        controller.set_right_stick(-direction * 0.08, 0.0)
        if ctx.now >= self.water_next_shore_hop_at:
            controller.tap("cross", 0.07)
            self.water_shore_hops += 1
            self.water_next_shore_hop_at = ctx.now + self.water_shore_hop_interval
        self._neutralized = False
        self.current_action = (
            f"jak: V12 WATER shore-hop {'R' if direction > 0 else 'L'}; "
            f"water={self.water_ratio_total:.2f} best={self.water_best_ratio:.2f}"
        )
        return self.current_action

    def _water_escape(self, controller: Controller, ctx: ProfileContext) -> str:
        self.water_escape_ticks += 1
        self.second_jump_pending = False

        if self.water_watchdog_uturn_pending and self.water_nav_mode != "u-turn":
            self.water_watchdog_uturn_pending = False
            self._start_water_uturn(ctx, reason="watchdog")

        if self.water_nav_mode == "backtrack":
            if ctx.now >= self.water_nav_mode_until:
                self.water_nav_mode = "seek"
                self._start_water_leg(ctx, direction=self._dry_side_direction())
            else:
                controller.set_left_stick(0.0, -self.water_backtrack_v12_speed)
                controller.set_right_stick(0.0, 0.0)
                self._neutralized = False
                self.current_action = (
                    f"jak: V12 WATER backtrack; water={self.water_ratio_total:.2f}"
                )
                return self.current_action

        if self.water_near_shore and self.water_nav_mode not in {"u-turn", "shore-hop"}:
            self.water_nav_mode = "shore-hop"
            self.water_nav_mode_until = ctx.now + self.water_shore_hop_seconds
            self.water_next_shore_hop_at = ctx.now
            self.water_direction_locked_until = self.water_nav_mode_until

        if self.water_nav_mode == "shore-hop":
            if not self.water_escape_active:
                controller.neutral_sticks()
                self._neutralized = False
                self.current_action = "jak: V12 WATER shore exit confirmed"
                return self.current_action
            if ctx.now >= self.water_nav_mode_until:
                self._start_water_uturn(ctx, reason="shore-hop-failed")
            else:
                return self._service_water_shore_hop(controller, ctx)

        if self.water_nav_mode == "u-turn":
            if ctx.now >= self.water_nav_mode_until:
                self.water_watchdog_uturn_pending = False
                self.water_nav_mode = "seek"
                self._start_water_leg(ctx, direction=self.water_nav_direction)
            else:
                d = self.water_nav_direction
                controller.set_left_stick(d * self.water_uturn_turn, -0.06)
                controller.set_right_stick(-d * self.water_uturn_camera, 0.0)
                self._neutralized = False
                self.current_action = (
                    f"jak: V12 WATER U-turn {'R' if d > 0 else 'L'}; "
                    f"water={self.water_ratio_total:.2f}"
                )
                return self.current_action

        reason = self._water_should_uturn(ctx)
        if reason:
            self._start_water_uturn(ctx, reason=reason)
            return self._water_escape(controller, ctx)

        # Seek phase: swim strongly enough to create translation, with only a modest
        # arc. Heading choice is retained for several seconds so improving legs are
        # not undone by noisy left/right water ratios.
        d = self.water_nav_direction
        controller.set_left_stick(d * self.water_seek_turn, self.water_seek_forward)
        controller.set_right_stick(-d * self.water_seek_camera, 0.0)
        self._neutralized = False
        progress_age = (
            0.0
            if self.water_last_progress_at is None
            else max(0.0, ctx.now - self.water_last_progress_at)
        )
        self.current_action = (
            f"jak: V12 WATER seek {'R' if d > 0 else 'L'}; "
            f"water={self.water_ratio_total:.2f} legbest={self.water_leg_best_ratio:.2f} "
            f"age={progress_age:.1f}s"
        )
        return self.current_action

    def recover(self, controller: Controller) -> str:
        if self.water_escape_active:
            # The runtime watchdog has no frame timestamp, so schedule the U-turn for
            # the next normal gameplay tick. Do not inject L1/Square/Circle while
            # swimming. Cross is reserved for the visually gated shore-hop path.
            controller.release_all()
            controller.neutral_sticks()
            self.water_watchdog_uturn_pending = True
            self.water_watchdog_recoveries += 1
            self.current_action = "jak: V12 water watchdog -> schedule swim U-turn"
            return self.current_action
        return super().recover(controller)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        progress_age = (
            None
            if self.water_last_progress_at is None
            else round(max(0.0, ctx.now - self.water_last_progress_at), 2)
        )
        state.update(
            {
                "jak_policy_version": "v12",
                "jak_water_nav_mode": self.water_nav_mode,
                "jak_water_nav_direction": "R" if self.water_nav_direction > 0 else "L",
                "jak_water_best_ratio": round(self.water_best_ratio, 4),
                "jak_water_leg_best_ratio": round(self.water_leg_best_ratio, 4),
                "jak_water_progress_age": progress_age,
                "jak_water_near_shore": self.water_near_shore,
                "jak_water_uturns": self.water_uturns,
                "jak_water_shore_hops": self.water_shore_hops,
                "jak_water_failed_shore_hops": self.water_failed_shore_hops,
                "jak_water_progress_resets": self.water_progress_resets,
                "jak_water_watchdog_recoveries": self.water_watchdog_recoveries,
                "jak_water_best_improvements": self.water_best_improvements,
            }
        )
        return state
