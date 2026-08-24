from __future__ import annotations

import math

from .base import ProfileContext
from .jak_and_daxter_v19 import JakAndDaxterV19Profile


class JakAndDaxterV20Profile(JakAndDaxterV19Profile):
    """Use rolling runtime pressure to protect platforming/reflex work.

    V19 reduced obvious duplicate CV, while the open performance tickets require a
    cross-game feedback loop: recent p50/p95/overrun health must be visible to profiles
    so expensive semantic perception can yield CPU to movement when the runtime is
    missing its control budget.

    V20 keeps water/menu safety unchanged, but dynamically stretches collectible/cue/
    ledge refresh cadence under sustained pressure and freezes goal/ledge refresh while
    an atomic locomotion skill or relocation macro owns control. It also consumes
    calibrated read-only Jak XYZ fields when available to expose real displacement
    evidence without requiring those addresses to exist yet.
    """

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

        self.semantic_last_position: tuple[float, float, float] | None = None
        self.semantic_translation_delta = 0.0
        self.semantic_translation_total = 0.0
        self.semantic_position_samples = 0
        self.semantic_position_age = 0.0
        self.semantic_position_last_at = 0.0

    @staticmethod
    def _pressure_factor(pressure: str) -> float:
        return {
            "healthy": 1.0,
            "warming": 1.0,
            "elevated": 1.25,
            "high": 1.65,
            "critical": 2.25,
        }.get(str(pressure), 1.0)

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

    def tick(self, controller, ctx: ProfileContext) -> str:
        self._apply_runtime_budget(ctx)
        self._update_semantic_translation(ctx)
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
                "jak_goal_refresh_seconds_v20": round(self.goal_refresh_seconds, 3),
                "jak_cue_refresh_seconds_v20": round(self.cue_refresh_seconds, 3),
                "jak_ledge_refresh_seconds_v20": round(self.ledge_refresh_seconds, 3),
                "jak_semantic_position_samples": self.semantic_position_samples,
                "jak_semantic_translation_delta": round(self.semantic_translation_delta, 4),
                "jak_semantic_translation_total": round(self.semantic_translation_total, 3),
                "jak_semantic_position_age": round(self.semantic_position_age, 2),
            }
        )
        return state

    def _v16_stream_intent(self) -> str:
        if self.v20_pressure in {"high", "critical"} and self._atomic_reflex_active():
            return f"REFLEX · V20 {self.v20_pressure.upper()} LOAD SHED"
        return super()._v16_stream_intent()
