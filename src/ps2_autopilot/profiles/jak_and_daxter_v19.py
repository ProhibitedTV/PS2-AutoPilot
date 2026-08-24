from __future__ import annotations

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .jak_and_daxter_v18 import JakAndDaxterV18Profile


class JakAndDaxterV19Profile(JakAndDaxterV18Profile):
    """Turn generic stagnation into displacement and trim expensive perception work.

    The first V18 soak fixed reward-control thrashing, but exposed two lower-level
    bottlenecks. First, generic loop/stagnation recovery still spent most of its time
    sweeping the camera and rescanning nearly identical corridors instead of actually
    relocating Jak. Second, the vision policy was badly over budget for a platformer:
    the live bundle showed ~240 ms median policy time against an ~83 ms loop budget.

    V19 therefore treats repeated stagnation as a navigation failure, not a request for
    another camera sweep. It promotes the existing V18 backoff -> pivot -> sustained
    drive macro to the generic anti-loop path, uses a route scan only for genuinely
    informative decisions, and falls back to a displacement macro when left/right scan
    scores are effectively tied.

    It also reduces duplicate vision work without weakening the safety hierarchy:
    collectible perception and ledge analysis are staggered, route scoring consumes the
    already-cached reward hint instead of rerunning four collectible detectors on each
    left/right sample, and water connected-component work is capped at a smaller width.
    Water/shoreline safety, menu ownership and specialist modes remain higher priority.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        # V18 live soak: median policy ~=240 ms vs ~=83 ms budget. The old 0.12/0.16s
        # visual refresh intervals therefore caused the expensive detectors to run on
        # nearly every actual tick. Stagger semantic/reward perception from locomotion.
        self.goal_refresh_seconds = max(
            self.goal_refresh_seconds,
            float(cfg.get("v19_goal_refresh_seconds", 0.45)),
        )
        self.cue_refresh_seconds = max(
            self.cue_refresh_seconds,
            float(cfg.get("v19_cue_refresh_seconds", 0.40)),
        )
        self.ledge_refresh_seconds = max(
            0.12, float(cfg.get("v19_ledge_refresh_seconds", 0.32))
        )
        self.next_ledge_refresh_at = 0.0
        self.water_geometry_max_width = min(
            self.water_geometry_max_width,
            max(200, int(cfg.get("v19_water_geometry_max_width", 360))),
        )
        self.goal_scan_salience_weight = min(self.goal_scan_salience_weight, 0.30)
        self.land_scan_cooldown_seconds = max(self.land_scan_cooldown_seconds, 7.0)

        # Progress HUD probing is useful, but holding traversal every minute creates a
        # visible pause. Keep it shorter and less frequent while semantic PINE remains
        # unavailable.
        if hasattr(self, "progress_probe_hold_seconds"):
            self.progress_probe_hold_seconds = min(self.progress_probe_hold_seconds, 0.80)
        if hasattr(self, "progress_probe_seconds"):
            self.progress_probe_seconds = max(self.progress_probe_seconds, 90.0)

        # Route scans in the V18 bundle frequently scored e.g. +0.13/+0.13. Those are
        # not decisions. A near-tie now means "get a new viewpoint" rather than spend
        # another several seconds scanning the same local geometry.
        self.scan_decision_margin = max(
            0.005, min(0.25, float(cfg.get("v19_scan_decision_margin", 0.035)))
        )
        self.stagnation_macro_cooldown_seconds = max(
            2.0, float(cfg.get("v19_stagnation_macro_cooldown_seconds", 6.0))
        )
        self.next_stagnation_macro_at = 0.0
        self.stagnation_escape_direction = 1.0

        # Give generic displacement a little more time than a rejected-target escape.
        self.nav_escape_drive_seconds = max(
            self.nav_escape_drive_seconds,
            float(cfg.get("v19_stagnation_drive_seconds", 3.2)),
        )

        self.stagnation_macro_commits = 0
        self.ambiguous_scan_macros = 0
        self.loop_sweeps_preempted = 0
        self.cached_scan_reward_reads = 0
        self.ledge_refresh_skips = 0

    def _refresh_ledge_cue(self, ctx: ProfileContext) -> None:
        # Ledge Sobel/Canny analysis used to run on every on-foot policy tick. It only
        # needs to be fast enough to catch an approaching step, not duplicate the
        # controller loop rate.
        if ctx.now < self.next_ledge_refresh_at:
            self.ledge_refresh_skips += 1
            return
        self.next_ledge_refresh_at = ctx.now + self.ledge_refresh_seconds
        super()._refresh_ledge_cue(ctx)

    def _visual_interest_score(self, frame) -> float:
        # V15 route scoring called _best_visual_goal(frame), which reran Cell, Orb,
        # Scout and Eco connected-component detectors for both the left and right scan
        # samples. V19 uses the most recent cached hint instead. Free-space geometry is
        # the route scan's job; reward classification can refresh on its own cadence.
        goal = self.visual_goal
        if goal.kind == "none":
            return 0.0
        self.cached_scan_reward_reads += 1
        raw = self._clamp(goal.score / 2.0, 0.0, 1.0)
        if self._goal_has_navigation_authority(goal, ignore_cooldown=True):
            return raw * 0.35
        return raw * self.reward_hint_scan_scale * 0.50

    def _stagnation_direction(self) -> float:
        # Prefer the drier side if shoreline evidence is asymmetric. Otherwise
        # alternate macro headings so repeated local failures do not create circles.
        water_delta = float(self.water_ratio_left) - float(self.water_ratio_right)
        if abs(water_delta) >= 0.04:
            return 1.0 if water_delta > 0.0 else -1.0
        direction = self.stagnation_escape_direction
        self.stagnation_escape_direction *= -1.0
        return direction

    def _begin_stagnation_commit(self, ctx: ProfileContext, *, reason: str) -> None:
        direction = self._stagnation_direction()
        self._begin_navigation_commit(ctx, reason=reason, direction=direction)
        self.next_stagnation_macro_at = ctx.now + self.stagnation_macro_cooldown_seconds
        self.stagnation_macro_commits += 1

    def _start_land_scan(
        self, controller: Controller, ctx: ProfileContext, *, reason: str
    ) -> str:
        # Loop/stagnation is already strong evidence that local camera-only reasoning
        # has failed. Relocate first; save the scan for objective-level orientation.
        if (
            "loop/stagnation" in reason
            and ctx.now >= self.next_stagnation_macro_at
            and not self.water_escape_active
            and not self.shoreline_risk.active
        ):
            self._begin_stagnation_commit(ctx, reason="generic-stagnation")
            return self._service_navigation_commit(controller, ctx)
        return super()._start_land_scan(controller, ctx, reason=reason)

    def _service_land_scan(self, controller: Controller, ctx: ProfileContext) -> str:
        action = super()._service_land_scan(controller, ctx)

        # Parent may transition from the right sample directly into its commit stage in
        # this same call. If the scores are effectively tied, overwrite that tiny
        # steering choice with a real relocation macro.
        if (
            self.land_scan_active
            and self.land_scan_stage == "commit"
            and abs(self.land_scan_left_score - self.land_scan_right_score)
            < self.scan_decision_margin
            and ctx.now >= self.next_stagnation_macro_at
            and not self.water_escape_active
            and not self.shoreline_risk.active
        ):
            self.ambiguous_scan_macros += 1
            self._cancel_land_scan()
            self._begin_stagnation_commit(ctx, reason="ambiguous-route-scan")
            return self._service_navigation_commit(controller, ctx)
        return action

    def _on_foot(self, controller: Controller, ctx: ProfileContext) -> str:
        action = super()._on_foot(controller, ctx)

        # Some inherited anti-loop paths emit the legacy camera-sweep action before
        # V13 gets a chance to start its route scan. Do not spend another tick staring
        # around: immediately replace that final controller state with displacement.
        if (
            "visual loop/stagnation" in action
            and ctx.now >= self.next_stagnation_macro_at
            and not self.navigation_commit_active
            and not self.land_scan_active
            and not self.skill_active
            and not self.water_escape_active
            and not self.shoreline_risk.active
        ):
            self.loop_sweeps_preempted += 1
            self._begin_stagnation_commit(ctx, reason="loop-sweep-preempted")
            return self._service_navigation_commit(controller, ctx)
        return action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "jak_policy_version": "v19",
                "jak_stagnation_macro_commits": self.stagnation_macro_commits,
                "jak_ambiguous_scan_macros": self.ambiguous_scan_macros,
                "jak_loop_sweeps_preempted": self.loop_sweeps_preempted,
                "jak_scan_decision_margin": round(self.scan_decision_margin, 3),
                "jak_cached_scan_reward_reads": self.cached_scan_reward_reads,
                "jak_ledge_refresh_seconds": round(self.ledge_refresh_seconds, 3),
                "jak_ledge_refresh_skips": self.ledge_refresh_skips,
                "jak_goal_refresh_seconds_v19": round(self.goal_refresh_seconds, 3),
                "jak_cue_refresh_seconds_v19": round(self.cue_refresh_seconds, 3),
                "jak_water_geometry_max_width_v19": self.water_geometry_max_width,
            }
        )
        return state

    def _v16_stream_intent(self) -> str:
        if self.navigation_commit_active and self.navigation_commit_reason in {
            "generic-stagnation",
            "loop-sweep-preempted",
            "ambiguous-route-scan",
        }:
            return f"NAV · V19 RELOCATE ({self.navigation_commit_stage.upper()})"
        return super()._v16_stream_intent()
