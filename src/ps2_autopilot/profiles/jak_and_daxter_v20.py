from __future__ import annotations

import math

import cv2
import numpy as np

from .base import ProfileContext
from .jak_and_daxter_v16 import ShorelineRisk
from .jak_and_daxter_v19 import JakAndDaxterV19Profile


class JakAndDaxterV20Profile(JakAndDaxterV19Profile):
    """Use runtime pressure, semantic displacement, and conservative water ownership.

    V19 reduced obvious duplicate CV, while the open performance tickets require a
    cross-game feedback loop: recent p50/p95/overrun health must be visible to profiles
    so expensive semantic perception can yield CPU to movement when the runtime is
    missing its control budget.

    V20 dynamically stretches collectible/cue/ledge refresh cadence under sustained
    pressure and freezes goal/ledge refresh while an atomic locomotion skill or
    relocation macro owns control. It also consumes calibrated read-only Jak XYZ fields
    when available to expose real displacement evidence.

    Live PCSX2 2.6.3 soaks later exposed a water-perception edge case that belongs at
    this final policy layer: blue sky / lighting can dominate the lower frame, while a
    single blue side of an otherwise dry path can satisfy the old shoreline total
    threshold. V20 therefore vetoes only *new* water ownership when both the upper and
    lower view are unusually uniform, nearly textureless versions of the same strict
    blue surface signature, and makes preventative shoreline steering center-priority.
    Existing swim recovery remains sticky once it has already been confirmed.
    """

    SKY_GUARD_ROI = (0.05, 0.95, 0.10, 0.48)

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.v20_goal_refresh_base = max(
            self.goal_refresh_seconds, float(cfg.get("v20_goal_refresh_base", 0.45))
        )
        self.v20_cue_refresh_base = max(
            self.cue_refresh_seconds, float(cfg.get("v20_cue_refresh_base", 0.40))
        )
        self.v20_ledge_refresh_base = max(
            self.ledge_refresh_seconds, float(cfg.get("v20_ledge_refresh_base", 0.32))
        )
        self.v20_pressure_factor = 1.0
        self.v20_pressure = "warming"
        self.v20_load_shed_ticks = 0
        self.v20_reflex_perception_skips = 0
        self.v20_menu_preemptions = 0

        self.semantic_last_position: tuple[float, float, float] | None = None
        self.semantic_translation_delta = 0.0
        self.semantic_translation_total = 0.0
        self.semantic_position_samples = 0
        self.semantic_position_age = 0.0
        self.semantic_position_last_at = 0.0

        # Late live-soak water hardening. These thresholds are deliberately strict:
        # suppress only a broad, nearly featureless blue view that spans both sky and
        # foreground. Textured/rippled water, a visible horizon, and already-active
        # swim recovery continue through the inherited V13 geometry gate.
        self.v20_sky_blue_min = max(
            0.35, min(0.95, float(cfg.get("v20_sky_blue_min", 0.62)))
        )
        self.v20_sky_edge_max = max(
            0.002, min(0.10, float(cfg.get("v20_sky_edge_max", 0.025)))
        )
        self.v20_sky_value_delta_max = max(
            4.0, min(80.0, float(cfg.get("v20_sky_value_delta_max", 28.0)))
        )
        self.v20_shoreline_broad_total = max(
            self.shoreline_guard_total,
            min(0.80, float(cfg.get("v20_shoreline_broad_total", 0.20))),
        )
        self.v20_shoreline_bilateral_min = max(
            0.01, min(0.40, float(cfg.get("v20_shoreline_bilateral_min", 0.05)))
        )
        self.v20_sky_blue_upper_ratio = 0.0
        self.v20_sky_blue_lower_ratio = 0.0
        self.v20_sky_upper_edge = 0.0
        self.v20_sky_lower_edge = 0.0
        self.v20_sky_water_suppressions = 0
        self.v20_shoreline_side_only_suppressions = 0

    @staticmethod
    def _pressure_factor(pressure: str) -> float:
        return {
            "healthy": 1.0,
            "warming": 1.0,
            "elevated": 1.25,
            "high": 1.65,
            "critical": 2.25,
        }.get(str(pressure), 1.0)

    @staticmethod
    def _normalized_crop(
        frame: np.ndarray, bounds: tuple[float, float, float, float]
    ) -> np.ndarray:
        if frame is None or frame.size == 0:
            return np.zeros((0, 0, 3), dtype=np.uint8)
        h, w = frame.shape[:2]
        x0, x1, y0, y1 = bounds
        return frame[
            max(0, int(round(y0 * h))):min(h, int(round(y1 * h))),
            max(0, int(round(x0 * w))):min(w, int(round(x1 * w))),
        ]

    def _strict_blue_metrics(self, roi: np.ndarray) -> tuple[float, float, float]:
        if roi is None or roi.size == 0:
            return 0.0, 0.0, 0.0
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        blue = (
            (hue >= self.water_surface_hue_min)
            & (hue <= self.water_surface_hue_max)
            & (sat >= self.water_surface_sat_min)
            & (val >= self.water_surface_value_min)
        )
        blue_ratio = float(np.mean(blue)) if blue.size else 0.0
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 55, 135)
        edge_ratio = float(np.mean(edges > 0)) if edges.size else 0.0
        mean_value = float(np.mean(val)) if val.size else 0.0
        return blue_ratio, edge_ratio, mean_value

    def _water_candidate_mask(self, frame: np.ndarray) -> np.ndarray:
        """Reject a uniform full-view blue field before it can own land navigation.

        This does not try to classify all sky. It only handles the live failure mode
        where the same strict blue signature fills both an upper reference band and
        the lower water ROI with very little edge structure. Once swim recovery is
        already active, never apply this veto: the escape controller must remain able
        to recover from genuinely immersive/underwater views.
        """
        mask = super()._water_candidate_mask(frame)
        if mask.size == 0:
            return mask

        upper = self._normalized_crop(frame, self.SKY_GUARD_ROI)
        lower = self._normalized_crop(frame, self.WATER_ROI)
        upper_blue, upper_edge, upper_value = self._strict_blue_metrics(upper)
        lower_blue, lower_edge, lower_value = self._strict_blue_metrics(lower)
        self.v20_sky_blue_upper_ratio = upper_blue
        self.v20_sky_blue_lower_ratio = lower_blue
        self.v20_sky_upper_edge = upper_edge
        self.v20_sky_lower_edge = lower_edge

        sky_like = bool(
            not self.water_escape_active
            and upper_blue >= self.v20_sky_blue_min
            and lower_blue >= self.v20_sky_blue_min
            and upper_edge <= self.v20_sky_edge_max
            and lower_edge <= self.v20_sky_edge_max
            and abs(upper_value - lower_value) <= self.v20_sky_value_delta_max
        )
        if sky_like:
            self.v20_sky_water_suppressions += 1
            return np.zeros_like(mask)
        return mask

    def _shoreline_metrics(self, frame: np.ndarray) -> ShorelineRisk:
        """Do not turn away from one blue side when the forward center is dry.

        V16 allowed total bottom-blue coverage alone to activate the preventative
        shoreline guard. In the live soak this produced repeated guard commands such
        as 0.40/0.00/0.00: a completely dry center corridor was still being steered
        away from because one side happened to be blue. Keep center water authoritative;
        require bilateral evidence before a total-only shoreline warning can fire.
        """
        risk = super()._shoreline_metrics(frame)
        if not risk.active:
            return risk

        center_risk = risk.center >= self.shoreline_guard_center
        broad_bilateral_risk = bool(
            risk.total >= self.v20_shoreline_broad_total
            and min(risk.left, risk.right) >= self.v20_shoreline_bilateral_min
        )
        active = bool(center_risk or broad_bilateral_risk)
        if not active:
            self.v20_shoreline_side_only_suppressions += 1
        return ShorelineRisk(
            total=risk.total,
            left=risk.left,
            center=risk.center,
            right=risk.right,
            active=active,
        )

    def _apply_runtime_budget(self, ctx: ProfileContext) -> None:
        pressure = str(ctx.performance.get("loop_pressure", "warming"))
        factor = self._pressure_factor(pressure)
        self.v20_pressure = pressure
        self.v20_pressure_factor = factor
        self.goal_refresh_seconds = self.v20_goal_refresh_base * factor
        self.cue_refresh_seconds = self.v20_cue_refresh_base * factor
        self.ledge_refresh_seconds = self.v20_ledge_refresh_base * factor
        if factor > 1.0:
            self.v20_load_shed_ticks += 1

    def _atomic_reflex_active(self) -> bool:
        return bool(
            self.navigation_commit_active
            or self.skill_active
            or getattr(self, "ledge_jump_active", False)
            or getattr(self, "mobility_active", False)
            or getattr(self, "target_resolution_active", False)
        )

    def _refresh_visual_goal(self, ctx: ProfileContext) -> None:
        if self._atomic_reflex_active():
            self.v20_reflex_perception_skips += 1
            return
        super()._refresh_visual_goal(ctx)

    def _refresh_ledge_cue(self, ctx: ProfileContext) -> None:
        if self._atomic_reflex_active():
            self.v20_reflex_perception_skips += 1
            return
        super()._refresh_ledge_cue(ctx)

    def _update_semantic_translation(self, ctx: ProfileContext) -> None:
        semantic = ctx.semantic
        try:
            x = float(semantic["jak_x"])
            y = float(semantic["jak_y"])
            z = float(semantic["jak_z"])
        except (KeyError, TypeError, ValueError):
            if self.semantic_position_last_at > 0.0:
                self.semantic_position_age = max(0.0, ctx.now - self.semantic_position_last_at)
            return
        if not all(math.isfinite(v) for v in (x, y, z)):
            return
        pos = (x, y, z)
        if self.semantic_last_position is not None:
            dx = x - self.semantic_last_position[0]
            dy = y - self.semantic_last_position[1]
            dz = z - self.semantic_last_position[2]
            self.semantic_translation_delta = math.sqrt(dx * dx + dy * dy + dz * dz)
            self.semantic_translation_total += self.semantic_translation_delta
        self.semantic_last_position = pos
        self.semantic_position_samples += 1
        self.semantic_position_last_at = ctx.now
        self.semantic_position_age = 0.0

    def _abort_locomotion_for_menu(self) -> None:
        """Cancel transient gameplay owners before a save menu gets controller input."""
        for attr in (
            "navigation_commit_active",
            "land_scan_active",
            "target_resolution_active",
            "mobility_active",
            "ledge_jump_active",
            "skill_active",
            "water_escape_active",
        ):
            if hasattr(self, attr):
                setattr(self, attr, False)
        if hasattr(self, "navigation_commit_stage"):
            self.navigation_commit_stage = "idle"
        if hasattr(self, "land_scan_stage"):
            self.land_scan_stage = "idle"
        if hasattr(self, "second_jump_pending"):
            self.second_jump_pending = False

    def _save_menu_preflight(self, controller, ctx: ProfileContext) -> str | None:
        """Give save transactions exclusive input ownership before gameplay policy runs."""
        if self.mode != "production":
            return None
        self._read_ocr_title_gate(ctx)
        if getattr(self, "save_prompt_visible", False):
            self._abort_locomotion_for_menu()
            self.v20_menu_preemptions += 1
            return self._save_prompt_gate(controller, ctx)
        if getattr(self, "save_file_selector_visible", False):
            self._abort_locomotion_for_menu()
            self.v20_menu_preemptions += 1
            return self._save_file_selector_gate(controller, ctx)
        return None

    def tick(self, controller, ctx: ProfileContext) -> str:
        self._apply_runtime_budget(ctx)
        self._update_semantic_translation(ctx)
        menu_action = self._save_menu_preflight(controller, ctx)
        if menu_action is not None:
            return menu_action
        return super().tick(controller, ctx)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "jak_policy_version": "v20",
                "jak_runtime_pressure": self.v20_pressure,
                "jak_runtime_pressure_factor": round(self.v20_pressure_factor, 2),
                "jak_load_shed_ticks": self.v20_load_shed_ticks,
                "jak_reflex_perception_skips": self.v20_reflex_perception_skips,
                "jak_menu_preemptions_v20": self.v20_menu_preemptions,
                "jak_goal_refresh_seconds_v20": round(self.goal_refresh_seconds, 3),
                "jak_cue_refresh_seconds_v20": round(self.cue_refresh_seconds, 3),
                "jak_ledge_refresh_seconds_v20": round(self.ledge_refresh_seconds, 3),
                "jak_semantic_position_samples": self.semantic_position_samples,
                "jak_semantic_translation_delta": round(self.semantic_translation_delta, 4),
                "jak_semantic_translation_total": round(self.semantic_translation_total, 3),
                "jak_semantic_position_age": round(self.semantic_position_age, 2),
                "jak_water_sky_blue_upper_ratio_v20": round(
                    self.v20_sky_blue_upper_ratio, 3
                ),
                "jak_water_sky_blue_lower_ratio_v20": round(
                    self.v20_sky_blue_lower_ratio, 3
                ),
                "jak_water_sky_upper_edge_v20": round(self.v20_sky_upper_edge, 4),
                "jak_water_sky_lower_edge_v20": round(self.v20_sky_lower_edge, 4),
                "jak_water_sky_suppressions_v20": self.v20_sky_water_suppressions,
                "jak_shoreline_side_only_suppressions_v20": (
                    self.v20_shoreline_side_only_suppressions
                ),
            }
        )
        return state

    def _v16_stream_intent(self) -> str:
        if self.v20_pressure in {"high", "critical"} and self._atomic_reflex_active():
            return f"REFLEX · V20 {self.v20_pressure.upper()} LOAD SHED"
        return super()._v16_stream_intent()
