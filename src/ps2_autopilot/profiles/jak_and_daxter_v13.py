from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .jak_and_daxter_v9 import WaterGeometry
from .jak_and_daxter_v10 import GameplayCue
from .jak_and_daxter_v12 import JakAndDaxterV12Profile


class JakAndDaxterV13Profile(JakAndDaxterV12Profile):
    """Keep Geyser Rock land traversal in charge unless water is unmistakable.

    The first V12 live run proved the progress-aware swimmer can actually get Jak out
    of the ocean. It also exposed a more important ownership bug: ordinary dark-blue
    Geyser Rock walls can satisfy the broad V7 HSV mask *and* V9's connected geometry
    checks. Once that happens, V12 quite correctly spends its time executing water
    backtracks, shore hops and U-turns on perfectly dry ground.

    V13 makes water entry deliberately asymmetric: prevention/escape may trigger a
    little late, after Jak is visibly in water, but dry terrain must not be handed to
    the swim controller just because a cliff is blue-gray. It also replaces random
    anti-loop turns with a short camera scan that scores the left/right corridor and
    commits toward the more open, less-water-like view. Finally, Scout Fly targeting
    gains temporal confirmation and a post-dive cooldown so one red/gray false target
    cannot monopolize gameplay with repeated dive attacks.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        # Strict *ownership* signature. The older broad HSV mask is still useful for
        # shoreline ratios/direction, but it is no longer enough to activate swimming.
        self.water_surface_hue_min = int(cfg.get("water_surface_hue_min", 88))
        self.water_surface_hue_max = int(cfg.get("water_surface_hue_max", 118))
        self.water_surface_sat_min = int(cfg.get("water_surface_sat_min", 80))
        self.water_surface_value_min = int(cfg.get("water_surface_value_min", 70))
        self.water_surface_bottom_fraction = max(
            0.10, min(0.50, float(cfg.get("water_surface_bottom_fraction", 0.28)))
        )
        self.water_surface_bottom_min = max(
            0.03, min(0.80, float(cfg.get("water_surface_bottom_min", 0.16)))
        )
        self.water_surface_candidate_min = max(
            0.01, min(0.60, float(cfg.get("water_surface_candidate_min", 0.055)))
        )
        self.water_surface_bottom_ratio = 0.0
        self.water_surface_rejections = 0
        self.water_surface_confirmations = 0

        # Cue hysteresis. V10 intentionally started permissive so we could see what
        # the detector found in a live level; the V12 run showed repeated "Scout Fly"
        # attacks can otherwise become their own loop.
        self.scout_stable_frames_required = max(
            1, min(12, int(cfg.get("scout_stable_frames_required", 3)))
        )
        self.scout_retry_cooldown_seconds = max(
            1.0, float(cfg.get("scout_retry_cooldown_seconds", 6.0))
        )
        self.scout_stability_x = max(
            0.02, min(0.50, float(cfg.get("scout_stability_x", 0.18)))
        )
        self.scout_stability_area_fraction = max(
            0.05, min(1.0, float(cfg.get("scout_stability_area_fraction", 0.55)))
        )
        self.scout_stable_frames = 0
        self.scout_last_x = 0.0
        self.scout_last_area = 0.0
        self.scout_retry_cooldown_until = 0.0
        self.scout_cooldown_suppressions = 0
        self.scout_unstable_suppressions = 0

        self.blue_eco_stable_frames_required = max(
            1, min(8, int(cfg.get("blue_eco_stable_frames_required", 2)))
        )
        self.blue_eco_stable_frames = 0
        self.blue_eco_last_x = 0.0
        self.blue_eco_unstable_suppressions = 0

        # Deliberate local route selection. This is only entered from the existing
        # visual loop/stagnation detector; ordinary traversal stays continuous.
        self.land_scan_camera = max(
            0.15, min(0.70, float(cfg.get("land_scan_camera", 0.42)))
        )
        self.land_scan_sweep_seconds = max(
            0.15, float(cfg.get("land_scan_sweep_seconds", 0.42))
        )
        self.land_scan_commit_seconds = max(
            0.25, float(cfg.get("land_scan_commit_seconds", 0.85))
        )
        self.land_scan_turn = max(
            0.35, min(1.0, float(cfg.get("land_scan_turn", 0.82)))
        )
        self.land_scan_forward = max(
            0.05, min(0.70, float(cfg.get("land_scan_forward", 0.34)))
        )
        self.land_scan_cooldown_seconds = max(
            1.0, float(cfg.get("land_scan_cooldown_seconds", 4.5))
        )
        self.land_scan_water_penalty = max(
            0.2, min(3.0, float(cfg.get("land_scan_water_penalty", 1.25)))
        )

        self.land_scan_active = False
        self.land_scan_stage = "idle"
        self.land_scan_until = 0.0
        self.land_scan_left_score = 0.0
        self.land_scan_right_score = 0.0
        self.land_scan_choice = 1.0
        self.land_scan_reason = ""
        self.next_land_scan_at = 0.0
        self.land_scans = 0
        self.land_scan_left_choices = 0
        self.land_scan_right_choices = 0

    def _water_candidate_mask(self, frame: np.ndarray) -> np.ndarray:
        """Return only bright/saturated cyan-blue likely to be actual water.

        V7's mask deliberately admitted dark blue scenery (sat>=45, value>=25). That
        was useful as a warning cue but is too broad for controller ownership. V13's
        geometry gate uses this stricter surface signature instead. The broad V7 ratio
        calculation remains untouched for choosing the drier direction after entry.
        """
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
        return (
            (hsv[:, :, 0] >= self.water_surface_hue_min)
            & (hsv[:, :, 0] <= self.water_surface_hue_max)
            & (hsv[:, :, 1] >= self.water_surface_sat_min)
            & (hsv[:, :, 2] >= self.water_surface_value_min)
        ).astype(np.uint8)

    def _water_geometry(self, frame: np.ndarray) -> WaterGeometry:
        geometry = super()._water_geometry(frame)
        mask = self._water_candidate_mask(frame)
        if mask.size == 0:
            self.water_surface_bottom_ratio = 0.0
            return geometry

        rows = max(1, int(round(mask.shape[0] * self.water_surface_bottom_fraction)))
        bottom = mask[-rows:, :]
        self.water_surface_bottom_ratio = float(np.mean(bottom > 0)) if bottom.size else 0.0
        surface_confirmed = bool(
            geometry.confirmed
            and geometry.candidate_ratio >= self.water_surface_candidate_min
            and self.water_surface_bottom_ratio >= self.water_surface_bottom_min
        )
        if surface_confirmed:
            self.water_surface_confirmations += 1
        elif geometry.confirmed:
            self.water_surface_rejections += 1

        return WaterGeometry(
            candidate_ratio=geometry.candidate_ratio,
            largest_component_ratio=geometry.largest_component_ratio,
            largest_width_ratio=geometry.largest_width_ratio,
            largest_height_ratio=geometry.largest_height_ratio,
            coherence=geometry.coherence,
            max_row_coverage=geometry.max_row_coverage,
            row_band_fraction=geometry.row_band_fraction,
            confirmed=surface_confirmed,
        )

    def _refresh_gameplay_cue(self, ctx: ProfileContext) -> None:
        super()._refresh_gameplay_cue(ctx)
        cue = self.gameplay_cue

        if cue.kind == "scout_box":
            if ctx.now < self.scout_retry_cooldown_until:
                self.scout_cooldown_suppressions += 1
                self.scout_stable_frames = 0
                self.gameplay_cue = GameplayCue()
                return

            area_tol = max(
                0.0008,
                self.scout_last_area * self.scout_stability_area_fraction,
            )
            stable = bool(
                self.scout_stable_frames > 0
                and abs(cue.x - self.scout_last_x) <= self.scout_stability_x
                and abs(cue.area - self.scout_last_area) <= area_tol
            )
            self.scout_stable_frames = self.scout_stable_frames + 1 if stable else 1
            self.scout_last_x = cue.x
            self.scout_last_area = cue.area
            if self.scout_stable_frames < self.scout_stable_frames_required:
                self.scout_unstable_suppressions += 1
                self.gameplay_cue = GameplayCue()
            return

        self.scout_stable_frames = 0

        if cue.kind == "blue_eco":
            stable = bool(
                self.blue_eco_stable_frames > 0
                and abs(cue.x - self.blue_eco_last_x) <= 0.22
            )
            self.blue_eco_stable_frames = self.blue_eco_stable_frames + 1 if stable else 1
            self.blue_eco_last_x = cue.x
            # A bright cyan object that also owns a meaningful part of the bottom
            # playfield is much more likely to be water than a collectible Eco vent.
            if (
                self.blue_eco_stable_frames < self.blue_eco_stable_frames_required
                or self.water_surface_bottom_ratio >= self.water_surface_bottom_min * 0.60
            ):
                self.blue_eco_unstable_suppressions += 1
                self.gameplay_cue = GameplayCue()
            return

        self.blue_eco_stable_frames = 0

    def _finish_skill(self, ctx: ProfileContext) -> None:
        was_scout = self.skill_name == "scout_dive"
        super()._finish_skill(ctx)
        if was_scout:
            self.scout_retry_cooldown_until = ctx.now + self.scout_retry_cooldown_seconds
            self.scout_stable_frames = 0
            self.gameplay_cue = GameplayCue()

    def _land_openness_score(self, frame: np.ndarray) -> float:
        if frame is None or frame.size == 0:
            return -1.0
        h, w = frame.shape[:2]
        roi = frame[
            int(round(0.28 * h)):int(round(0.88 * h)),
            int(round(0.22 * w)):int(round(0.78 * w)),
        ]
        if roi.size == 0:
            return -1.0

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        sat = hsv[:, :, 1]
        val = hsv[:, :, 2]
        mean_value = float(np.mean(val)) / 255.0
        bright_fraction = float(np.mean(val >= 95))
        dark_fraction = float(np.mean(val <= 50))

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 55, 135)
        edge_density = float(np.mean(edges > 0))
        edge_open = 1.0 - min(1.0, edge_density * 4.0)

        water_like = float(
            np.mean(
                (hsv[:, :, 0] >= self.water_surface_hue_min)
                & (hsv[:, :, 0] <= self.water_surface_hue_max)
                & (sat >= self.water_surface_sat_min)
                & (val >= self.water_surface_value_min)
            )
        )

        return (
            0.52 * mean_value
            + 0.24 * bright_fraction
            + 0.20 * edge_open
            - 0.14 * dark_fraction
            - self.land_scan_water_penalty * water_like
        )

    def _cancel_land_scan(self) -> None:
        self.land_scan_active = False
        self.land_scan_stage = "idle"
        self.land_scan_until = 0.0

    def _start_land_scan(self, controller: Controller, ctx: ProfileContext, *, reason: str) -> str:
        self.land_scan_active = True
        self.land_scan_stage = "left"
        self.land_scan_until = ctx.now + self.land_scan_sweep_seconds
        self.land_scan_left_score = 0.0
        self.land_scan_right_score = 0.0
        self.land_scan_reason = reason
        self.land_scans += 1
        self.local_stuck_armed_at = None
        self.local_stuck_low_motion_since = None
        self.second_jump_pending = False
        controller.set_left_stick(0.0, 0.0)
        controller.set_right_stick(-self.land_scan_camera, 0.0)
        self._neutralized = False
        self.current_action = f"jak: V13 route scan left ({reason})"
        return self.current_action

    def _service_land_scan(self, controller: Controller, ctx: ProfileContext) -> str:
        if self.land_scan_stage == "left":
            if ctx.now < self.land_scan_until:
                controller.set_left_stick(0.0, 0.0)
                controller.set_right_stick(-self.land_scan_camera, 0.0)
                self._neutralized = False
                self.current_action = "jak: V13 route scan left"
                return self.current_action
            self.land_scan_left_score = self._land_openness_score(ctx.frame)
            self.land_scan_stage = "right"
            # Twice the sweep duration carries the camera from the left sample through
            # center to a comparably offset right sample.
            self.land_scan_until = ctx.now + self.land_scan_sweep_seconds * 2.0

        if self.land_scan_stage == "right":
            if ctx.now < self.land_scan_until:
                controller.set_left_stick(0.0, 0.0)
                controller.set_right_stick(self.land_scan_camera, 0.0)
                self._neutralized = False
                self.current_action = (
                    f"jak: V13 route scan right; left={self.land_scan_left_score:+.2f}"
                )
                return self.current_action
            self.land_scan_right_score = self._land_openness_score(ctx.frame)
            self.land_scan_choice = -1.0 if self.land_scan_left_score >= self.land_scan_right_score else 1.0
            if self.land_scan_choice < 0:
                self.land_scan_left_choices += 1
            else:
                self.land_scan_right_choices += 1
            self.land_scan_stage = "commit"
            self.land_scan_until = ctx.now + self.land_scan_commit_seconds

        if self.land_scan_stage == "commit":
            if ctx.now < self.land_scan_until:
                d = self.land_scan_choice
                controller.set_left_stick(d * self.land_scan_turn, self.land_scan_forward)
                controller.set_right_stick(-d * 0.18, 0.0)
                self._neutralized = False
                self.current_action = (
                    f"jak: V13 choose {'L' if d < 0 else 'R'} corridor; "
                    f"scores={self.land_scan_left_score:+.2f}/{self.land_scan_right_score:+.2f}"
                )
                return self.current_action

            d = self.land_scan_choice
            self.route_bias = d * 0.24
            self.next_route_bias_at = ctx.now + 5.5
            self.next_land_scan_at = ctx.now + self.land_scan_cooldown_seconds
            self.next_production_action_at = ctx.now + 0.05
            self._cancel_land_scan()
            controller.set_left_stick(d * 0.22, self.production_forward * self.safe_forward_scale)
            controller.set_right_stick(0.0, 0.0)
            self._neutralized = False
            self.current_action = f"jak: V13 corridor committed {'L' if d < 0 else 'R'}"
            return self.current_action

        self._cancel_land_scan()
        return super()._anti_loop(controller, ctx)

    def _anti_loop(self, controller: Controller, ctx: ProfileContext) -> str:
        if ctx.now < self.next_land_scan_at:
            return super()._anti_loop(controller, ctx)
        return self._start_land_scan(controller, ctx, reason="loop/stagnation")

    def _on_foot(self, controller: Controller, ctx: ProfileContext) -> str:
        if not self.land_scan_active:
            return super()._on_foot(controller, ctx)

        # Safety layers remain above route scanning. If water or a proven local
        # collision takes ownership, abandon the camera scan immediately.
        self._refresh_water_state(ctx)
        self._refresh_local_stuck(ctx)
        if self.water_escape_active:
            self._cancel_land_scan()
            return self._water_escape(controller, ctx)
        if self.local_stuck_active:
            self._cancel_land_scan()
            return self._local_stuck_escape(controller, ctx)
        if self.skill_active:
            self._cancel_land_scan()
            return super()._on_foot(controller, ctx)
        return self._service_land_scan(controller, ctx)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "jak_policy_version": "v13",
                "jak_water_surface_bottom_ratio": round(self.water_surface_bottom_ratio, 4),
                "jak_water_surface_confirmations": self.water_surface_confirmations,
                "jak_water_surface_rejections": self.water_surface_rejections,
                "jak_scout_stable_frames": self.scout_stable_frames,
                "jak_scout_cooldown_active": ctx.now < self.scout_retry_cooldown_until,
                "jak_scout_cooldown_suppressions": self.scout_cooldown_suppressions,
                "jak_scout_unstable_suppressions": self.scout_unstable_suppressions,
                "jak_blue_eco_stable_frames": self.blue_eco_stable_frames,
                "jak_blue_eco_unstable_suppressions": self.blue_eco_unstable_suppressions,
                "jak_land_scan_active": self.land_scan_active,
                "jak_land_scan_stage": self.land_scan_stage,
                "jak_land_scan_reason": self.land_scan_reason,
                "jak_land_scan_left_score": round(self.land_scan_left_score, 4),
                "jak_land_scan_right_score": round(self.land_scan_right_score, 4),
                "jak_land_scans": self.land_scans,
                "jak_land_scan_left_choices": self.land_scan_left_choices,
                "jak_land_scan_right_choices": self.land_scan_right_choices,
            }
        )
        return state
