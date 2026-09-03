from __future__ import annotations

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .nfs_hot_pursuit_2 import NfsPhase
from .nfs_hot_pursuit_2_v10 import NfsHotPursuit2V10Profile
from .nfs_hot_pursuit_2_v2 import NfsScreen


class NfsHotPursuit2V11Profile(NfsHotPursuit2V10Profile):
    """V11: chase-camera-safe road ownership and bounded blind driving.

    V10 proved the controller cadence and recovery-storm ladder under live PCSX2,
    then exposed the real driving failure: the color seed sat on the player's car and
    awarded walls/shoulders near-perfect road confidence. V11 consumes the corrected
    corridor geometry and adds fail-safe behavior around the remaining uncertainty:

    * a short launch guard always accelerates and limits steering instead of braking
      on the first noisy race frame;
    * moving gameplay with no valid corridor continues forward with rapidly decaying
      steering rather than alternating reverse maneuvers;
    * persistent moving-blind gameplay requests a bounded Restart Race;
    * positive wrong-way evidence restarts the race instead of attempting a blind
      wall-to-wall U-turn;
    * image-derived racing steer is capped below the recovery maneuver authority.
    """

    name = "nfs_hot_pursuit_2"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        self.launch_guard_seconds = max(1.0, float(cfg.get("launch_guard_seconds", 4.0)))
        self.launch_guard_max_steer = max(
            0.10, min(0.60, float(cfg.get("launch_guard_max_steer", 0.36)))
        )
        self.racing_max_steer = max(
            self.launch_guard_max_steer,
            min(0.85, float(cfg.get("racing_max_steer", 0.62))),
        )
        self.blind_motion_threshold = max(
            self.race_motion_threshold,
            float(cfg.get("blind_motion_threshold", 0.012)),
        )
        self.blind_steer_decay = max(
            0.0, min(0.95, float(cfg.get("blind_steer_decay", 0.78)))
        )
        self.blind_max_steer = max(
            0.05, min(0.40, float(cfg.get("blind_max_steer", 0.22)))
        )
        self.blind_restart_seconds = max(
            3.0, float(cfg.get("blind_restart_seconds", 7.0))
        )

        self.blind_moving_since: float | None = None
        self.blind_moving_ticks = 0
        self.blind_moving_restarts = 0
        self.launch_guard_ticks = 0
        self.racing_steer_clamps = 0
        self.wrong_way_restarts = 0
        self.fast_takeover_hud_blocks = 0

    def _road_takeover_allowed(self, ctx: ProfileContext, screen: NfsScreen) -> bool:
        allowed = super()._road_takeover_allowed(ctx, screen)
        if not allowed or self.phase is NfsPhase.RACING:
            return allowed
        # Road color alone is not gameplay ownership. On the stopped V10 run it
        # promoted menu/loading imagery into RACING and corrupted launch accounting.
        # V7 refreshes normalized lap/rank/map/tach HUD evidence before this method.
        if not self._hud_owned():
            self.fast_takeover_hud_blocks += 1
            return False
        return True

    def _fast_road_takeover(self, controller: Controller, ctx: ProfileContext) -> str | None:
        if not self._hud_owned():
            if (
                self.phase not in {NfsPhase.RACING, NfsPhase.RECOVERY}
                and self.screen is NfsScreen.UNKNOWN
                and self.road.confidence >= self.strong_road_confidence
                and ctx.motion >= self.fast_takeover_min_motion
            ):
                self.fast_takeover_hud_blocks += 1
            return None
        return super()._fast_road_takeover(controller, ctx)

    def _bounded_steer(self, controller: Controller, limit: float) -> float:
        bounded = self._clamp(self.last_steer, limit)
        if bounded != self.last_steer:
            self.racing_steer_clamps += 1
        self.last_steer = bounded
        controller.set_left_stick(bounded, 0.0)
        return bounded

    def _launch_guard(self, controller: Controller, ctx: ProfileContext) -> str:
        self.blind_moving_since = None
        controller.release(self.brake_action)
        controller.hold(self.accelerate_action)
        steer = self._bounded_steer(controller, self.launch_guard_max_steer)
        self.road_lost_since = None
        self.launch_guard_ticks += 1
        return (
            f"v11 launch guard: throttle steer={steer:+.2f} "
            f"road={self.road.confidence:.2f}"
        )

    def _moving_blind_drive(self, controller: Controller, ctx: ProfileContext) -> str:
        if self.blind_moving_since is None:
            self.blind_moving_since = ctx.now
        age = max(0.0, ctx.now - self.blind_moving_since)
        if age >= self.blind_restart_seconds and self.hard_restart_stage is None:
            self.blind_moving_restarts += 1
            return self._begin_hard_restart(
                controller,
                ctx.now,
                frame=ctx.frame,
                source="moving-blind",
            )

        controller.release(self.brake_action)
        controller.hold(self.accelerate_action)
        self.last_steer *= self.blind_steer_decay
        steer = self._bounded_steer(controller, self.blind_max_steer)
        self.road_lost_since = None
        self.blind_moving_ticks += 1
        reason = self.road.rejection_reason or "no-corridor"
        return (
            f"v11 moving-blind: forward/decay steer={steer:+.2f} "
            f"age={age:.1f}s reason={reason}"
        )

    def _start_recovery(self, controller: Controller, ctx: ProfileContext, reason: str) -> str:
        if (
            "wrong-way" in reason.lower()
            and self._hud_owned()
            and self.hard_restart_stage is None
        ):
            self.wrong_way_restarts += 1
            return self._begin_hard_restart(
                controller,
                ctx.now,
                frame=ctx.frame,
                source="wrong-way",
            )
        return super()._start_recovery(controller, ctx, reason)

    def _finish_hard_restart(self, ctx: ProfileContext, *, progressed: bool) -> str:
        self.blind_moving_since = None
        return super()._finish_hard_restart(ctx, progressed=progressed)

    def _drive(self, controller: Controller, ctx: ProfileContext) -> str:
        phase_age = max(0.0, ctx.now - self.phase_since)
        if phase_age <= self.launch_guard_seconds:
            return self._launch_guard(controller, ctx)

        if self.road.confidence < self.drive_confidence:
            if self._hud_owned() and ctx.motion >= self.blind_motion_threshold:
                return self._moving_blind_drive(controller, ctx)
            self.blind_moving_since = None
            return super()._drive(controller, ctx)

        self.blind_moving_since = None
        action = super()._drive(controller, ctx)
        if self.phase is not NfsPhase.RACING:
            return action
        steer = self._bounded_steer(controller, self.racing_max_steer)
        if abs(steer) >= self.racing_max_steer - 1e-6:
            return f"{action} v11-steer-cap={steer:+.2f}"
        return action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        blind_age = None
        if self.blind_moving_since is not None:
            blind_age = round(max(0.0, ctx.now - self.blind_moving_since), 2)
        state.update(
            {
                "nfs_policy_version": 11,
                "nfs_road_rejection_reason": self.road.rejection_reason,
                "nfs_launch_guard_seconds": round(self.launch_guard_seconds, 2),
                "nfs_launch_guard_ticks": self.launch_guard_ticks,
                "nfs_racing_max_steer": round(self.racing_max_steer, 2),
                "nfs_racing_steer_clamps": self.racing_steer_clamps,
                "nfs_blind_moving_age": blind_age,
                "nfs_blind_moving_ticks": self.blind_moving_ticks,
                "nfs_blind_moving_restarts": self.blind_moving_restarts,
                "nfs_wrong_way_restarts": self.wrong_way_restarts,
                "nfs_fast_takeover_hud_blocks": self.fast_takeover_hud_blocks,
            }
        )
        return state
