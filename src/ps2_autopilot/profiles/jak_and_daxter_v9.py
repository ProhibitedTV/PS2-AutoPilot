from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .jak_and_daxter_v8 import JakAndDaxterV8Profile


@dataclass(frozen=True)
class WaterGeometry:
    candidate_ratio: float = 0.0
    largest_component_ratio: float = 0.0
    largest_width_ratio: float = 0.0
    largest_height_ratio: float = 0.0
    coherence: float = 0.0
    max_row_coverage: float = 0.0
    row_band_fraction: float = 0.0
    confirmed: bool = False


class JakAndDaxterV9Profile(JakAndDaxterV8Profile):
    """Add geometry-aware water perception and closed-loop local navigation recovery.

    The first V8 live run reached a useful failure state: Jak was wedged against dark
    Geyser Rock geometry under a large leaf while the V7 color-only water detector
    repeatedly reported ~0.12-0.30 blue/cyan coverage. The shoreline policy then kept
    commanding movement for more than twenty seconds even though visual motion had
    collapsed almost to zero.

    V9 treats color as only the first water cue. A candidate must also resemble a
    coherent horizontal surface: a sufficiently large connected component, meaningful
    horizontal span/coherence, or a broad row band. Fragmented blue rock/foliage can no
    longer own shoreline recovery simply because its hue falls in the water range.

    V9 also closes the action/outcome loop. If locomotion has been commanded but the
    scene remains nearly static for a short period, it performs a bounded reverse,
    pivot, and forward-test escape locally instead of waiting 25 seconds for the global
    watchdog. Failed directions are alternated and the successful escape direction is
    held as a short route bias so Jak does not immediately drive back into the obstacle.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        # Water geometry verification. The live false-positive capture measured about
        # 0.12 raw candidate coverage but only ~0.013 largest-component area at native
        # resolution, ~0.096 component width and <0.25 maximum per-row coverage.
        self.water_geometry_max_width = max(
            160, min(960, int(cfg.get("water_geometry_max_width", 480)))
        )
        self.water_component_min_ratio = max(
            0.005, min(0.30, float(cfg.get("water_component_min_ratio", 0.020)))
        )
        self.water_component_min_width = max(
            0.05, min(0.90, float(cfg.get("water_component_min_width", 0.18)))
        )
        self.water_component_min_coherence = max(
            0.05, min(1.0, float(cfg.get("water_component_min_coherence", 0.16)))
        )
        self.water_row_coverage_threshold = max(
            0.05, min(0.95, float(cfg.get("water_row_coverage_threshold", 0.30)))
        )
        self.water_row_max_threshold = max(
            self.water_row_coverage_threshold,
            min(1.0, float(cfg.get("water_row_max_threshold", 0.42))),
        )
        self.water_row_band_min_fraction = max(
            0.01, min(0.80, float(cfg.get("water_row_band_min_fraction", 0.04)))
        )
        self.water_risk_confirmations_required = max(
            1, min(6, int(cfg.get("water_risk_confirmations_required", 2)))
        )
        self.water_direction_reconsider_seconds = max(
            0.5, float(cfg.get("water_direction_reconsider_seconds", 1.75))
        )
        self.water_direction_flip_margin = max(
            0.02, min(0.60, float(cfg.get("water_direction_flip_margin", 0.10)))
        )

        self.water_geometry = WaterGeometry()
        self.water_geometry_confirmed = False
        self.water_risk_streak = 0
        self.water_false_color_frames = 0
        self.water_geometry_confirmations = 0
        self.water_direction_reconsider_at = 0.0
        self.water_direction_flips = 0

        # Local action/outcome recovery. This deliberately operates much faster than
        # the 25-second runtime watchdog, but only inside proven gameplay.
        self.local_stuck_motion_threshold = max(
            0.0005, min(0.05, float(cfg.get("local_stuck_motion_threshold", 0.0035)))
        )
        self.local_stuck_trigger_seconds = max(
            0.6, float(cfg.get("local_stuck_trigger_seconds", 2.2))
        )
        self.local_stuck_command_warmup_seconds = max(
            0.2, float(cfg.get("local_stuck_command_warmup_seconds", 0.55))
        )
        self.local_stuck_reverse_seconds = max(
            0.25, float(cfg.get("local_stuck_reverse_seconds", 0.70))
        )
        self.local_stuck_pivot_seconds = max(
            0.25, float(cfg.get("local_stuck_pivot_seconds", 0.80))
        )
        self.local_stuck_test_seconds = max(
            0.4, float(cfg.get("local_stuck_test_seconds", 1.15))
        )
        self.local_stuck_reverse_speed = max(
            0.25, min(0.95, float(cfg.get("local_stuck_reverse_speed", 0.62)))
        )
        self.local_stuck_pivot_turn = max(
            0.30, min(1.0, float(cfg.get("local_stuck_pivot_turn", 0.86)))
        )
        self.local_stuck_camera_turn = max(
            0.10, min(0.80, float(cfg.get("local_stuck_camera_turn", 0.38)))
        )
        self.local_stuck_test_forward = max(
            0.20, min(0.90, float(cfg.get("local_stuck_test_forward", 0.58)))
        )
        self.local_stuck_success_motion = max(
            self.local_stuck_motion_threshold,
            min(0.15, float(cfg.get("local_stuck_success_motion", 0.010)))
        )
        self.local_stuck_success_seconds = max(
            0.15, float(cfg.get("local_stuck_success_seconds", 0.30))
        )
        self.local_stuck_max_nonjump_attempts = max(
            1, min(4, int(cfg.get("local_stuck_max_nonjump_attempts", 2)))
        )
        self.local_stuck_jump_turn_seconds = max(
            0.3, float(cfg.get("local_stuck_jump_turn_seconds", 0.75))
        )
        self.local_stuck_cooldown_seconds = max(
            0.2, float(cfg.get("local_stuck_cooldown_seconds", 1.4))
        )
        self.local_stuck_route_memory_seconds = max(
            1.0, float(cfg.get("local_stuck_route_memory_seconds", 5.5))
        )

        self.local_stuck_armed_at: float | None = None
        self.local_stuck_low_motion_since: float | None = None
        self.local_stuck_active = False
        self.local_stuck_stage = "none"
        self.local_stuck_stage_until = 0.0
        self.local_stuck_direction = 1.0
        self.local_stuck_next_direction = 1.0
        self.local_stuck_cycle_attempt = 0
        self.local_stuck_final_test = False
        self.local_stuck_jump_sent = False
        self.local_stuck_success_since: float | None = None
        self.local_stuck_cooldown_until = 0.0
        self.local_stuck_last_trigger_action = ""
        self.local_stuck_triggers = 0
        self.local_stuck_successes = 0
        self.local_stuck_failures = 0
        self.local_stuck_jump_escalations = 0

    def _water_candidate_mask(self, frame: np.ndarray) -> np.ndarray:
        if frame is None or frame.size == 0:
            return np.zeros((0, 0), dtype=np.uint8)
        h, w = frame.shape[:2]
        x0, x1, y0, y1 = self.WATER_ROI
        roi = frame[
            max(0, int(round(y0 * h))):min(h, int(round(y1 * h))),
            max(0, int(round(x0 * w))):min(w, int(round(x1 * w))),
        ]
        if roi.size == 0:
            return np.zeros((0, 0), dtype=np.uint8)
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = (
            (hsv[:, :, 0] >= self.water_hue_min)
            & (hsv[:, :, 0] <= self.water_hue_max)
            & (hsv[:, :, 1] >= self.water_sat_min)
            & (hsv[:, :, 2] >= self.water_value_min)
        ).astype(np.uint8)
        return mask

    def _water_geometry(self, frame: np.ndarray) -> WaterGeometry:
        mask = self._water_candidate_mask(frame)
        if mask.size == 0:
            return WaterGeometry()

        candidate_ratio = float(np.count_nonzero(mask)) / float(mask.size)
        if candidate_ratio <= 0.0:
            return WaterGeometry(candidate_ratio=0.0)

        work = mask
        if work.shape[1] > self.water_geometry_max_width:
            target_w = self.water_geometry_max_width
            target_h = max(1, int(round(work.shape[0] * target_w / work.shape[1])))
            work = cv2.resize(work, (target_w, target_h), interpolation=cv2.INTER_NEAREST)

        # Close only tiny gaps caused by ripples/render sampling. A deliberately small
        # kernel does not merge separate blue rocks/leaves into an artificial ocean.
        work = cv2.morphologyEx(
            work,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)),
        )

        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(work, 8)
        largest_component_ratio = 0.0
        largest_width_ratio = 0.0
        largest_height_ratio = 0.0
        coherence = 0.0
        if count > 1:
            component_stats = stats[1:]
            largest_offset = int(np.argmax(component_stats[:, cv2.CC_STAT_AREA]))
            largest = component_stats[largest_offset]
            largest_area = float(largest[cv2.CC_STAT_AREA])
            largest_component_ratio = largest_area / float(max(work.size, 1))
            largest_width_ratio = float(largest[cv2.CC_STAT_WIDTH]) / float(max(work.shape[1], 1))
            largest_height_ratio = float(largest[cv2.CC_STAT_HEIGHT]) / float(max(work.shape[0], 1))
            candidate_pixels = float(max(np.count_nonzero(work), 1))
            coherence = largest_area / candidate_pixels

        row_coverage = np.mean(work > 0, axis=1) if work.shape[0] else np.zeros((0,))
        max_row_coverage = float(np.max(row_coverage)) if row_coverage.size else 0.0
        row_band_fraction = (
            float(np.mean(row_coverage >= self.water_row_coverage_threshold))
            if row_coverage.size
            else 0.0
        )

        component_confirmed = bool(
            largest_component_ratio >= self.water_component_min_ratio
            and largest_width_ratio >= self.water_component_min_width
            and coherence >= self.water_component_min_coherence
        )
        band_confirmed = bool(
            max_row_coverage >= self.water_row_max_threshold
            and row_band_fraction >= self.water_row_band_min_fraction
        )
        return WaterGeometry(
            candidate_ratio=candidate_ratio,
            largest_component_ratio=largest_component_ratio,
            largest_width_ratio=largest_width_ratio,
            largest_height_ratio=largest_height_ratio,
            coherence=coherence,
            max_row_coverage=max_row_coverage,
            row_band_fraction=row_band_fraction,
            confirmed=bool(component_confirmed or band_confirmed),
        )

    def _refresh_water_state(self, ctx: ProfileContext) -> None:
        (
            self.water_ratio_total,
            self.water_ratio_left,
            self.water_ratio_center,
            self.water_ratio_right,
        ) = self._water_ratios(ctx.frame)
        self.water_geometry = self._water_geometry(ctx.frame)

        raw_risk = bool(
            self.water_ratio_center >= self.water_center_caution
            or self.water_ratio_total >= self.water_total_caution
        )
        geometry_risk = bool(raw_risk and self.water_geometry.confirmed)
        self.water_geometry_confirmed = geometry_risk

        if raw_risk and not geometry_risk:
            self.water_false_color_frames += 1
        if geometry_risk:
            self.water_risk_streak += 1
            self.water_geometry_confirmations += 1
        else:
            self.water_risk_streak = 0

        # Once escape is active, geometry evidence owns persistence. Initial entry asks
        # for a tiny temporal quorum to reject one-frame color/layout glitches.
        risk = bool(
            geometry_risk
            and (
                self.water_escape_active
                or self.water_risk_streak >= self.water_risk_confirmations_required
            )
        )
        if risk:
            self.water_clear_since = None
            desired = 1.0 if self.water_ratio_right < self.water_ratio_left else -1.0
            if not self.water_escape_active:
                self.water_escape_active = True
                self.water_escape_stage = "backtrack"
                self.water_escape_stage_until = ctx.now + self.water_backtrack_seconds
                self.water_escape_direction = desired
                self.water_direction_reconsider_at = (
                    ctx.now + self.water_direction_reconsider_seconds
                )
                self.water_avoidance_events += 1
                return

            # Direction hysteresis: do not alternate left/right every video frame when
            # two shoreline sectors have nearly identical blue coverage.
            if ctx.now >= self.water_direction_reconsider_at:
                delta = abs(self.water_ratio_right - self.water_ratio_left)
                if delta >= self.water_direction_flip_margin and desired != self.water_escape_direction:
                    self.water_escape_direction = desired
                    self.water_direction_flips += 1
                self.water_direction_reconsider_at = (
                    ctx.now + self.water_direction_reconsider_seconds
                )
            return

        if not self.water_escape_active:
            self.water_clear_since = None
            return

        # Fragmented blue/green scenery should release water ownership quickly even
        # when the raw HSV ratio remains above V7's old release threshold.
        if self.water_clear_since is None:
            self.water_clear_since = ctx.now
            return
        if ctx.now - self.water_clear_since >= self.water_clear_seconds:
            self.water_escape_active = False
            self.water_escape_stage = "none"
            self.water_escape_stage_until = 0.0
            self.water_clear_since = None
            self.next_production_action_at = ctx.now

    def _arm_local_stuck(self, ctx: ProfileContext) -> None:
        if self.local_stuck_armed_at is None:
            self.local_stuck_armed_at = ctx.now

    def _local_progress_motion(self, ctx: ProfileContext) -> float:
        return max(
            float(ctx.motion),
            float(self.scene_metrics.center_motion),
            float(self.scene_metrics.lower_motion),
        )

    def _start_local_stuck_escape(self, ctx: ProfileContext) -> None:
        # If coherent water is present, use the already-selected drier-side direction.
        # Otherwise alternate each new recovery cycle so repeated failures explore a
        # different solution instead of deterministically wedging into the same wall.
        if self.water_geometry_confirmed:
            direction = self.water_escape_direction
        else:
            direction = self.local_stuck_next_direction
            self.local_stuck_next_direction *= -1.0

        self.local_stuck_active = True
        self.local_stuck_stage = "reverse"
        self.local_stuck_stage_until = ctx.now + self.local_stuck_reverse_seconds
        self.local_stuck_direction = direction
        self.local_stuck_cycle_attempt = 0
        self.local_stuck_final_test = False
        self.local_stuck_jump_sent = False
        self.local_stuck_success_since = None
        self.local_stuck_last_trigger_action = self.current_action
        self.local_stuck_triggers += 1
        self.second_jump_pending = False
        self.next_production_action_at = ctx.now

        # Remember the escape direction for several seconds after recovery so the
        # ordinary route sampler does not immediately steer back into the same object.
        self.route_bias = self._clamp(direction * 0.30, -0.36, 0.36)
        self.next_route_bias_at = ctx.now + self.local_stuck_route_memory_seconds

    def _refresh_local_stuck(self, ctx: ProfileContext) -> None:
        if self.local_stuck_active:
            return
        if ctx.now < self.local_stuck_cooldown_until:
            self.local_stuck_low_motion_since = None
            return
        if self.local_stuck_armed_at is None:
            return
        if ctx.now - self.local_stuck_armed_at < self.local_stuck_command_warmup_seconds:
            return

        progress_motion = self._local_progress_motion(ctx)
        if progress_motion > self.local_stuck_motion_threshold:
            self.local_stuck_low_motion_since = None
            return
        if self.local_stuck_low_motion_since is None:
            self.local_stuck_low_motion_since = ctx.now
            return
        if ctx.now - self.local_stuck_low_motion_since >= self.local_stuck_trigger_seconds:
            self._start_local_stuck_escape(ctx)

    def _set_local_stuck_stage(self, stage: str, until: float) -> None:
        self.local_stuck_stage = stage
        self.local_stuck_stage_until = until
        self.local_stuck_success_since = None

    def _finish_local_stuck_escape(
        self, controller: Controller, ctx: ProfileContext, *, success: bool
    ) -> str:
        controller.neutral_sticks()
        self._neutralized = False
        self.local_stuck_active = False
        self.local_stuck_stage = "none"
        self.local_stuck_stage_until = 0.0
        self.local_stuck_low_motion_since = None
        self.local_stuck_armed_at = None
        self.local_stuck_cooldown_until = ctx.now + self.local_stuck_cooldown_seconds
        self.next_production_action_at = ctx.now
        self.route_bias = self._clamp(
            self.local_stuck_direction * 0.30, -0.36, 0.36
        )
        self.next_route_bias_at = ctx.now + self.local_stuck_route_memory_seconds
        if success:
            self.local_stuck_successes += 1
            self.current_action = "jak: V9 local obstacle escape succeeded; remember heading"
        else:
            self.local_stuck_failures += 1
            self.current_action = "jak: V9 local obstacle escape exhausted; resample route"
        return self.current_action

    def _local_stuck_escape(self, controller: Controller, ctx: ProfileContext) -> str:
        direction = self.local_stuck_direction
        self.second_jump_pending = False

        if self.local_stuck_stage == "reverse":
            if ctx.now >= self.local_stuck_stage_until:
                self._set_local_stuck_stage(
                    "pivot", ctx.now + self.local_stuck_pivot_seconds
                )
            else:
                controller.set_left_stick(
                    direction * 0.18, -self.local_stuck_reverse_speed
                )
                controller.set_right_stick(-direction * 0.12, 0.0)
                self._neutralized = False
                self.current_action = "jak: V9 obstacle escape reverse"
                return self.current_action

        if self.local_stuck_stage == "pivot":
            if ctx.now >= self.local_stuck_stage_until:
                self._set_local_stuck_stage(
                    "test", ctx.now + self.local_stuck_test_seconds
                )
            else:
                controller.set_left_stick(direction * self.local_stuck_pivot_turn, 0.06)
                controller.set_right_stick(
                    -direction * self.local_stuck_camera_turn, 0.0
                )
                self._neutralized = False
                self.current_action = "jak: V9 obstacle escape pivot"
                return self.current_action

        if self.local_stuck_stage == "jump-turn":
            if not self.local_stuck_jump_sent:
                controller.tap("cross", 0.07)
                controller.tap("cross", 0.07)
                self.double_jumps += 1
                self.local_stuck_jump_escalations += 1
                self.local_stuck_jump_sent = True
            if ctx.now >= self.local_stuck_stage_until:
                self.local_stuck_final_test = True
                self._set_local_stuck_stage(
                    "test", ctx.now + self.local_stuck_test_seconds
                )
            else:
                controller.set_left_stick(direction * 0.62, 0.34)
                controller.set_right_stick(-direction * 0.24, 0.0)
                self._neutralized = False
                self.current_action = "jak: V9 obstacle escape jump-turn escalation"
                return self.current_action

        # Forward test contains no camera command, so sustained visual motion here is
        # useful evidence that Jak actually translated away from the obstacle rather
        # than merely rotating the camera.
        controller.set_left_stick(direction * 0.20, self.local_stuck_test_forward)
        controller.set_right_stick(0.0, 0.0)
        self._neutralized = False
        motion = max(float(ctx.motion), float(self.scene_metrics.lower_motion))
        if motion >= self.local_stuck_success_motion:
            if self.local_stuck_success_since is None:
                self.local_stuck_success_since = ctx.now
            elif ctx.now - self.local_stuck_success_since >= self.local_stuck_success_seconds:
                return self._finish_local_stuck_escape(controller, ctx, success=True)
        else:
            self.local_stuck_success_since = None

        if ctx.now < self.local_stuck_stage_until:
            self.current_action = (
                f"jak: V9 obstacle escape forward test motion={motion:.4f}"
            )
            return self.current_action

        if self.local_stuck_final_test:
            return self._finish_local_stuck_escape(controller, ctx, success=False)

        self.local_stuck_cycle_attempt += 1
        if self.local_stuck_cycle_attempt < self.local_stuck_max_nonjump_attempts:
            self.local_stuck_direction *= -1.0
            self.route_bias = self._clamp(
                self.local_stuck_direction * 0.30, -0.36, 0.36
            )
            self.next_route_bias_at = ctx.now + self.local_stuck_route_memory_seconds
            self._set_local_stuck_stage(
                "reverse", ctx.now + self.local_stuck_reverse_seconds
            )
            self.current_action = "jak: V9 obstacle escape retry opposite side"
            return self.current_action

        # Jumping near confirmed water is a poor escalation. In that case finish the
        # local cycle and let the coherent shoreline policy choose the next route.
        if self.water_geometry_confirmed:
            return self._finish_local_stuck_escape(controller, ctx, success=False)

        self.local_stuck_jump_sent = False
        self._set_local_stuck_stage(
            "jump-turn", ctx.now + self.local_stuck_jump_turn_seconds
        )
        self.current_action = "jak: V9 obstacle escape escalate to jump-turn"
        return self.current_action

    def _water_escape(self, controller: Controller, ctx: ProfileContext) -> str:
        action = super()._water_escape(controller, ctx)
        self._arm_local_stuck(ctx)
        return action

    def _anti_loop(self, controller: Controller, ctx: ProfileContext) -> str:
        action = super()._anti_loop(controller, ctx)
        self._arm_local_stuck(ctx)
        return action

    def _on_foot(self, controller: Controller, ctx: ProfileContext) -> str:
        # V9 evaluates both perception ownership and motion outcome every gameplay tick.
        self._refresh_water_state(ctx)
        self._refresh_local_stuck(ctx)
        if self.local_stuck_active:
            return self._local_stuck_escape(controller, ctx)
        if self.water_escape_active:
            return self._water_escape(controller, ctx)

        if self._service_jump_combo(controller, ctx):
            return self.current_action
        if ctx.now < self.next_production_action_at:
            return self.current_action

        looping = self.scene_metrics.loop_similarity >= self.loop_similarity_threshold
        stagnant = (
            self.scene_metrics.center_motion <= self.stagnant_motion_threshold
            and self.scene_metrics.lower_motion <= self.stagnant_motion_threshold
        )
        if looping or stagnant:
            return self._anti_loop(controller, ctx)

        if ctx.now >= self.next_route_bias_at:
            self.route_bias = self.production_random.uniform(-0.26, 0.26)
            self.next_route_bias_at = ctx.now + self.production_random.uniform(3.5, 6.5)

        heading = self._clamp(
            self.route_bias + self.production_random.uniform(-0.08, 0.08),
            -0.36,
            0.36,
        )
        forward = self.production_forward * self.safe_forward_scale
        camera = self._clamp(
            heading * 0.28, -self.production_camera, self.production_camera
        )
        controller.set_left_stick(heading, forward)
        controller.set_right_stick(camera, 0.0)
        self._neutralized = False
        self._arm_local_stuck(ctx)

        suffix = ""
        if self.production_random.random() < self.production_jump_probability * self.safe_jump_scale:
            suffix = self._schedule_jump(controller, ctx)

        duration = self.production_random.uniform(
            self.production_burst_min, self.production_burst_max
        )
        self.next_production_action_at = ctx.now + duration
        self.production_bursts += 1
        self.current_action = (
            f"jak: V9 on-foot arc x={heading:+.2f} fwd={forward:.2f} "
            f"water={self.water_ratio_total:.2f}/geom={int(self.water_geometry_confirmed)}{suffix}"
        )
        return self.current_action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        low_motion_seconds = (
            0.0
            if self.local_stuck_low_motion_since is None
            else max(0.0, ctx.now - self.local_stuck_low_motion_since)
        )
        state.update(
            {
                "jak_policy_version": "v9",
                "jak_water_geometry_confirmed": self.water_geometry_confirmed,
                "jak_water_candidate_ratio": round(self.water_geometry.candidate_ratio, 4),
                "jak_water_component_ratio": round(
                    self.water_geometry.largest_component_ratio, 4
                ),
                "jak_water_component_width": round(
                    self.water_geometry.largest_width_ratio, 4
                ),
                "jak_water_component_coherence": round(
                    self.water_geometry.coherence, 4
                ),
                "jak_water_max_row_coverage": round(
                    self.water_geometry.max_row_coverage, 4
                ),
                "jak_water_row_band_fraction": round(
                    self.water_geometry.row_band_fraction, 4
                ),
                "jak_water_risk_streak": self.water_risk_streak,
                "jak_water_false_color_frames": self.water_false_color_frames,
                "jak_water_direction_flips": self.water_direction_flips,
                "jak_local_stuck_active": self.local_stuck_active,
                "jak_local_stuck_stage": self.local_stuck_stage,
                "jak_local_stuck_low_motion_seconds": round(low_motion_seconds, 2),
                "jak_local_stuck_triggers": self.local_stuck_triggers,
                "jak_local_stuck_successes": self.local_stuck_successes,
                "jak_local_stuck_failures": self.local_stuck_failures,
                "jak_local_stuck_jump_escalations": self.local_stuck_jump_escalations,
                "jak_route_bias": round(self.route_bias, 3),
            }
        )
        return state
