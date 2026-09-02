from __future__ import annotations

from collections import deque
import time

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .nfs_hot_pursuit_2 import NfsPhase
from .nfs_hot_pursuit_2_v9 import NfsHotPursuit2V9Profile


class NfsHotPursuit2V10Profile(NfsHotPursuit2V9Profile):
    """V10: recovery-storm liveness and honest race lifecycle telemetry.

    The first full overnight V9 run proved that a wall trap could produce enough
    camera motion and intermittent road confidence to reset the instantaneous HUD
    stall detector forever. V10 adds a second, deliberately coarse liveness signal:
    repeated HUD-owned recovery starts in a rolling window. When that density crosses
    a configured limit, the existing bounded Restart Race state machine takes over.

    V10 also distinguishes a verified race launch from a temporary loss and
    reacquisition of gameplay evidence. This keeps race-entry telemetry useful during
    a long soak and prevents replay cadence from advancing hundreds of times inside a
    single failed race.
    """

    name = "nfs_hot_pursuit_2"

    _LIFECYCLE_PHASES = {
        NfsPhase.TITLE,
        NfsPhase.MAIN_MENU,
        NfsPhase.RACE_SETUP,
        NfsPhase.LOADING,
        NfsPhase.COUNTDOWN,
        NfsPhase.RESULTS,
    }

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        self.recovery_storm_enabled = bool(cfg.get("recovery_storm_enabled", True))
        self.recovery_storm_window_seconds = max(
            20.0, float(cfg.get("recovery_storm_window_seconds", 120.0))
        )
        self.recovery_storm_limit = max(3, int(cfg.get("recovery_storm_limit", 8)))
        self.showmanship_recovery_quiet_seconds = max(
            0.0, float(cfg.get("showmanship_recovery_quiet_seconds", 60.0))
        )

        self.recovery_storm_starts: deque[float] = deque()
        self.recovery_storm_triggers = 0
        self.last_recovery_at = -1e9

        # The first gameplay acquisition is a real launch. Thereafter, only positive
        # title/menu/setup/loading/countdown/results evidence (or a verified restart)
        # arms another launch. CALIBRATION alone is not sufficient: recovery uses it
        # as a bounded reacquisition state inside the same race.
        self.race_launch_armed = True
        self.verified_race_entries = 0
        self.gameplay_reacquisitions = 0

    def _prune_recovery_storm(self, now: float) -> None:
        cutoff = now - self.recovery_storm_window_seconds
        while self.recovery_storm_starts and self.recovery_storm_starts[0] < cutoff:
            self.recovery_storm_starts.popleft()

    def _note_recovery_start(self, now: float) -> None:
        self.last_recovery_at = now
        if not self._hud_owned():
            return
        self._prune_recovery_storm(now)
        self.recovery_storm_starts.append(now)

    def _recovery_storm_ready(self, now: float) -> bool:
        self._prune_recovery_storm(now)
        return (
            self.recovery_storm_enabled
            and self.hud_stall_hard_restart_enabled
            and self._hud_owned()
            and len(self.recovery_storm_starts) >= self.recovery_storm_limit
        )

    def _begin_recovery_storm_restart(
        self,
        controller: Controller,
        ctx: ProfileContext,
        *,
        source: str,
    ) -> str:
        self.recovery_storm_triggers += 1
        count = len(self.recovery_storm_starts)
        action = self._begin_hard_restart(
            controller,
            ctx.now,
            frame=ctx.frame,
            source=source,
        )
        return f"{action} storm={count}/{self.recovery_storm_limit}"

    def _start_recovery(self, controller: Controller, ctx: ProfileContext, reason: str) -> str:
        self._note_recovery_start(ctx.now)
        if self._recovery_storm_ready(ctx.now):
            return self._begin_recovery_storm_restart(
                controller,
                ctx,
                source="recovery-storm",
            )
        return super()._start_recovery(controller, ctx, reason)

    def recover(self, controller: Controller) -> str:
        if self.hard_restart_stage is not None:
            return super().recover(controller)

        now = time.monotonic()
        self._note_recovery_start(now)
        if self._recovery_storm_ready(now):
            # The shared watchdog has no current frame, but V8's fingerprint helper
            # deliberately accepts None and V9 establishes its verification reference
            # after the final confirmation.
            ctx = ProfileContext(frame=None, motion=0.0, template=None, now=now)
            return self._begin_recovery_storm_restart(
                controller,
                ctx,
                source="shared-watchdog-storm",
            )
        return super().recover(controller)

    def _finish_hard_restart(self, ctx: ProfileContext, *, progressed: bool) -> str:
        action = super()._finish_hard_restart(ctx, progressed=progressed)
        self.recovery_storm_starts.clear()
        if progressed:
            self.race_launch_armed = True
        return action

    def _finish_hard_quit(self, ctx: ProfileContext, *, progressed: bool) -> str:
        action = super()._finish_hard_quit(ctx, progressed=progressed)
        self.recovery_storm_starts.clear()
        if progressed:
            self.race_launch_armed = True
        return action

    def _reconcile_race_entries(self, previous_entries: int) -> None:
        delta = max(0, self.race_entries - previous_entries)
        if not delta:
            return
        if self.race_launch_armed:
            self.verified_race_entries += delta
            self.race_launch_armed = False
            return

        self.gameplay_reacquisitions += delta
        self.race_entries -= delta

    def _showmanship_safe(self, ctx: ProfileContext) -> bool:
        if ctx.now - self.last_recovery_at < self.showmanship_recovery_quiet_seconds:
            return False
        return super()._showmanship_safe(ctx)

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        previous_entries = self.race_entries
        action = super().tick(controller, ctx)
        self._reconcile_race_entries(previous_entries)

        if self.phase in self._LIFECYCLE_PHASES:
            self.race_launch_armed = True
            self.recovery_storm_starts.clear()
        else:
            self._prune_recovery_storm(ctx.now)
        return action

    def telemetry(self, ctx: ProfileContext) -> dict:
        self._prune_recovery_storm(ctx.now)
        state = super().telemetry(ctx)
        last_recovery_age = None
        if self.last_recovery_at > -1e8:
            last_recovery_age = round(max(0.0, ctx.now - self.last_recovery_at), 2)
        state.update(
            {
                "nfs_policy_version": 10,
                "nfs_verified_race_entries": self.verified_race_entries,
                "nfs_gameplay_reacquisitions": self.gameplay_reacquisitions,
                "nfs_race_launch_armed": self.race_launch_armed,
                "nfs_recovery_storm_enabled": self.recovery_storm_enabled,
                "nfs_recovery_storm_count": len(self.recovery_storm_starts),
                "nfs_recovery_storm_limit": self.recovery_storm_limit,
                "nfs_recovery_storm_window_seconds": round(
                    self.recovery_storm_window_seconds, 2
                ),
                "nfs_recovery_storm_triggers": self.recovery_storm_triggers,
                "nfs_last_recovery_age": last_recovery_age,
                "nfs_showmanship_recovery_quiet_seconds": round(
                    self.showmanship_recovery_quiet_seconds, 2
                ),
            }
        )
        return state
