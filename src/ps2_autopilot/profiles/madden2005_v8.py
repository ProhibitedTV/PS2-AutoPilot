from __future__ import annotations

import math

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_spatial import MaddenSpatialTracker, SpatialSnapshot
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005 import MaddenPhase, PlayIntent, Possession
from .madden2005_v7 import Madden2005V7Profile


class Madden2005V8Profile(Madden2005V7Profile):
    """Spatially aware Madden policy with confidence-gated intervention.

    v0.6 adds a lightweight player/ball localization layer but keeps the proven
    v0.5.x behavior as the safety net. Spatial evidence can refine live pursuit and
    run steering only when confidence is high enough; otherwise the parent policy
    remains completely authoritative.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.spatial_interval_seconds = max(
            0.08, float(cfg.get("spatial_interval_seconds", 0.20))
        )
        self.spatial_target_confidence = float(
            cfg.get("spatial_target_confidence", 0.50)
        )
        self.spatial_controlled_confidence = float(
            cfg.get("spatial_controlled_confidence", 0.48)
        )
        self.spatial_open_confidence = float(
            cfg.get("spatial_open_confidence", 0.42)
        )
        self.spatial_max_age_seconds = max(
            self.spatial_interval_seconds * 1.5,
            float(cfg.get("spatial_max_age_seconds", 0.70)),
        )
        self.spatial_tracker = MaddenSpatialTracker(
            sample_width=int(cfg.get("spatial_sample_width", 480)),
            min_player_confidence=float(cfg.get("spatial_player_min_confidence", 0.30)),
            max_players=int(cfg.get("spatial_max_players", 22)),
            track_ttl_seconds=float(cfg.get("spatial_track_ttl_seconds", 0.90)),
            ball_ttl_seconds=float(cfg.get("spatial_ball_ttl_seconds", 0.70)),
        )
        self.last_spatial = SpatialSnapshot(False, reason="waiting for field")
        self.last_spatial_at = -1e9
        self.spatial_policy_mode = "fallback"
        self.spatial_policy_reason = "no confident spatial evidence"
        self.spatial_overrides = 0

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)
        should_sample = (
            self.phase in {MaddenPhase.PRE_SNAP, MaddenPhase.LIVE, MaddenPhase.KICKING}
            and obs.green_ratio >= max(0.08, self.field_green_threshold * 0.65)
            and ctx.now - self.last_spatial_at >= self.spatial_interval_seconds
        )
        if should_sample:
            self.last_spatial = self.spatial_tracker.observe(
                ctx.frame,
                ctx.previous_frame,
                ctx.now,
            )
            self.last_spatial_at = ctx.now
        elif self.phase not in {MaddenPhase.PRE_SNAP, MaddenPhase.LIVE, MaddenPhase.KICKING}:
            self.spatial_policy_mode = "inactive"
            self.spatial_policy_reason = f"phase={self.phase.value}"
        return obs

    def _spatial_fresh(self, now: float) -> bool:
        return (
            self.last_spatial.available
            and self.last_spatial_at > -1e8
            and now - self.last_spatial_at <= self.spatial_max_age_seconds
        )

    def _defense_live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        action = super()._defense_live(controller, obs, now)
        spatial = self.last_spatial
        if not self._spatial_fresh(now):
            self.spatial_policy_mode = "fallback-motion"
            self.spatial_policy_reason = "spatial sample unavailable/stale"
            return action
        if spatial.target_confidence < self.spatial_target_confidence:
            self.spatial_policy_mode = "fallback-motion"
            self.spatial_policy_reason = (
                f"target confidence {spatial.target_confidence:.2f} below "
                f"{self.spatial_target_confidence:.2f}"
            )
            return action

        controlled = spatial.controlled
        if (
            controlled is not None
            and controlled.confidence >= self.spatial_controlled_confidence
        ):
            dx = spatial.target_x - controlled.x
            dy = spatial.target_y - controlled.y
            distance = max(0.16, math.hypot(dx, dy))
            stick_x = self._clamp(dx / distance * 0.92, -0.95, 0.95)
            stick_y = self._clamp(-dy / distance * 0.92, -0.95, 0.95)
            controller.set_left_stick(stick_x, stick_y)
            self.spatial_policy_mode = "defense-vector"
            self.spatial_policy_reason = (
                f"controlled#{controlled.track_id} -> target "
                f"({spatial.target_x:+.2f},{spatial.target_y:+.2f})"
            )
        else:
            # Even without a verified control marker, a good ball/action target is
            # useful as a better steering hint than the global motion centroid.
            stick_x = self._clamp(spatial.target_x * 0.78, -0.82, 0.82)
            stick_y = self._clamp(-spatial.target_y * 0.56 + 0.26, -0.82, 0.92)
            controller.set_left_stick(stick_x, stick_y)
            self.spatial_policy_mode = "defense-target"
            self.spatial_policy_reason = (
                f"target confidence {spatial.target_confidence:.2f}; control marker unverified"
            )
        self.spatial_overrides += 1
        return action

    def _offense_live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        action = super()._offense_live(controller, obs, now)
        spatial = self.last_spatial
        if self.planned_play != PlayIntent.RUN:
            self.spatial_policy_mode = "pass-parent-policy"
            self.spatial_policy_reason = "receiver mapping not yet spatially calibrated"
            return action
        if not self._spatial_fresh(now):
            self.spatial_policy_mode = "fallback-field"
            self.spatial_policy_reason = "spatial sample unavailable/stale"
            return action
        if spatial.open_space_confidence < self.spatial_open_confidence:
            self.spatial_policy_mode = "fallback-field"
            self.spatial_policy_reason = (
                f"open-space confidence {spatial.open_space_confidence:.2f} below "
                f"{self.spatial_open_confidence:.2f}"
            )
            return action

        # Preserve the parent's north/south run bias and special-move cadence while
        # replacing only the lateral direction with a conservative open-space hint.
        lateral = self._clamp(
            spatial.open_space_x * 0.62 + obs.field_center_x * 0.10,
            -0.68,
            0.68,
        )
        controller.set_left_stick(lateral, 1.0)
        self.spatial_policy_mode = "run-open-space"
        self.spatial_policy_reason = (
            f"open x={spatial.open_space_x:+.2f} confidence={spatial.open_space_confidence:.2f}"
        )
        self.spatial_overrides += 1
        return action

    def _live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        if self.possession == Possession.UNKNOWN or self.possession_confidence < 0.45:
            self.spatial_policy_mode = "fallback-unknown-role"
            self.spatial_policy_reason = "role confidence too low for spatial intervention"
        return super()._live(controller, obs, now)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(self.last_spatial.telemetry())
        state.update(
            {
                "spatial_age": round(max(0.0, ctx.now - self.last_spatial_at), 3)
                if self.last_spatial_at > -1e8
                else None,
                "spatial_policy_mode": self.spatial_policy_mode,
                "spatial_policy_reason": self.spatial_policy_reason,
                "spatial_overrides": self.spatial_overrides,
                "spatial_fresh": self._spatial_fresh(ctx.now),
            }
        )
        return state
