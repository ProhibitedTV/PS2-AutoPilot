from __future__ import annotations

from dataclasses import dataclass
import time


@dataclass
class WatchdogStatus:
    stuck: bool
    still_seconds: float
    recoveries: int


class MotionWatchdog:
    def __init__(self, threshold: float, stuck_seconds: float, cooldown_seconds: float) -> None:
        self.threshold = threshold
        self.stuck_seconds = stuck_seconds
        self.cooldown_seconds = cooldown_seconds
        self.last_motion_at = time.monotonic()
        self.last_recovery_at = 0.0
        self.recoveries = 0

    def update(self, score: float) -> WatchdogStatus:
        now = time.monotonic()
        if score >= self.threshold:
            self.last_motion_at = now
        still = now - self.last_motion_at
        stuck = still >= self.stuck_seconds and now - self.last_recovery_at >= self.cooldown_seconds
        return WatchdogStatus(stuck=stuck, still_seconds=still, recoveries=self.recoveries)

    def mark_safe_still(self) -> None:
        """A semantic profile verified that a static scene is intentional.

        Reset the raw-motion timer without incrementing recovery counters. This keeps
        long cutscenes and known static menus from generating fake failure bundles.
        """
        self.last_motion_at = time.monotonic()

    def mark_recovery(self) -> None:
        self.recoveries += 1
        self.last_recovery_at = time.monotonic()
        self.last_motion_at = self.last_recovery_at

    def reset_recoveries(self) -> None:
        self.recoveries = 0
