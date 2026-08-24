from __future__ import annotations

from dataclasses import dataclass
import math

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.jak_knowledge import JakControlMode

from .base import ProfileContext
from .jak_and_daxter_v15 import JakAndDaxterV15Profile, VisualGoal


@dataclass(frozen=True)
class ShorelineRisk:
    total: float = 0.0
    left: float = 0.0
    center: float = 0.0
    right: float = 0.0
    active: bool = False


class JakAndDaxterV16Profile(JakAndDaxterV15Profile):
    """Move from reward-seeking exploration toward a full-game control hierarchy.

    The first V15 soak proved that visual rewards can dominate navigation, but also
    exposed the next failure class: a bright environmental lamp was treated as a
    Power Cell for ~25 seconds while Jak pushed into a wall. More generally, the bot
    could be technically healthy while doing nothing useful: pausing on small ledges,
    abandoning climbable terrain, then driving back toward the ocean.

    V16 adds a control layer above V15 rather than another one-frame heuristic:

    * visual targets are tracked as *hypotheses*. A target that does not get closer
      while forward motion stalls triggers jump/bypass attempts and is eventually
      blacklisted for a bounded period instead of monopolizing navigation forever;
    * a proactive mobility controller treats low-motion forward contact as a likely
      small ledge before the 25-second watchdog. It tries jump, double-jump and a
      side-step escape while staying subordinate to confirmed water/menu/cutscenes;
    * a shoreline guard is distinct from the swim controller. Water in the lower
      foreground can bend land navigation away from the ocean *before* Jak falls in;
    * successful water exits create a temporary inland route commitment so the next
      generic traversal burst does not immediately drive back into the same shore;
    * specialist policies for Zoomer, Flut Flut, cannon and fishing are upgraded and
      remain isolated from on-foot button semantics. Semantic telemetry may provide a
      mode hint once PINE fields are calibrated; template detection remains fallback;
    * telemetry reports whether the agent is pursuing, validating, bypassing,
      shoreline-guarding or recovering, which is more useful for long unattended
      stream soaks than a single generic "gameplay" state.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        # Target hypothesis validation.
        self.target_static_seconds = max(0.8, float(cfg.get("target_static_seconds", 1.8)))
        self.target_stall_motion_max = max(
            0.0005, min(0.03, float(cfg.get("target_stall_motion_max", 0.0045)))
        )
        self.target_progress_x = max(0.01, float(cfg.get("target_progress_x", 0.07)))
        self.target_progress_y = max(0.01, float(cfg.get("target_progress_y", 0.05)))
        self.target_progress_area_fraction = max(
            0.03, min(1.0, float(cfg.get("target_progress_area_fraction", 0.22)))
        )
        self.target_blacklist_seconds = max(
            3.0, float(cfg.get("target_blacklist_seconds", 18.0))
        )
        self.target_max_resolution_attempts = max(
            2, min(8, int(cfg.get("target_max_resolution_attempts", 4)))
        )
        self.target_jump_seconds = max(0.15, float(cfg.get("target_jump_seconds", 0.34)))
        self.target_bypass_seconds = max(0.30, float(cfg.get("target_bypass_seconds", 0.95)))
        self.target_bypass_turn = max(
            0.20, min(0.95, float(cfg.get("target_bypass_turn", 0.66)))
        )
        self.target_bypass_forward = max(
            0.15, min(0.85, float(cfg.get("target_bypass_forward", 0.46)))
        )

        self.target_signature: tuple[str, int, int, int] | None = None
        self.target_started_at = 0.0
        self.target_last_progress_at = 0.0
        self.target_last_x = 0.0
        self.target_last_y = 0.0
        self.target_last_area = 0.0
        self.target_low_motion_since: float | None = None
        self.target_resolution_active = False
        self.target_resolution_stage = "idle"
        self.target_resolution_until = 0.0
        self.target_resolution_direction = 1.0
        self.target_resolution_attempts = 0
        self.target_blacklist: dict[tuple[str, int, int, int], float] = {}
        self.target_stalls = 0
        self.target_blacklists = 0
        self.target_bypasses = 0
        self.target_jump_resolutions = 0
        self.target_progress_events = 0

        # Proactive small-ledge/contact mobility.
        self.mobility_stall_motion_max = max(
            0.0005, min(0.03, float(cfg.get("mobility_stall_motion_max", 0.0042)))
        )
        self.mobility_stall_seconds = max(0.6, float(cfg.get("mobility_stall_seconds", 1.15)))
        self.mobility_jump_forward = max(
            0.35, min(0.95, float(cfg.get("mobility_jump_forward", 0.72)))
        )
        self.mobility_stage_seconds = max(0.15, float(cfg.get("mobility_stage_seconds", 0.32)))
        self.mobility_bypass_seconds = max(0.25, float(cfg.get("mobility_bypass_seconds", 0.80)))
        self.mobility_success_motion = max(
            self.mobility_stall_motion_max,
            min(0.12, float(cfg.get("mobility_success_motion", 0.010)))
        )
        self.mobility_low_motion_since: float | None = None
        self.mobility_active = False
        self.mobility_stage = "idle"
        self.mobility_until = 0.0
        self.mobility_direction = 1.0
        self.mobility_attempts = 0
        self.mobility_successes = 0
        self.mobility_failures = 0
        self.mobility_double_jumps = 0

        # Preventative shoreline handling on land.
        self.shoreline_guard_bottom_fraction = max(
            0.12, min(0.45, float(cfg.get("shoreline_guard_bottom_fraction", 0.24)))
        )
        self.shoreline_guard_total = max(
            0.02, min(0.65, float(cfg.get("shoreline_guard_total", 0.11)))
        )
        self.shoreline_guard_center = max(
            0.02, min(0.80, float(cfg.get("shoreline_guard_center", 0.15)))
        )
        self.shoreline_guard_side_delta = max(
            0.02, min(0.60, float(cfg.get("shoreline_guard_side_delta", 0.08)))
        )
        self.shoreline_guard_turn = max(
            0.20, min(0.95, float(cfg.get("shoreline_guard_turn", 0.64)))
        )
        self.shoreline_guard_forward = max(
            0.05, min(0.70, float(cfg.get("shoreline_guard_forward", 0.30)))
        )
        self.shore_exit_commit_seconds = max(
            2.0, float(cfg.get("shore_exit_commit_seconds", 8.0))
        )
        self.shoreline_risk = ShorelineRisk()
        self.shore_exit_commit_until = 0.0
        self.shore_exit_direction = 0.0
        self.shoreline_guard_ticks = 0
        self.shoreline_entries = 0
        self.shore_exit_commits = 0

        # Specialist-mode cadence. These policies are deliberately simple but use the
        # correct control vocabulary and closed-loop stagnation recovery instead of
        # V3's purely random bursts.
        self.specialist_action_at = 0.0
        self.specialist_heading = 0.0
        self.specialist_turn_direction = 1.0
        self.specialist_stall_since: float | None = None
        self.specialist_recoveries = 0
        self.zoomer_hops = 0
        self.zoomer_brakes = 0
        self.flut_flutters = 0
        self.cannon_shots = 0
        self.fishing_tracks = 0
        self.fishing_no_target = 0
        self.semantic_mode_hint = JakControlMode.UNKNOWN

    @staticmethod
    def _goal_signature(goal: VisualGoal) -> tuple[str, int, int, int]:
        area_bin = int(round(math.log10(max(goal.area, 1e-6)) * 3.0))
        return (
            goal.kind,
            int(round(goal.x / 0.18)),
            int(round(goal.y / 0.12)),
            area_bin,
        )

    def _expire_target_blacklist(self, now: float) -> None:
        expired = [key for key, until in self.target_blacklist.items() if now >= until]
        for key in expired:
            self.target_blacklist.pop(key, None)

    def _raw_goal_candidates(self, frame: np.ndarray) -> list[VisualGoal]:
        candidates = super()._raw_goal_candidates(frame)
        # Current-time expiry happens in _refresh_visual_goal. Filtering here keeps
        # false lamps/blocked rewards out of both pursuit and route-scan salience.
        return [
            item
            for item in candidates
            if self.target_blacklist.get(self._goal_signature(item), -1e9)
            <= getattr(self, "_v16_now", 0.0)
        ]

    def _semantic_refresh(self, ctx: ProfileContext) -> None:
        super()._semantic_refresh(ctx)
        raw = None
        for name in ("jak_control_mode", "control_mode", "gameplay_mode"):
            value = ctx.semantic.get(name)
            if value is not None:
                raw = str(value).strip().lower().replace("-", "_").replace(" ", "_")
                break
        aliases = {
            "onfoot": JakControlMode.ON_FOOT,
            "on_foot": JakControlMode.ON_FOOT,
            "foot": JakControlMode.ON_FOOT,
            "zoomer": JakControlMode.ZOOMER,
            "a_grav": JakControlMode.ZOOMER,
            "agrav": JakControlMode.ZOOMER,
            "flut_flut": JakControlMode.FLUT_FLUT,
            "flutflut": JakControlMode.FLUT_FLUT,
            "cannon": JakControlMode.CANNON,
            "turret": JakControlMode.CANNON,
            "fishing": JakControlMode.FISHING,
        }
        self.semantic_mode_hint = aliases.get(raw or "", JakControlMode.UNKNOWN)

    def _update_control_mode(self) -> None:
        if self.semantic_mode_hint != JakControlMode.UNKNOWN:
            self._set_control_mode(self.semantic_mode_hint)
            return
        super()._update_control_mode()

    def _shoreline_metrics(self, frame: np.ndarray) -> ShorelineRisk:
        mask = self._water_candidate_mask(frame)
        if mask.size == 0:
            return ShorelineRisk()
        rows = max(1, int(round(mask.shape[0] * self.shoreline_guard_bottom_fraction)))
        bottom = mask[-rows:, :]
        if bottom.size == 0:
            return ShorelineRisk()
        thirds = np.array_split(bottom, 3, axis=1)
        left, center, right = [float(np.mean(part > 0)) if part.size else 0.0 for part in thirds]
        total = float(np.mean(bottom > 0))
        active = bool(
            not self.water_geometry_confirmed
            and (
                total >= self.shoreline_guard_total
                or center >= self.shoreline_guard_center
            )
        )
        return ShorelineRisk(total, left, center, right, active)

    def _refresh_water_state(self, ctx: ProfileContext) -> None:
        was_active = self.water_escape_active
        previous_direction = getattr(self, "water_nav_direction", 0.0)
        super()._refresh_water_state(ctx)
        self.shoreline_risk = self._shoreline_metrics(ctx.frame)
        if not was_active and self.water_escape_active:
            self.shoreline_entries += 1
        if was_active and not self.water_escape_active:
            # Continue the heading that produced the dry exit rather than immediately
            # handing route selection back to random exploration.
            direction = previous_direction or self._dry_side_direction()
            self.shore_exit_direction = 1.0 if direction >= 0.0 else -1.0
            self.shore_exit_commit_until = ctx.now + self.shore_exit_commit_seconds
            self.shore_exit_commits += 1

    def _service_shoreline_guard(self, controller: Controller, ctx: ProfileContext) -> str:
        risk = self.shoreline_risk
        # Turn toward the drier foreground third. If both are similar, prefer the
        # current inland commitment or simply rotate away from the larger side.
        if abs(risk.left - risk.right) >= self.shoreline_guard_side_delta:
            direction = 1.0 if risk.left > risk.right else -1.0
        elif ctx.now < self.shore_exit_commit_until and self.shore_exit_direction:
            direction = self.shore_exit_direction
        else:
            direction = -1.0 if self.route_bias > 0 else 1.0
        controller.set_left_stick(direction * self.shoreline_guard_turn, self.shoreline_guard_forward)
        controller.set_right_stick(-direction * 0.16, 0.0)
        self.route_bias = direction * 0.28
        self.next_route_bias_at = max(self.next_route_bias_at, ctx.now + 2.5)
        self.shoreline_guard_ticks += 1
        self._neutralized = False
        self.current_action = (
            f"jak: V16 shoreline guard {'R' if direction > 0 else 'L'}; "
            f"water={risk.left:.2f}/{risk.center:.2f}/{risk.right:.2f}"
        )
        return self.current_action

    def _reset_target_tracking(self) -> None:
        self.target_signature = None
        self.target_started_at = 0.0
        self.target_last_progress_at = 0.0
        self.target_last_x = 0.0
        self.target_last_y = 0.0
        self.target_last_area = 0.0
        self.target_low_motion_since = None
        self.target_resolution_attempts = 0

    def _track_target(self, ctx: ProfileContext) -> None:
        if not self._visual_goal_actionable():
            self._reset_target_tracking()
            return
        goal = self.visual_goal
        signature = self._goal_signature(goal)
        if self.target_signature != signature:
            self.target_signature = signature
            self.target_started_at = ctx.now
            self.target_last_progress_at = ctx.now
            self.target_last_x = goal.x
            self.target_last_y = goal.y
            self.target_last_area = goal.area
            self.target_low_motion_since = None
            self.target_resolution_attempts = 0
            return

        area_gain = (
            (goal.area - self.target_last_area) / max(self.target_last_area, 1e-6)
            if self.target_last_area > 0
            else 0.0
        )
        screen_progress = bool(
            goal.y >= self.target_last_y + self.target_progress_y
            or abs(goal.x) <= max(0.04, abs(self.target_last_x) - self.target_progress_x)
            or area_gain >= self.target_progress_area_fraction
        )
        if screen_progress:
            self.target_last_progress_at = ctx.now
            self.target_last_x = goal.x
            self.target_last_y = goal.y
            self.target_last_area = goal.area
            self.target_low_motion_since = None
            self.target_progress_events += 1
        elif float(ctx.motion) <= self.target_stall_motion_max:
            if self.target_low_motion_since is None:
                self.target_low_motion_since = ctx.now
        else:
            self.target_low_motion_since = None

    def _target_stalled(self, ctx: ProfileContext) -> bool:
        if not self._visual_goal_actionable() or self.target_signature is None:
            return False
        no_visual_progress = ctx.now - self.target_last_progress_at >= self.target_static_seconds
        low_motion = bool(
            self.target_low_motion_since is not None
            and ctx.now - self.target_low_motion_since >= self.target_static_seconds * 0.65
        )
        return no_visual_progress and low_motion

    def _blacklist_current_target(self, ctx: ProfileContext) -> None:
        if self.target_signature is not None:
            self.target_blacklist[self.target_signature] = ctx.now + self.target_blacklist_seconds
            self.target_blacklists += 1
        self.visual_goal = VisualGoal()
        self.visual_goal_last_kind = "none"
        self.visual_goal_stable_frames = 0
        self.target_resolution_active = False
        self._reset_target_tracking()

    def _start_target_resolution(self, controller: Controller, ctx: ProfileContext) -> str:
        self.target_stalls += 1
        self.target_resolution_active = True
        self.target_resolution_attempts += 1
        # A target above/near center or a visible ledge means "try platforming" before
        # assuming the target is false. This directly addresses small tutorial steps.
        should_jump = bool(
            self.ledge_cue.confidence >= max(0.25, self.ledge_confidence_min - 0.15)
            or self.visual_goal.y <= 0.64
            or self.target_resolution_attempts == 1
        )
        if should_jump and self.target_resolution_attempts <= 2:
            self.target_resolution_stage = "jump"
            self.target_resolution_until = ctx.now + self.target_jump_seconds
            controller.set_left_stick(
                self._clamp(self.visual_goal.x * 0.55, -0.35, 0.35),
                self.mobility_jump_forward,
            )
            controller.tap("cross", 0.07)
            if self.target_resolution_attempts >= 2:
                self.second_jump_pending = True
                self.second_jump_at = ctx.now + 0.18
            self.target_jump_resolutions += 1
            self._neutralized = False
            self.current_action = "jak: V16 target blocked -> jump/ledge solve"
            return self.current_action

        # Alternate side bypasses before declaring the visual target bogus/unreachable.
        if self.target_resolution_attempts <= self.target_max_resolution_attempts:
            self.target_resolution_direction *= -1.0
            self.target_resolution_stage = "bypass"
            self.target_resolution_until = ctx.now + self.target_bypass_seconds
            self.target_bypasses += 1
            return self._service_target_resolution(controller, ctx)

        self._blacklist_current_target(ctx)
        controller.set_left_stick(self.target_resolution_direction * 0.62, -0.42)
        controller.set_right_stick(-self.target_resolution_direction * 0.25, 0.0)
        self._neutralized = False
        self.current_action = "jak: V16 target rejected/blacklisted; disengage"
        return self.current_action

    def _service_target_resolution(self, controller: Controller, ctx: ProfileContext) -> str:
        if not self.target_resolution_active:
            return self.current_action
        if self.water_escape_active or self.shoreline_risk.active:
            self.target_resolution_active = False
            return self._service_shoreline_guard(controller, ctx) if not self.water_escape_active else self._water_escape(controller, ctx)

        if float(ctx.motion) >= self.mobility_success_motion and ctx.now >= self.target_resolution_until:
            self.target_resolution_active = False
            self.target_last_progress_at = ctx.now
            self.target_low_motion_since = None
            self.current_action = "jak: V16 target obstacle cleared; resume pursuit"
            return self.current_action

        if self.target_resolution_stage == "jump":
            controller.set_left_stick(
                self._clamp(self.visual_goal.x * 0.50, -0.35, 0.35),
                self.mobility_jump_forward,
            )
            self._neutralized = False
            if ctx.now < self.target_resolution_until:
                self.current_action = "jak: V16 target jump follow-through"
                return self.current_action
            self.target_resolution_active = False
            return self._start_target_resolution(controller, ctx)

        direction = self.target_resolution_direction
        controller.set_left_stick(
            direction * self.target_bypass_turn,
            self.target_bypass_forward,
        )
        controller.set_right_stick(-direction * 0.18, 0.0)
        self._neutralized = False
        if ctx.now < self.target_resolution_until:
            self.current_action = (
                f"jak: V16 target bypass {'R' if direction > 0 else 'L'}"
            )
            return self.current_action
        self.target_resolution_active = False
        return self._start_target_resolution(controller, ctx)

    def _mobility_safe(self) -> bool:
        return bool(
            not self.water_escape_active
            and not self.water_geometry_confirmed
            and not self.shoreline_risk.active
            and not self.land_scan_active
            and not self.skill_active
            and not self.target_resolution_active
        )

    def _refresh_mobility_stall(self, ctx: ProfileContext) -> None:
        if not self._mobility_safe() or self._visual_goal_actionable():
            self.mobility_low_motion_since = None
            return
        if float(ctx.motion) <= self.mobility_stall_motion_max:
            if self.mobility_low_motion_since is None:
                self.mobility_low_motion_since = ctx.now
        else:
            self.mobility_low_motion_since = None

    def _mobility_due(self, ctx: ProfileContext) -> bool:
        return bool(
            self._mobility_safe()
            and self.mobility_low_motion_since is not None
            and ctx.now - self.mobility_low_motion_since >= self.mobility_stall_seconds
        )

    def _start_mobility_probe(self, controller: Controller, ctx: ProfileContext) -> str:
        self.mobility_active = True
        self.mobility_stage = "jump"
        self.mobility_until = ctx.now + self.mobility_stage_seconds
        self.mobility_direction = -1.0 if self.route_bias > 0 else 1.0
        self.mobility_attempts += 1
        controller.set_left_stick(0.0, self.mobility_jump_forward)
        controller.tap("cross", 0.07)
        self._neutralized = False
        self.current_action = "jak: V16 mobility probe -> hop small ledge"
        return self.current_action

    def _service_mobility_probe(self, controller: Controller, ctx: ProfileContext) -> str:
        if self.water_escape_active or self.shoreline_risk.active:
            self.mobility_active = False
            return self._service_shoreline_guard(controller, ctx) if not self.water_escape_active else self._water_escape(controller, ctx)

        if float(ctx.motion) >= self.mobility_success_motion and ctx.now >= self.mobility_until:
            self.mobility_active = False
            self.mobility_low_motion_since = None
            self.mobility_successes += 1
            controller.set_left_stick(self.route_bias, self.goal_pursuit_forward)
            self._neutralized = False
            self.current_action = "jak: V16 mobility restored after ledge/contact"
            return self.current_action

        if self.mobility_stage == "jump":
            controller.set_left_stick(0.0, self.mobility_jump_forward)
            if ctx.now < self.mobility_until:
                self.current_action = "jak: V16 mobility hop follow-through"
                return self.current_action
            controller.tap("cross", 0.07)
            self.mobility_double_jumps += 1
            self.mobility_stage = "double"
            self.mobility_until = ctx.now + self.mobility_stage_seconds
            self.current_action = "jak: V16 mobility second jump"
            return self.current_action

        if self.mobility_stage == "double":
            controller.set_left_stick(0.0, self.mobility_jump_forward)
            if ctx.now < self.mobility_until:
                self.current_action = "jak: V16 mobility climb follow-through"
                return self.current_action
            self.mobility_stage = "bypass"
            self.mobility_until = ctx.now + self.mobility_bypass_seconds

        direction = self.mobility_direction
        controller.set_left_stick(direction * 0.72, 0.38)
        controller.set_right_stick(-direction * 0.22, 0.0)
        self._neutralized = False
        if ctx.now < self.mobility_until:
            self.current_action = f"jak: V16 mobility sidestep {'R' if direction > 0 else 'L'}"
            return self.current_action

        self.mobility_active = False
        self.mobility_low_motion_since = None
        self.mobility_failures += 1
        self.next_land_scan_at = min(self.next_land_scan_at, ctx.now)
        return self._start_land_scan(controller, ctx, reason="v16-mobility-failed")

    def _on_foot(self, controller: Controller, ctx: ProfileContext) -> str:
        self._v16_now = ctx.now
        self._expire_target_blacklist(ctx.now)

        # Refresh safety before anything that can move Jak.
        if not self.land_scan_active:
            self._refresh_water_state(ctx)
            if self.water_escape_active:
                self.target_resolution_active = False
                self.mobility_active = False
                return super()._on_foot(controller, ctx)

        if self.shoreline_risk.active and not self.skill_active:
            self.target_resolution_active = False
            self.mobility_active = False
            return self._service_shoreline_guard(controller, ctx)

        # After climbing out, preserve an inland heading even if no target is visible.
        if (
            ctx.now < self.shore_exit_commit_until
            and self.shore_exit_direction
            and not self._visual_goal_actionable()
            and not self.land_scan_active
            and not self.skill_active
        ):
            controller.set_left_stick(self.shore_exit_direction * 0.24, 0.56)
            controller.set_right_stick(-self.shore_exit_direction * 0.08, 0.0)
            self._neutralized = False
            self.current_action = "jak: V16 continue inland after shore exit"
            return self.current_action

        if self.target_resolution_active:
            return self._service_target_resolution(controller, ctx)
        if self.mobility_active:
            return self._service_mobility_probe(controller, ctx)

        # Refresh V15 goal/ledge perception here so target validation can preempt the
        # base pursuit before it spends 25 seconds pushing into scenery.
        self._refresh_visual_goal(ctx)
        self._refresh_ledge_cue(ctx)
        self._track_target(ctx)
        if self._target_stalled(ctx):
            return self._start_target_resolution(controller, ctx)

        self._refresh_mobility_stall(ctx)
        if self._mobility_due(ctx):
            return self._start_mobility_probe(controller, ctx)

        return super()._on_foot(controller, ctx)

    # --- Specialist modes -------------------------------------------------
    # These are intentionally isolated so future mode-specific perception can grow
    # without contaminating on-foot platforming semantics.

    def _specialist_stalled(self, ctx: ProfileContext) -> bool:
        low = float(ctx.motion) <= self.mobility_stall_motion_max
        if low:
            if self.specialist_stall_since is None:
                self.specialist_stall_since = ctx.now
        else:
            self.specialist_stall_since = None
        return bool(
            self.specialist_stall_since is not None
            and ctx.now - self.specialist_stall_since >= 1.25
        )

    def _zoomer(self, controller: Controller, ctx: ProfileContext) -> str:
        stalled = self._specialist_stalled(ctx)
        if stalled:
            controller.release("cross")
            controller.tap("square", 0.16)  # brake before a hard recovery turn
            self.specialist_turn_direction *= -1.0
            d = self.specialist_turn_direction
            controller.set_left_stick(d * 0.90, 0.0)
            controller.tap("l1" if d < 0 else "r1", 0.08)
            self.specialist_action_at = ctx.now + 0.75
            self.specialist_stall_since = None
            self.specialist_recoveries += 1
            self.zoomer_brakes += 1
            self.zoomer_hops += 1
            self._neutralized = False
            self.current_action = "jak: V16 Zoomer brake + hard-turn/hop recovery"
            return self.current_action
        if ctx.now < self.specialist_action_at:
            return self.current_action
        steer = self._clamp(self.route_bias * 1.6, -0.38, 0.38)
        if abs(steer) < 0.05:
            steer = self.production_random.uniform(-0.16, 0.16)
        controller.set_left_stick(steer, 0.0)
        controller.hold("cross")
        self.held_cross_until = ctx.now + 1.0
        if self.production_random.random() < 0.10:
            controller.tap("l1" if steer < 0 else "r1", 0.07)
            self.zoomer_hops += 1
        self.specialist_action_at = ctx.now + 0.85
        self._neutralized = False
        self.current_action = f"jak: V16 Zoomer route-hold steer={steer:+.2f}"
        return self.current_action

    def _flut_flut(self, controller: Controller, ctx: ProfileContext) -> str:
        stalled = self._specialist_stalled(ctx)
        heading = self._clamp(self.route_bias, -0.30, 0.30)
        controller.set_left_stick(heading, 0.72)
        if stalled or ctx.now >= self.specialist_action_at:
            controller.tap("cross", 0.07)
            controller.tap("cross", 0.07)
            self.flut_flutters += 1
            self.specialist_action_at = ctx.now + (0.55 if stalled else 1.15)
            if stalled:
                self.specialist_turn_direction *= -1.0
                controller.set_left_stick(self.specialist_turn_direction * 0.42, 0.64)
                self.specialist_stall_since = None
                self.specialist_recoveries += 1
        self._neutralized = False
        self.current_action = "jak: V16 Flut Flut traverse + flutter control"
        return self.current_action

    def _cannon(self, controller: Controller, ctx: ProfileContext) -> str:
        if ctx.now >= self.specialist_action_at:
            self.specialist_turn_direction *= -1.0
            self.specialist_heading = self.specialist_turn_direction * 0.48
            self.specialist_action_at = ctx.now + 1.15
        controller.set_left_stick(self.specialist_heading, 0.0)
        # Frequent short shots keep the cannon useful without locking a charged shot
        # indefinitely; later target perception can replace the scan oscillator.
        if int(ctx.now * 4.0) % 3 == 0:
            controller.tap("cross", 0.10)
            self.cannon_shots += 1
        self._neutralized = False
        self.current_action = f"jak: V16 cannon sweep/fire x={self.specialist_heading:+.2f}"
        return self.current_action

    def _fishing(self, controller: Controller, ctx: ProfileContext) -> str:
        # Fishing is a 1-D tracking problem. Track the strongest moving component in
        # the lower-middle playfield using frame differencing. This intentionally does
        # not invent poison-eel classification; that remains a later visual calibration
        # layer, but the net no longer sits completely idle when the mode is recognized.
        if ctx.previous_frame is None or ctx.previous_frame.shape != ctx.frame.shape:
            controller.set_left_stick(0.0, 0.0)
            self.fishing_no_target += 1
            self.current_action = "jak: V16 fishing wait for motion frame"
            return self.current_action
        h, w = ctx.frame.shape[:2]
        y0, y1 = int(0.30 * h), int(0.88 * h)
        gray = cv2.cvtColor(ctx.frame[y0:y1], cv2.COLOR_BGR2GRAY)
        prev = cv2.cvtColor(ctx.previous_frame[y0:y1], cv2.COLOR_BGR2GRAY)
        diff = cv2.absdiff(gray, prev)
        mask = (diff >= 18).astype(np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        count, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        best = None
        best_area = 0
        for idx in range(1, count):
            area = int(stats[idx, cv2.CC_STAT_AREA])
            if 12 <= area <= int(0.06 * mask.size) and area > best_area:
                best = centroids[idx]
                best_area = area
        if best is None:
            controller.set_left_stick(0.0, 0.0)
            self.fishing_no_target += 1
            self.current_action = "jak: V16 fishing scan; no fish track"
            return self.current_action
        nx = self._clamp((float(best[0]) / max(w - 1, 1) - 0.5) * 2.0, -1.0, 1.0)
        controller.set_left_stick(nx * 0.85, 0.0)
        self.fishing_tracks += 1
        self._neutralized = False
        self.current_action = f"jak: V16 fishing track moving fish x={nx:+.2f}"
        return self.current_action

    def _production_gameplay(self, controller: Controller, ctx: ProfileContext) -> str:
        # V3's dispatcher has no fishing implementation. Detect the mode early enough
        # to route it to V16; all other modes continue through the inherited dispatcher
        # and therefore retain menu/progress/safety behavior.
        self._update_control_mode()
        if self.control_mode == JakControlMode.FISHING:
            self.scene_metrics = self.scene_memory.observe(ctx.frame, ctx.previous_frame, ctx.now)
            self.last_gameplay_at = ctx.now
            self._release_timed_holds(controller, ctx)
            if self._maybe_progress_probe(controller, ctx):
                return self.current_action
            return self._fishing(controller, ctx)
        return super()._production_gameplay(controller, ctx)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "jak_policy_version": "v16",
                "jak_target_signature": None if self.target_signature is None else list(self.target_signature),
                "jak_target_resolution_active": self.target_resolution_active,
                "jak_target_resolution_stage": self.target_resolution_stage,
                "jak_target_resolution_attempts": self.target_resolution_attempts,
                "jak_target_stalls": self.target_stalls,
                "jak_target_blacklists": self.target_blacklists,
                "jak_target_blacklist_size": len(self.target_blacklist),
                "jak_target_bypasses": self.target_bypasses,
                "jak_target_jump_resolutions": self.target_jump_resolutions,
                "jak_target_progress_events": self.target_progress_events,
                "jak_mobility_active": self.mobility_active,
                "jak_mobility_stage": self.mobility_stage,
                "jak_mobility_attempts": self.mobility_attempts,
                "jak_mobility_successes": self.mobility_successes,
                "jak_mobility_failures": self.mobility_failures,
                "jak_mobility_double_jumps": self.mobility_double_jumps,
                "jak_shoreline_guard_active": self.shoreline_risk.active,
                "jak_shoreline_guard_ratio": round(self.shoreline_risk.total, 3),
                "jak_shoreline_guard_lcr": [
                    round(self.shoreline_risk.left, 3),
                    round(self.shoreline_risk.center, 3),
                    round(self.shoreline_risk.right, 3),
                ],
                "jak_shoreline_guard_ticks": self.shoreline_guard_ticks,
                "jak_shoreline_entries": self.shoreline_entries,
                "jak_shore_exit_commits": self.shore_exit_commits,
                "jak_shore_exit_commit_active": ctx.now < self.shore_exit_commit_until,
                "jak_semantic_mode_hint": self.semantic_mode_hint.value,
                "jak_specialist_recoveries": self.specialist_recoveries,
                "jak_zoomer_hops": self.zoomer_hops,
                "jak_zoomer_brakes": self.zoomer_brakes,
                "jak_flut_flutters": self.flut_flutters,
                "jak_cannon_shots": self.cannon_shots,
                "jak_fishing_tracks": self.fishing_tracks,
                "jak_fishing_no_target": self.fishing_no_target,
                "jak_stream_intent": self._v16_stream_intent(),
            }
        )
        return state

    def _v16_stream_intent(self) -> str:
        if self.water_escape_active:
            return "SAFETY · ESCAPE WATER"
        if self.shoreline_risk.active:
            return "SAFETY · AVOID SHORELINE"
        if self.target_resolution_active:
            return f"NAV · SOLVE BLOCKED {self.visual_goal.kind.upper()}"
        if self.mobility_active:
            return "PLATFORM · CLEAR SMALL LEDGE"
        if self._visual_goal_actionable():
            return f"TARGET · {self.visual_goal.kind.upper()}"
        if self.control_mode != JakControlMode.ON_FOOT:
            return f"MODE · {self.control_mode.value.upper()}"
        return f"{self.objective.goal} · {self.objective.subgoal}"
