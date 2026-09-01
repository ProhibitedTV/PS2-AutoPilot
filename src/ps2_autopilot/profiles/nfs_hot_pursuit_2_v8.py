from __future__ import annotations

import time

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.nfs_hp2_hud import estimate_gameplay_hud

from .base import ProfileContext
from .nfs_hot_pursuit_2 import NfsPhase
from .nfs_hot_pursuit_2_v7 import NfsHotPursuit2V7Profile


class NfsHotPursuit2V8Profile(NfsHotPursuit2V7Profile):
    """V8: long-stall escape and race-restart fallback for 24/7 HP2.

    The overnight V6 corpus was not merely an UNKNOWN-screen problem: the car had
    remained at 0 mph against the same wall for more than eight hours. V7 fixes
    gameplay ownership by recognizing the fixed HUD. V8 closes the next loop:

    * shared-watchdog recovery now participates in V4's alternating recovery streak
      instead of bypassing it with the same steering direction every time;
    * HUD-owned recovery completion stays in gameplay ownership when road vision is
      still blind, so the profile can make another bounded escape attempt;
    * after several failed HUD-owned escape attempts over a sustained stall, V8 uses
      the documented HP2 Pause Menu ordering to request Restart Race rather than
      leaving a 24/7 stream parked against a wall forever.

    Hard restart is deliberately a last resort. It requires strong fixed-HUD evidence,
    very low motion, poor road confidence, multiple failed recoveries and a minimum
    stall age. Normal racing, menus and short recoverable crashes never enter it.
    """

    name = "nfs_hot_pursuit_2"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        self.hud_stall_motion_threshold = max(
            0.0005, float(cfg.get("hud_stall_motion_threshold", 0.0035))
        )
        self.hud_stall_reset_motion = max(
            self.hud_stall_motion_threshold * 2.0,
            float(cfg.get("hud_stall_reset_motion", 0.020)),
        )
        self.hud_stall_hard_restart_enabled = bool(
            cfg.get("hud_stall_hard_restart_enabled", True)
        )
        self.hud_stall_hard_restart_recoveries = max(
            2, int(cfg.get("hud_stall_hard_restart_recoveries", 4))
        )
        self.hud_stall_hard_restart_seconds = max(
            12.0, float(cfg.get("hud_stall_hard_restart_seconds", 28.0))
        )
        self.hard_restart_action_seconds = max(
            0.35, float(cfg.get("hard_restart_action_seconds", 0.75))
        )
        self.hard_restart_wait_seconds = max(
            2.0, float(cfg.get("hard_restart_wait_seconds", 7.0))
        )
        self.hard_restart_progress_delta = max(
            0.005, min(0.30, float(cfg.get("hard_restart_progress_delta", 0.025)))
        )

        self.hud_stall_since: float | None = None
        self.hud_stall_recovery_attempts = 0
        self.hud_watchdog_recovery_arms = 0

        self.hard_restart_stage: str | None = None
        self.hard_restart_stage_since = -1e9
        self.hard_restart_reference = None
        self.hard_restart_progress_seen = False
        self.hard_restart_attempts = 0
        self.hard_restart_inputs = 0
        self.hard_restart_successes = 0
        self.hard_restart_failures = 0

    def _hud_owned(self) -> bool:
        return (
            self.hud_gameplay_enabled
            and self.hud.confidence >= self.hud_gameplay_threshold
        )

    def _note_hud_stall(self, ctx: ProfileContext) -> None:
        stalled = (
            self._hud_owned()
            and self.road.confidence < self.drive_confidence
            and ctx.motion <= self.hud_stall_motion_threshold
        )
        if stalled:
            if self.hud_stall_since is None:
                self.hud_stall_since = ctx.now
            return

        # Do not erase a useful recovery streak because a wall wiggle produced a
        # couple pixels of motion. Require a coherent road or clearly meaningful
        # motion before declaring the trap escaped.
        escaped = (
            self.road.confidence >= self.drive_confidence
            or ctx.motion >= self.hud_stall_reset_motion
        )
        if escaped:
            self.hud_stall_since = None
            self.hud_stall_recovery_attempts = 0

    def _hard_restart_ready(self, now: float) -> bool:
        if not self.hud_stall_hard_restart_enabled or not self._hud_owned():
            return False
        if self.hud_stall_since is None:
            return False
        if self.hud_stall_recovery_attempts < self.hud_stall_hard_restart_recoveries:
            return False
        return now - self.hud_stall_since >= self.hud_stall_hard_restart_seconds

    def _begin_hard_restart(
        self,
        controller: Controller,
        now: float,
        *,
        frame=None,
        source: str,
    ) -> str:
        controller.release_all()
        controller.neutral_sticks()
        controller.tap("start", 0.08)
        self.hard_restart_stage = "pause"
        self.hard_restart_stage_since = now
        self.hard_restart_reference = self._fingerprint(frame)
        self.hard_restart_progress_seen = False
        self.hard_restart_attempts += 1
        self.hard_restart_inputs += 1
        return (
            "hud hard-stall restart: tap start "
            f"source={source} recoveries={self.hud_stall_recovery_attempts}"
        )

    def _finish_hard_restart(self, ctx: ProfileContext, *, progressed: bool) -> str:
        self.hard_restart_stage = None
        self.hard_restart_stage_since = -1e9
        self.hard_restart_reference = None
        self.hard_restart_progress_seen = False
        self.hud_stall_since = None
        self.hud_stall_recovery_attempts = 0
        self.road_lost_since = None
        self._set_phase(NfsPhase.CALIBRATION, ctx.now)
        if progressed:
            self.hard_restart_successes += 1
            return "hud hard-stall restart: visual progress; hand back to lifecycle"
        self.hard_restart_failures += 1
        return "hud hard-stall restart: no progress; hand back to unattended reacquisition"

    def _tick_hard_restart(self, controller: Controller, ctx: ProfileContext) -> str:
        controller.release_all()
        controller.neutral_sticks()

        current_fp = self._fingerprint(ctx.frame)
        delta = self._fingerprint_delta(self.hard_restart_reference, current_fp)
        if delta >= self.hard_restart_progress_delta:
            self.hard_restart_progress_seen = True

        age = max(0.0, ctx.now - self.hard_restart_stage_since)
        if self.hard_restart_stage == "pause":
            if age < self.hard_restart_action_seconds:
                return "hud hard-stall restart: wait for pause menu"
            # HP2 Pause Menu is ordered Resume Race, Restart Race, ... . The menu
            # opens on Resume Race, so one Down selects Restart Race.
            controller.tap("down", 0.08)
            self.hard_restart_inputs += 1
            self.hard_restart_stage = "restart_selected"
            self.hard_restart_stage_since = ctx.now
            return "hud hard-stall restart: select Restart Race"

        if self.hard_restart_stage == "restart_selected":
            if age < self.hard_restart_action_seconds:
                return "hud hard-stall restart: settle restart selection"
            controller.tap("confirm", 0.08)
            self.hard_restart_inputs += 1
            self.hard_restart_stage = "restart_confirm"
            self.hard_restart_stage_since = ctx.now
            return "hud hard-stall restart: confirm Restart Race"

        if self.hard_restart_stage == "restart_confirm":
            if age < self.hard_restart_action_seconds:
                return "hud hard-stall restart: wait for restart confirmation"
            # Some regional builds/settings may show an additional confirmation.
            # X is also gas, so one bounded second Confirm is harmless if the race
            # already began transitioning/loading.
            controller.tap("confirm", 0.08)
            self.hard_restart_inputs += 1
            self.hard_restart_stage = "wait_restart"
            self.hard_restart_stage_since = ctx.now
            return "hud hard-stall restart: bounded second confirm"

        if self.hard_restart_stage == "wait_restart":
            if self.hard_restart_progress_seen and age >= 1.25:
                return self._finish_hard_restart(ctx, progressed=True)
            if age >= self.hard_restart_wait_seconds:
                return self._finish_hard_restart(ctx, progressed=False)
            return "hud hard-stall restart: awaiting loading/countdown progress"

        return self._finish_hard_restart(ctx, progressed=False)

    def _start_recovery(self, controller: Controller, ctx: ProfileContext, reason: str) -> str:
        self._note_hud_stall(ctx)
        if self._hard_restart_ready(ctx.now):
            return self._begin_hard_restart(
                controller,
                ctx.now,
                frame=ctx.frame,
                source="road-loss",
            )

        if self._hud_owned() and ctx.motion <= self.hud_stall_motion_threshold:
            self.hud_stall_recovery_attempts += 1
        return super()._start_recovery(controller, ctx, reason)

    def _tick_recovery(self, controller: Controller, ctx: ProfileContext) -> str:
        action = super()._tick_recovery(controller, ctx)

        if self.road.confidence >= self.drive_confidence:
            self.hud_stall_since = None
            self.hud_stall_recovery_attempts = 0
            return action

        # V4 recovery used CALIBRATION when the road detector remained blind. V7's
        # fixed HUD proves we are still in gameplay, so retain gameplay ownership and
        # let the normal road-loss timer schedule the next bounded escape attempt.
        if action == "recovery complete: awaiting road/template evidence" and self._hud_owned():
            self._set_phase(NfsPhase.RACING, ctx.now)
            self.road_lost_since = ctx.now
            return "recovery complete: HUD still owns gameplay; continue escape monitoring"
        return action

    def recover(self, controller: Controller) -> str:
        if self.hard_restart_stage is not None:
            controller.release_all()
            controller.neutral_sticks()
            return "nfs hard-stall restart already active"

        if not self._hud_owned():
            return super().recover(controller)

        now = time.monotonic()
        if self._hard_restart_ready(now):
            return self._begin_hard_restart(
                controller,
                now,
                source="shared-watchdog",
            )

        # V1's context-free watchdog arm predates V4's alternating recovery streak.
        # Reproduce the V4 streak bookkeeping here so watchdog-triggered recoveries
        # do not use the same steering direction forever.
        previous_start = self.last_recovery_started_at
        if now - previous_start <= self.recovery_streak_window_seconds:
            self.recovery_streak += 1
        else:
            self.recovery_streak = 1
        self.last_recovery_started_at = now

        direction = -1.0 if self.last_steer > 0.08 else 1.0
        if self.last_steer < -0.08:
            direction = 1.0
        if self.recovery_streak % 2 == 0:
            direction *= -1.0
            self.recovery_escalations += 1
        self.recovery_direction = direction

        controller.release_all()
        controller.neutral_sticks()
        self.recoveries += 1
        self.hud_stall_recovery_attempts += 1
        self.hud_watchdog_recovery_arms += 1
        self.recovery_reason = "shared motion watchdog"
        self.recovery_started_at = now
        self.road_lost_since = None
        self._set_phase(NfsPhase.RECOVERY, now)
        return (
            "nfs HUD-owned watchdog recovery armed "
            f"streak={self.recovery_streak} direction={self.recovery_direction:+.0f}"
        )

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        if self.hard_restart_stage is not None:
            # Keep the fixed-HUD observation current for telemetry while the hard
            # restart state machine exclusively owns controller input.
            self.hud = estimate_gameplay_hud(ctx.frame)
            self.last_action = self._tick_hard_restart(controller, ctx)
            return self.last_action

        action = super().tick(controller, ctx)
        self._note_hud_stall(ctx)
        return action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        stall_age = None
        if self.hud_stall_since is not None:
            stall_age = round(max(0.0, ctx.now - self.hud_stall_since), 2)
        state.update(
            {
                "nfs_policy_version": 8,
                "nfs_hud_stall_age": stall_age,
                "nfs_hud_stall_recovery_attempts": self.hud_stall_recovery_attempts,
                "nfs_hud_watchdog_recovery_arms": self.hud_watchdog_recovery_arms,
                "nfs_hard_restart_enabled": self.hud_stall_hard_restart_enabled,
                "nfs_hard_restart_stage": self.hard_restart_stage,
                "nfs_hard_restart_attempts": self.hard_restart_attempts,
                "nfs_hard_restart_inputs": self.hard_restart_inputs,
                "nfs_hard_restart_successes": self.hard_restart_successes,
                "nfs_hard_restart_failures": self.hard_restart_failures,
            }
        )
        return state
