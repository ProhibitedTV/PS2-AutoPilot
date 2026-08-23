from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .jak_and_daxter_v9 import JakAndDaxterV9Profile


@dataclass(frozen=True)
class GameplayCue:
    kind: str = "none"
    x: float = 0.0
    y: float = 0.0
    area: float = 0.0
    confidence: float = 0.0


class JakAndDaxterV10Profile(JakAndDaxterV9Profile):
    """Make autonomous Jak gameplay look deliberate rather than merely survivable.

    V9 closes the navigation loop around water and local collisions. V10 layers a
    small game-specific action vocabulary on top of that safe base:

    * visually interesting red/gray Scout Fly boxes are approached and dive-attacked;
    * bright blue Eco energy is treated as a beneficial navigation cue when the water
      geometry gate says the scene is not an ocean/shoreline;
    * normal exploration uses occasional moving spin attacks to clear wooden crates
      and nearby enemies without stopping traversal;
    * stable, dry, mostly-straight traversal can use a real roll-jump instead of only
      jogging and generic double-jumps.

    Every new behavior is subordinate to V9 water ownership and obstacle recovery.
    The result should be a more recognizable Jak player on stream while retaining the
    conservative menu/cutscene ownership rules from V8/V9.
    """

    CUE_ROI = (0.08, 0.92, 0.24, 0.93)

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        self.scout_red_sat_min = int(cfg.get("scout_red_sat_min", 90))
        self.scout_red_value_min = int(cfg.get("scout_red_value_min", 55))
        self.scout_gray_value_min = int(cfg.get("scout_gray_value_min", 85))
        self.scout_gray_sat_max = int(cfg.get("scout_gray_sat_max", 105))
        self.scout_min_area = max(0.0003, float(cfg.get("scout_min_area", 0.0010)))
        self.scout_max_area = min(0.12, float(cfg.get("scout_max_area", 0.045)))
        self.scout_gray_neighbor_min = max(
            0.02, min(0.80, float(cfg.get("scout_gray_neighbor_min", 0.10)))
        )
        self.scout_cue_min_confidence = max(
            0.1, min(1.0, float(cfg.get("scout_cue_min_confidence", 0.42)))
        )

        self.blue_eco_hue_min = int(cfg.get("blue_eco_hue_min", 88))
        self.blue_eco_hue_max = int(cfg.get("blue_eco_hue_max", 118))
        self.blue_eco_sat_min = int(cfg.get("blue_eco_sat_min", 95))
        self.blue_eco_value_min = int(cfg.get("blue_eco_value_min", 150))
        self.blue_eco_white_value_min = int(cfg.get("blue_eco_white_value_min", 205))
        self.blue_eco_white_sat_max = int(cfg.get("blue_eco_white_sat_max", 80))
        self.blue_eco_min_area = max(0.0002, float(cfg.get("blue_eco_min_area", 0.0008)))
        self.blue_eco_max_area = min(0.20, float(cfg.get("blue_eco_max_area", 0.075)))
        self.blue_eco_white_neighbor_min = max(
            0.002, min(0.50, float(cfg.get("blue_eco_white_neighbor_min", 0.010)))
        )
        self.blue_eco_cue_min_confidence = max(
            0.1, min(1.0, float(cfg.get("blue_eco_cue_min_confidence", 0.38)))
        )

        self.cue_refresh_seconds = max(0.05, float(cfg.get("cue_refresh_seconds", 0.16)))
        self.next_cue_refresh_at = 0.0
        self.gameplay_cue = GameplayCue()
        self.cue_frames = 0
        self.scout_cues = 0
        self.blue_eco_cues = 0

        self.scout_approach_seconds = max(
            0.2, float(cfg.get("scout_approach_seconds", 0.55))
        )
        self.scout_jump_lead_seconds = max(
            0.08, float(cfg.get("scout_jump_lead_seconds", 0.18))
        )
        self.scout_follow_seconds = max(
            0.15, float(cfg.get("scout_follow_seconds", 0.34))
        )
        self.blue_eco_seek_seconds = max(
            0.2, float(cfg.get("blue_eco_seek_seconds", 0.72))
        )
        self.blue_eco_seek_forward = max(
            0.20, min(0.85, float(cfg.get("blue_eco_seek_forward", 0.54)))
        )
        self.cue_turn_gain = max(0.2, min(1.8, float(cfg.get("cue_turn_gain", 0.90))))

        self.spin_attack_min_seconds = max(
            0.5, float(cfg.get("spin_attack_min_seconds", 1.6))
        )
        self.spin_attack_max_seconds = max(
            self.spin_attack_min_seconds,
            float(cfg.get("spin_attack_max_seconds", 3.2)),
        )
        self.next_spin_attack_at = 0.0

        self.roll_jump_min_seconds = max(
            1.0, float(cfg.get("roll_jump_min_seconds", 5.0))
        )
        self.roll_jump_max_seconds = max(
            self.roll_jump_min_seconds,
            float(cfg.get("roll_jump_max_seconds", 9.0)),
        )
        self.roll_jump_motion_min = max(
            0.001, float(cfg.get("roll_jump_motion_min", 0.012))
        )
        self.roll_jump_water_max = max(
            0.0, min(0.30, float(cfg.get("roll_jump_water_max", 0.055)))
        )
        self.roll_jump_heading_max = max(
            0.05, min(0.45, float(cfg.get("roll_jump_heading_max", 0.20)))
        )
        self.roll_jump_roll_seconds = max(
            0.06, float(cfg.get("roll_jump_roll_seconds", 0.14))
        )
        self.roll_jump_air_seconds = max(
            0.18, float(cfg.get("roll_jump_air_seconds", 0.48))
        )
        self.next_roll_jump_at = 0.0

        self.skill_active = False
        self.skill_name = "none"
        self.skill_stage = "none"
        self.skill_until = 0.0
        self.skill_heading = 0.0
        self.skill_button_sent = False
        self.skill_cue_x = 0.0

        self.scout_dive_attempts = 0
        self.blue_eco_seek_attempts = 0
        self.roll_jump_attempts = 0
        self.moving_spin_attacks = 0
        self.skills_completed = 0

    @staticmethod
    def _component_candidates(mask: np.ndarray):
        count, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
        for idx in range(1, count):
            x, y, w, h, area = stats[idx]
            yield x, y, w, h, int(area), centroids[idx]

    def _cue_crop(self, frame: np.ndarray) -> tuple[np.ndarray, int, int, int, int]:
        if frame is None or frame.size == 0:
            return np.zeros((0, 0, 3), dtype=np.uint8), 0, 0, 0, 0
        h, w = frame.shape[:2]
        x0, x1, y0, y1 = self.CUE_ROI
        xa = max(0, int(round(x0 * w)))
        xb = min(w, int(round(x1 * w)))
        ya = max(0, int(round(y0 * h)))
        yb = min(h, int(round(y1 * h)))
        return frame[ya:yb, xa:xb], xa, xb, ya, yb

    def _detect_scout_box(self, frame: np.ndarray) -> GameplayCue:
        roi, xa, xb, ya, yb = self._cue_crop(frame)
        if roi.size == 0:
            return GameplayCue()
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        red = (
            ((hue <= 12) | (hue >= 168))
            & (sat >= self.scout_red_sat_min)
            & (val >= self.scout_red_value_min)
        ).astype(np.uint8)
        gray = (
            (sat <= self.scout_gray_sat_max)
            & (val >= self.scout_gray_value_min)
        ).astype(np.uint8)
        red = cv2.morphologyEx(
            red, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        )

        frame_area = float(max(frame.shape[0] * frame.shape[1], 1))
        best = GameplayCue()
        rh, rw = roi.shape[:2]
        for x, y, w, h, area, center in self._component_candidates(red):
            area_ratio = area / frame_area
            if area_ratio < self.scout_min_area or area_ratio > self.scout_max_area:
                continue
            aspect = float(w) / float(max(h, 1))
            if aspect < 0.45 or aspect > 2.4:
                continue

            pad_x = max(3, int(round(w * 0.65)))
            pad_y = max(3, int(round(h * 0.65)))
            x0 = max(0, x - pad_x)
            x1 = min(rw, x + w + pad_x)
            y0 = max(0, y - pad_y)
            y1 = min(rh, y + h + pad_y)
            gray_ratio = float(np.mean(gray[y0:y1, x0:x1] > 0)) if x1 > x0 and y1 > y0 else 0.0
            if gray_ratio < self.scout_gray_neighbor_min:
                continue

            cx = (xa + float(center[0])) / float(max(frame.shape[1], 1))
            cy = (ya + float(center[1])) / float(max(frame.shape[0], 1))
            nx = self._clamp((cx - 0.5) / 0.5, -1.0, 1.0)
            closeness = self._clamp((cy - 0.30) / 0.62, 0.0, 1.0)
            size_score = self._clamp(area_ratio / max(self.scout_min_area * 4.0, 1e-6), 0.0, 1.0)
            center_score = 1.0 - min(1.0, abs(nx) * 0.65)
            confidence = self._clamp(
                0.28 * size_score
                + 0.28 * self._clamp(gray_ratio / 0.28, 0.0, 1.0)
                + 0.24 * closeness
                + 0.20 * center_score,
                0.0,
                1.0,
            )
            if confidence > best.confidence:
                best = GameplayCue("scout_box", nx, cy, area_ratio, confidence)
        return best

    def _detect_blue_eco(self, frame: np.ndarray) -> GameplayCue:
        roi, xa, xb, ya, yb = self._cue_crop(frame)
        if roi.size == 0:
            return GameplayCue()
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        blue = (
            (hue >= self.blue_eco_hue_min)
            & (hue <= self.blue_eco_hue_max)
            & (sat >= self.blue_eco_sat_min)
            & (val >= self.blue_eco_value_min)
        ).astype(np.uint8)
        white = (
            (sat <= self.blue_eco_white_sat_max)
            & (val >= self.blue_eco_white_value_min)
        ).astype(np.uint8)
        blue = cv2.morphologyEx(
            blue, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 7))
        )

        frame_area = float(max(frame.shape[0] * frame.shape[1], 1))
        best = GameplayCue()
        rh, rw = roi.shape[:2]
        for x, y, w, h, area, center in self._component_candidates(blue):
            area_ratio = area / frame_area
            if area_ratio < self.blue_eco_min_area or area_ratio > self.blue_eco_max_area:
                continue
            pad_x = max(4, int(round(w * 0.80)))
            pad_y = max(4, int(round(h * 0.55)))
            x0 = max(0, x - pad_x)
            x1 = min(rw, x + w + pad_x)
            y0 = max(0, y - pad_y)
            y1 = min(rh, y + h + pad_y)
            white_ratio = float(np.mean(white[y0:y1, x0:x1] > 0)) if x1 > x0 and y1 > y0 else 0.0
            if white_ratio < self.blue_eco_white_neighbor_min:
                continue

            cx = (xa + float(center[0])) / float(max(frame.shape[1], 1))
            cy = (ya + float(center[1])) / float(max(frame.shape[0], 1))
            nx = self._clamp((cx - 0.5) / 0.5, -1.0, 1.0)
            center_score = 1.0 - min(1.0, abs(nx) * 0.55)
            lower_score = self._clamp((cy - 0.20) / 0.62, 0.0, 1.0)
            verticality = self._clamp((float(h) / float(max(w, 1))) / 2.2, 0.0, 1.0)
            confidence = self._clamp(
                0.30 * self._clamp(white_ratio / 0.12, 0.0, 1.0)
                + 0.25 * center_score
                + 0.20 * lower_score
                + 0.15 * verticality
                + 0.10 * self._clamp(area_ratio / max(self.blue_eco_min_area * 4.0, 1e-6), 0.0, 1.0),
                0.0,
                1.0,
            )
            if confidence > best.confidence:
                best = GameplayCue("blue_eco", nx, cy, area_ratio, confidence)
        return best

    def _refresh_gameplay_cue(self, ctx: ProfileContext) -> None:
        if ctx.now < self.next_cue_refresh_at:
            return
        self.next_cue_refresh_at = ctx.now + self.cue_refresh_seconds
        self.cue_frames += 1

        scout = self._detect_scout_box(ctx.frame)
        if scout.confidence >= self.scout_cue_min_confidence:
            if self.gameplay_cue.kind != "scout_box":
                self.scout_cues += 1
            self.gameplay_cue = scout
            return

        eco = self._detect_blue_eco(ctx.frame)
        if eco.confidence >= self.blue_eco_cue_min_confidence:
            if self.gameplay_cue.kind != "blue_eco":
                self.blue_eco_cues += 1
            self.gameplay_cue = eco
            return

        self.gameplay_cue = GameplayCue()

    def _finish_skill(self, ctx: ProfileContext) -> None:
        self.skill_active = False
        self.skill_name = "none"
        self.skill_stage = "none"
        self.skill_until = 0.0
        self.skill_button_sent = False
        self.skills_completed += 1
        self.next_production_action_at = ctx.now

    def _start_scout_dive(self, ctx: ProfileContext) -> None:
        self.skill_active = True
        self.skill_name = "scout_dive"
        self.skill_stage = "approach"
        self.skill_until = ctx.now + self.scout_approach_seconds
        self.skill_cue_x = self.gameplay_cue.x
        self.skill_button_sent = False
        self.scout_dive_attempts += 1

    def _start_roll_jump(self, ctx: ProfileContext, heading: float) -> None:
        self.skill_active = True
        self.skill_name = "roll_jump"
        self.skill_stage = "roll"
        self.skill_until = ctx.now + self.roll_jump_roll_seconds
        self.skill_heading = heading
        self.skill_button_sent = False
        self.roll_jump_attempts += 1
        self.next_roll_jump_at = ctx.now + self.production_random.uniform(
            self.roll_jump_min_seconds, self.roll_jump_max_seconds
        )

    def _service_skill(self, controller: Controller, ctx: ProfileContext) -> str:
        if self.skill_name == "scout_dive":
            heading = self._clamp(self.skill_cue_x * self.cue_turn_gain, -0.52, 0.52)
            if self.skill_stage == "approach":
                controller.set_left_stick(heading, 0.48)
                controller.set_right_stick(-heading * 0.16, 0.0)
                self._arm_local_stuck(ctx)
                self._neutralized = False
                if ctx.now >= self.skill_until:
                    controller.tap("cross", 0.07)
                    self.skill_stage = "jump"
                    self.skill_until = ctx.now + self.scout_jump_lead_seconds
                    self.current_action = "jak: V10 Scout Fly box -> jump for dive"
                    return self.current_action
                self.current_action = "jak: V10 line up Scout Fly box"
                return self.current_action

            if self.skill_stage == "jump":
                controller.set_left_stick(heading * 0.55, 0.42)
                controller.set_right_stick(0.0, 0.0)
                self._neutralized = False
                if ctx.now >= self.skill_until:
                    controller.tap("square", 0.09)
                    self.skill_stage = "slam"
                    self.skill_until = ctx.now + self.scout_follow_seconds
                    self.current_action = "jak: V10 Scout Fly box -> dive attack"
                    return self.current_action
                self.current_action = "jak: V10 Scout Fly dive airborne"
                return self.current_action

            controller.set_left_stick(heading * 0.25, 0.28)
            controller.set_right_stick(0.0, 0.0)
            self._neutralized = False
            if ctx.now >= self.skill_until:
                self._finish_skill(ctx)
                self.current_action = "jak: V10 Scout Fly dive complete"
                return self.current_action
            self.current_action = "jak: V10 Scout Fly dive follow-through"
            return self.current_action

        if self.skill_name == "roll_jump":
            heading = self.skill_heading
            forward = self.production_forward * self.safe_forward_scale
            controller.set_left_stick(heading, forward)
            controller.set_right_stick(heading * 0.10, 0.0)
            self._arm_local_stuck(ctx)
            self._neutralized = False
            if self.skill_stage == "roll":
                if not self.skill_button_sent:
                    controller.tap("l1", max(0.10, self.roll_jump_roll_seconds))
                    self.skill_button_sent = True
                if ctx.now >= self.skill_until:
                    controller.tap("cross", 0.07)
                    self.skill_stage = "air"
                    self.skill_until = ctx.now + self.roll_jump_air_seconds
                    self.skill_button_sent = False
                    self.current_action = "jak: V10 roll-jump launch"
                    return self.current_action
                self.current_action = "jak: V10 roll into jump"
                return self.current_action

            if ctx.now >= self.skill_until:
                self._finish_skill(ctx)
                self.current_action = "jak: V10 roll-jump landed; resume route"
                return self.current_action
            self.current_action = "jak: V10 roll-jump traversal"
            return self.current_action

        self._finish_skill(ctx)
        self.current_action = "jak: V10 skill reset"
        return self.current_action

    def _seek_blue_eco(self, controller: Controller, ctx: ProfileContext) -> str:
        heading = self._clamp(self.gameplay_cue.x * self.cue_turn_gain, -0.50, 0.50)
        controller.set_left_stick(heading, self.blue_eco_seek_forward)
        controller.set_right_stick(-heading * 0.12, 0.0)
        self._arm_local_stuck(ctx)
        self._neutralized = False
        self.next_production_action_at = ctx.now + self.blue_eco_seek_seconds
        self.blue_eco_seek_attempts += 1
        self.current_action = (
            f"jak: V10 seek Blue Eco x={self.gameplay_cue.x:+.2f} "
            f"conf={self.gameplay_cue.confidence:.2f}"
        )
        return self.current_action

    def _can_roll_jump(self, ctx: ProfileContext, heading: float) -> bool:
        motion = max(
            float(ctx.motion),
            float(self.scene_metrics.center_motion),
            float(self.scene_metrics.lower_motion),
        )
        return bool(
            ctx.now >= self.next_roll_jump_at
            and abs(heading) <= self.roll_jump_heading_max
            and motion >= self.roll_jump_motion_min
            and self.water_ratio_total <= self.roll_jump_water_max
            and not self.water_geometry_confirmed
            and self.gameplay_cue.kind == "none"
            and not self.local_stuck_active
        )

    def _maybe_spin_attack(self, controller: Controller, ctx: ProfileContext) -> str:
        if ctx.now < self.next_spin_attack_at:
            return ""
        controller.tap("circle", 0.07)
        self.moving_spin_attacks += 1
        self.next_spin_attack_at = ctx.now + self.production_random.uniform(
            self.spin_attack_min_seconds, self.spin_attack_max_seconds
        )
        return " + spin-clear"

    def _on_foot(self, controller: Controller, ctx: ProfileContext) -> str:
        self._refresh_water_state(ctx)
        self._refresh_local_stuck(ctx)
        if self.local_stuck_active:
            return self._local_stuck_escape(controller, ctx)
        if self.water_escape_active:
            return self._water_escape(controller, ctx)

        self._refresh_gameplay_cue(ctx)
        if self.skill_active:
            return self._service_skill(controller, ctx)

        if self._service_jump_combo(controller, ctx):
            return self.current_action

        if self.gameplay_cue.kind == "scout_box":
            self._start_scout_dive(ctx)
            return self._service_skill(controller, ctx)
        if self.gameplay_cue.kind == "blue_eco" and ctx.now >= self.next_production_action_at:
            return self._seek_blue_eco(controller, ctx)

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

        if self._can_roll_jump(ctx, heading):
            self._start_roll_jump(ctx, heading)
            return self._service_skill(controller, ctx)

        forward = self.production_forward * self.safe_forward_scale
        camera = self._clamp(
            heading * 0.28, -self.production_camera, self.production_camera
        )
        controller.set_left_stick(heading, forward)
        controller.set_right_stick(camera, 0.0)
        self._arm_local_stuck(ctx)
        self._neutralized = False

        suffix = self._maybe_spin_attack(controller, ctx)
        if self.production_random.random() < self.production_jump_probability * self.safe_jump_scale:
            suffix += self._schedule_jump(controller, ctx)

        duration = self.production_random.uniform(
            self.production_burst_min, self.production_burst_max
        )
        self.next_production_action_at = ctx.now + duration
        self.production_bursts += 1
        self.current_action = (
            f"jak: V10 purposeful arc x={heading:+.2f} fwd={forward:.2f} "
            f"water={self.water_ratio_total:.2f}/geom={int(self.water_geometry_confirmed)}{suffix}"
        )
        return self.current_action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "jak_policy_version": "v10",
                "jak_gameplay_cue": self.gameplay_cue.kind,
                "jak_cue_x": round(self.gameplay_cue.x, 3),
                "jak_cue_y": round(self.gameplay_cue.y, 3),
                "jak_cue_area": round(self.gameplay_cue.area, 5),
                "jak_cue_confidence": round(self.gameplay_cue.confidence, 3),
                "jak_skill": self.skill_name,
                "jak_skill_stage": self.skill_stage,
                "jak_scout_cues": self.scout_cues,
                "jak_blue_eco_cues": self.blue_eco_cues,
                "jak_scout_dive_attempts": self.scout_dive_attempts,
                "jak_blue_eco_seek_attempts": self.blue_eco_seek_attempts,
                "jak_roll_jump_attempts": self.roll_jump_attempts,
                "jak_moving_spin_attacks": self.moving_spin_attacks,
                "jak_skills_completed": self.skills_completed,
            }
        )
        return state
