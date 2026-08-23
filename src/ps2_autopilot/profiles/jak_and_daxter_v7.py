from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .jak_and_daxter import JakPhase
from .jak_and_daxter_v6 import JakAndDaxterV6Profile


class JakAndDaxterV7Profile(JakAndDaxterV6Profile):
    """Make first-area traversal safer and allow supervised mid-session restarts.

    V6 can now boot, create a save and reach gameplay. The first real gameplay pass
    exposed two production problems:

    1. V3's generic on-foot policy mostly drives forward with only tiny heading
       variation, which can walk/swim straight off Geyser Rock into the ocean.
    2. Restarting AutoPilot while the game is already in gameplay loses the launch
       timer, so the fail-closed policy has no safe way to reacquire gameplay.

    V7 adds a cheap shoreline/water detector over the lower playfield and a bounded
    escape policy that backtracks first, then steers toward the side with less water.
    It also adds a reversible right-stick camera-response probe for reacquiring a
    running gameplay session without blindly assuming every unknown screen is safe.
    """

    WATER_ROI = (0.05, 0.95, 0.54, 0.98)
    GAMEPLAY_BLOCKERS = (
        "SAVE",
        "LOADGAME",
        "NEWGAME",
        "OPTIONS",
        "SELECTGAME",
        "MEMORYCARD",
        "SELECTFILE",
        "CONTINUEWITHOUTSAVING",
        "ERASE",
        "DELETE",
        "FORMAT",
        "COPY",
    )

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        # Water/shoreline perception. OpenCV hue is 0..179. Jak's Geyser Rock water
        # is strongly blue/cyan while the nearby rock/grass is much warmer.
        self.water_hue_min = int(cfg.get("water_hue_min", 85))
        self.water_hue_max = int(cfg.get("water_hue_max", 140))
        self.water_sat_min = int(cfg.get("water_sat_min", 45))
        self.water_value_min = int(cfg.get("water_value_min", 25))
        self.water_center_caution = max(
            0.02, min(0.80, float(cfg.get("water_center_caution", 0.09)))
        )
        self.water_total_caution = max(
            0.02, min(0.80, float(cfg.get("water_total_caution", 0.11)))
        )
        self.water_release_ratio = max(
            0.005, min(self.water_total_caution, float(cfg.get("water_release_ratio", 0.045)))
        )
        self.water_clear_seconds = max(0.3, float(cfg.get("water_clear_seconds", 1.0)))
        self.water_backtrack_seconds = max(0.4, float(cfg.get("water_backtrack_seconds", 1.6)))
        self.water_turn_seconds = max(0.4, float(cfg.get("water_turn_seconds", 1.4)))
        self.water_backtrack_speed = max(
            0.25, min(0.95, float(cfg.get("water_backtrack_speed", 0.72)))
        )
        self.water_escape_turn = max(
            0.35, min(1.0, float(cfg.get("water_escape_turn", 0.82)))
        )
        self.water_escape_forward = max(
            0.10, min(0.70, float(cfg.get("water_escape_forward", 0.28)))
        )

        self.water_ratio_total = 0.0
        self.water_ratio_left = 0.0
        self.water_ratio_center = 0.0
        self.water_ratio_right = 0.0
        self.water_escape_active = False
        self.water_escape_stage = "none"
        self.water_escape_stage_until = 0.0
        self.water_escape_direction = 1.0
        self.water_clear_since: float | None = None
        self.water_avoidance_events = 0
        self.water_escape_ticks = 0

        # Safer land routing: use gentle multi-second arcs instead of almost always
        # charging straight ahead. The parent jump probability is also scaled down
        # near the beginning of autonomous traversal.
        self.safe_forward_scale = max(
            0.45, min(1.0, float(cfg.get("safe_forward_scale", 0.82)))
        )
        self.safe_jump_scale = max(
            0.0, min(1.0, float(cfg.get("safe_jump_scale", 0.55)))
        )
        self.route_bias = 0.0
        self.next_route_bias_at = 0.0

        # Mid-session gameplay reacquisition. A tiny camera nudge is reversible and
        # ignored by ordinary cutscenes, but a player-controlled camera responds.
        self.runtime_started_at: float | None = None
        self.attach_probe_after_seconds = max(
            1.0, float(cfg.get("attach_probe_after_seconds", 4.0))
        )
        self.attach_probe_idle_seconds = max(
            0.4, float(cfg.get("attach_probe_idle_seconds", 0.8))
        )
        self.attach_probe_baseline_max = max(
            0.002, min(0.05, float(cfg.get("attach_probe_baseline_max", 0.02)))
        )
        self.attach_probe_response_min = max(
            0.004, min(0.20, float(cfg.get("attach_probe_response_min", 0.018)))
        )
        self.attach_probe_camera_x = max(
            0.10, min(0.55, float(cfg.get("attach_probe_camera_x", 0.30)))
        )
        self.attach_probe_drive_seconds = max(
            0.15, min(0.70, float(cfg.get("attach_probe_drive_seconds", 0.30)))
        )
        self.attach_probe_observe_seconds = max(
            0.25, float(cfg.get("attach_probe_observe_seconds", 0.80))
        )
        self.attach_probe_retry_seconds = max(
            0.5, float(cfg.get("attach_probe_retry_seconds", 1.5))
        )
        self.attach_probe_confirmations_required = max(
            2, min(4, int(cfg.get("attach_probe_confirmations_required", 2)))
        )

        self.attach_probe_stage = "idle"
        self.attach_probe_idle_since: float | None = None
        self.attach_probe_baseline_motion = 0.0
        self.attach_probe_release_at = 0.0
        self.attach_probe_deadline = 0.0
        self.attach_probe_next_at = 0.0
        self.attach_probe_peak_motion = 0.0
        self.attach_probe_confirmations = 0
        self.attach_probe_attempts = 0
        self.attach_probe_successes = 0
        self.attach_probe_direction = 1.0

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def _water_ratios(self, frame: np.ndarray) -> tuple[float, float, float, float]:
        if frame is None or frame.size == 0:
            return 0.0, 0.0, 0.0, 0.0
        h, w = frame.shape[:2]
        x0, x1, y0, y1 = self.WATER_ROI
        roi = frame[
            max(0, int(round(y0 * h))):min(h, int(round(y1 * h))),
            max(0, int(round(x0 * w))):min(w, int(round(x1 * w))),
        ]
        if roi.size == 0:
            return 0.0, 0.0, 0.0, 0.0

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue = hsv[:, :, 0]
        sat = hsv[:, :, 1]
        val = hsv[:, :, 2]
        water = (
            (hue >= self.water_hue_min)
            & (hue <= self.water_hue_max)
            & (sat >= self.water_sat_min)
            & (val >= self.water_value_min)
        )

        rw = water.shape[1]
        a = max(1, rw // 3)
        b = max(a + 1, (rw * 2) // 3)

        def ratio(mask: np.ndarray) -> float:
            return float(np.count_nonzero(mask)) / float(max(mask.size, 1))

        return ratio(water), ratio(water[:, :a]), ratio(water[:, a:b]), ratio(water[:, b:])

    def _refresh_water_state(self, ctx: ProfileContext) -> None:
        (
            self.water_ratio_total,
            self.water_ratio_left,
            self.water_ratio_center,
            self.water_ratio_right,
        ) = self._water_ratios(ctx.frame)

        risk = (
            self.water_ratio_center >= self.water_center_caution
            or self.water_ratio_total >= self.water_total_caution
        )
        if risk:
            self.water_clear_since = None
            direction = 1.0 if self.water_ratio_right < self.water_ratio_left else -1.0
            if abs(self.water_ratio_right - self.water_ratio_left) >= 0.015:
                self.water_escape_direction = direction
            if not self.water_escape_active:
                self.water_escape_active = True
                self.water_escape_stage = "backtrack"
                self.water_escape_stage_until = ctx.now + self.water_backtrack_seconds
                self.water_escape_direction = direction
                self.water_avoidance_events += 1
            return

        if not self.water_escape_active:
            return

        clear = (
            self.water_ratio_total <= self.water_release_ratio
            and self.water_ratio_center <= self.water_release_ratio
        )
        if not clear:
            self.water_clear_since = None
            return
        if self.water_clear_since is None:
            self.water_clear_since = ctx.now
            return
        if ctx.now - self.water_clear_since >= self.water_clear_seconds:
            self.water_escape_active = False
            self.water_escape_stage = "none"
            self.water_escape_stage_until = 0.0
            self.water_clear_since = None
            self.next_production_action_at = ctx.now

    def _water_escape(self, controller: Controller, ctx: ProfileContext) -> str:
        direction = self.water_escape_direction
        self.water_escape_ticks += 1
        self.second_jump_pending = False

        if self.water_escape_stage == "backtrack" and ctx.now < self.water_escape_stage_until:
            # The generic policy reached water by driving mostly forward, so the
            # safest first move is to retrace that motion while biasing toward the
            # side with less blue water in the lower frame.
            controller.set_left_stick(direction * 0.30, -self.water_backtrack_speed)
            controller.set_right_stick(direction * 0.12, 0.0)
            self._neutralized = False
            self.current_action = (
                f"jak: WATER backtrack; L/C/R={self.water_ratio_left:.2f}/"
                f"{self.water_ratio_center:.2f}/{self.water_ratio_right:.2f}"
            )
            return self.current_action

        if self.water_escape_stage == "backtrack":
            self.water_escape_stage = "turn"
            self.water_escape_stage_until = ctx.now + self.water_turn_seconds

        if self.water_escape_stage == "turn" and ctx.now < self.water_escape_stage_until:
            controller.set_left_stick(direction * self.water_escape_turn, self.water_escape_forward)
            controller.set_right_stick(direction * 0.34, 0.0)
            self._neutralized = False
            self.current_action = (
                f"jak: WATER turn toward drier side {'R' if direction > 0 else 'L'}; "
                f"water={self.water_ratio_total:.2f}"
            )
            return self.current_action

        # Reassess continuously after the first backtrack/turn pair. If still wet,
        # keep a broad shoreline-seeking arc rather than resuming random exploration.
        controller.set_left_stick(direction * 0.52, 0.48)
        controller.set_right_stick(direction * 0.20, 0.0)
        self._neutralized = False
        self.current_action = (
            f"jak: WATER shoreline seek {'R' if direction > 0 else 'L'}; "
            f"water={self.water_ratio_total:.2f}"
        )
        return self.current_action

    def _on_foot(self, controller: Controller, ctx: ProfileContext) -> str:
        # Water is checked every tick, not just when a movement burst expires. This
        # allows the guardrail to interrupt a burst before it commits to the ocean.
        self._refresh_water_state(ctx)
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
            self.route_bias + self.production_random.uniform(-0.08, 0.08), -0.36, 0.36
        )
        forward = self.production_forward * self.safe_forward_scale
        camera = self._clamp(heading * 0.28, -self.production_camera, self.production_camera)
        controller.set_left_stick(heading, forward)
        controller.set_right_stick(camera, 0.0)
        self._neutralized = False

        suffix = ""
        if self.production_random.random() < self.production_jump_probability * self.safe_jump_scale:
            suffix = self._schedule_jump(controller, ctx)

        duration = self.production_random.uniform(self.production_burst_min, self.production_burst_max)
        self.next_production_action_at = ctx.now + duration
        self.production_bursts += 1
        self.current_action = (
            f"jak: safe on-foot arc x={heading:+.2f} fwd={forward:.2f} "
            f"water={self.water_ratio_total:.2f}{suffix}"
        )
        return self.current_action

    def _gameplay_blocker(self) -> str:
        if self.title_gate_visible:
            return "title"
        if self.main_menu_visible:
            return "main-menu"
        if self.save_prompt_visible:
            return "save-prompt"
        if self.save_file_selector_visible:
            return "save-file-selector"
        compact = self._compact_text(self.last_ocr_text)
        for marker in self.GAMEPLAY_BLOCKERS:
            if marker in compact:
                return marker.lower()
        return ""

    def _reset_attach_probe(self, *, clear_confirmations: bool = False) -> None:
        self.attach_probe_stage = "idle"
        self.attach_probe_idle_since = None
        self.attach_probe_baseline_motion = 0.0
        self.attach_probe_release_at = 0.0
        self.attach_probe_deadline = 0.0
        self.attach_probe_peak_motion = 0.0
        if clear_confirmations:
            self.attach_probe_confirmations = 0

    def _service_attach_probe(self, controller: Controller, ctx: ProfileContext) -> str | None:
        # This path exists only for a restarted AutoPilot process. A normal full boot
        # has campaign_launch_at and uses the existing opening safety transaction.
        if self.campaign_launch_at is not None or self.phase == JakPhase.GAMEPLAY:
            return None
        if self.runtime_started_at is None:
            return None
        if ctx.now - self.runtime_started_at < self.attach_probe_after_seconds:
            return None

        blocker = self._gameplay_blocker()
        if blocker:
            self._reset_attach_probe(clear_confirmations=True)
            return None

        if self.attach_probe_stage == "drive":
            self.attach_probe_peak_motion = max(self.attach_probe_peak_motion, float(ctx.motion))
            if ctx.now < self.attach_probe_release_at:
                controller.set_right_stick(self.attach_probe_direction * self.attach_probe_camera_x, 0.0)
                self._neutralized = False
                self.current_action = "jak: mid-session gameplay probe; nudge camera"
                return self.current_action
            controller.set_right_stick(0.0, 0.0)
            self.attach_probe_stage = "observe"
            self.attach_probe_deadline = ctx.now + self.attach_probe_observe_seconds
            self.current_action = "jak: mid-session gameplay probe; observe response"
            return self.current_action

        if self.attach_probe_stage == "observe":
            self.attach_probe_peak_motion = max(self.attach_probe_peak_motion, float(ctx.motion))
            threshold = max(
                self.attach_probe_response_min,
                self.attach_probe_baseline_motion * 1.6 + 0.004,
            )
            if self.attach_probe_peak_motion >= threshold:
                self.attach_probe_confirmations += 1
                self.attach_probe_direction *= -1.0
                if self.attach_probe_confirmations >= self.attach_probe_confirmations_required:
                    controller.neutral_sticks()
                    self._set_phase(JakPhase.GAMEPLAY)
                    self.last_gameplay_at = ctx.now
                    self.gameplay_assumed_after_opening = True
                    self.attach_probe_successes += 1
                    self.current_action = "jak: mid-session camera response confirmed -> GAMEPLAY"
                    return self._production_gameplay(controller, ctx)
                self._reset_attach_probe(clear_confirmations=False)
                self.attach_probe_next_at = ctx.now + self.attach_probe_retry_seconds
                self.current_action = (
                    f"jak: gameplay probe confirmation {self.attach_probe_confirmations}/"
                    f"{self.attach_probe_confirmations_required}"
                )
                return self.current_action
            if ctx.now < self.attach_probe_deadline:
                self.current_action = "jak: mid-session gameplay probe; waiting for camera motion"
                return self.current_action
            self._reset_attach_probe(clear_confirmations=True)
            self.attach_probe_next_at = ctx.now + self.attach_probe_retry_seconds
            return None

        if ctx.now < self.attach_probe_next_at:
            return None
        if float(ctx.motion) > self.attach_probe_baseline_max:
            self.attach_probe_idle_since = None
            return None
        if self.attach_probe_idle_since is None:
            self.attach_probe_idle_since = ctx.now
            self.attach_probe_baseline_motion = float(ctx.motion)
            return None
        self.attach_probe_baseline_motion = max(
            self.attach_probe_baseline_motion, float(ctx.motion)
        )
        if ctx.now - self.attach_probe_idle_since < self.attach_probe_idle_seconds:
            return None

        self.attach_probe_attempts += 1
        self.attach_probe_stage = "drive"
        self.attach_probe_peak_motion = 0.0
        self.attach_probe_release_at = ctx.now + self.attach_probe_drive_seconds
        controller.set_right_stick(self.attach_probe_direction * self.attach_probe_camera_x, 0.0)
        self._neutralized = False
        self.current_action = "jak: mid-session gameplay probe; nudge camera"
        return self.current_action

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        if self.runtime_started_at is None:
            self.runtime_started_at = ctx.now

        action = super().tick(controller, ctx)
        if self.mode != "production" or self.phase == JakPhase.GAMEPLAY:
            return action
        probe_action = self._service_attach_probe(controller, ctx)
        return probe_action if probe_action is not None else action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "jak_water_ratio_total": round(self.water_ratio_total, 4),
                "jak_water_ratio_left": round(self.water_ratio_left, 4),
                "jak_water_ratio_center": round(self.water_ratio_center, 4),
                "jak_water_ratio_right": round(self.water_ratio_right, 4),
                "jak_water_escape_active": self.water_escape_active,
                "jak_water_escape_stage": self.water_escape_stage,
                "jak_water_avoidance_events": self.water_avoidance_events,
                "jak_water_escape_ticks": self.water_escape_ticks,
                "jak_attach_probe_stage": self.attach_probe_stage,
                "jak_attach_probe_attempts": self.attach_probe_attempts,
                "jak_attach_probe_confirmations": self.attach_probe_confirmations,
                "jak_attach_probe_successes": self.attach_probe_successes,
                "jak_attach_probe_peak_motion": round(self.attach_probe_peak_motion, 4),
            }
        )
        return state
