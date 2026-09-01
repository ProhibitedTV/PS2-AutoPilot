from __future__ import annotations

import numpy as np

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .nfs_hot_pursuit_2 import NfsPhase
from .nfs_hot_pursuit_2_v2 import NfsScreen
from .nfs_hot_pursuit_2_v5 import NfsHotPursuit2V5Profile


class NfsHotPursuit2V6Profile(NfsHotPursuit2V5Profile):
    """Unattended bootstrap/reacquisition policy for 24/7 HP2 streams.

    V1-V5 intentionally left UNKNOWN screens inert until a local template or a
    full five-frame road lock appeared. The first live V5 run proved that this is
    too conservative for an unattended stream: the runtime stayed healthy for
    minutes while the game policy remained in CALIBRATION.

    V6 keeps positive templates as the highest-quality evidence, but makes them
    optional. On an unknown non-racing screen it runs a slow, progress-aware PS2
    bootstrap sequence (Start -> Down -> Confirm...) and waits for visual change
    after every input. Moving unknown scenes are observed before probing, with a
    bounded force-after escape so an animated attract/title screen cannot deadlock
    forever. Strong road evidence also has a two-frame fast path, based on the live
    V5 run where high-confidence road observations appeared but never survived the
    old five-frame race-entry gate.
    """

    name = "nfs_hot_pursuit_2"

    _DEFAULT_BOOTSTRAP_SEQUENCE = (
        "start",
        "down",
        "confirm",
        "confirm",
        "confirm",
        "confirm",
    )

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        self.unattended_bootstrap_enabled = bool(cfg.get("unattended_bootstrap_enabled", True))
        self.bootstrap_stable_seconds = max(0.35, float(cfg.get("bootstrap_stable_seconds", 1.25)))
        self.bootstrap_settle_seconds = max(0.25, float(cfg.get("bootstrap_settle_seconds", 0.85)))
        self.bootstrap_progress_timeout_seconds = max(
            0.75, float(cfg.get("bootstrap_progress_timeout_seconds", 2.0))
        )
        self.bootstrap_progress_delta = max(
            0.005, min(0.40, float(cfg.get("bootstrap_progress_delta", 0.028)))
        )
        self.bootstrap_motion_guard = max(
            self.race_motion_threshold,
            float(cfg.get("bootstrap_motion_guard", 0.035)),
        )
        self.bootstrap_force_after_seconds = max(
            2.0, float(cfg.get("bootstrap_force_after_seconds", 7.0))
        )
        self.bootstrap_cycle_pause_seconds = max(
            0.5, float(cfg.get("bootstrap_cycle_pause_seconds", 2.0))
        )
        self.strong_road_confidence = max(
            self.race_enter_confidence,
            min(1.0, float(cfg.get("strong_road_confidence", 0.82))),
        )
        self.strong_road_enter_frames = max(
            1, min(self.race_enter_frames, int(cfg.get("strong_road_enter_frames", 2)))
        )

        configured_sequence = cfg.get("bootstrap_sequence")
        if isinstance(configured_sequence, (list, tuple)):
            cleaned = tuple(str(item).strip().lower() for item in configured_sequence if str(item).strip())
        else:
            cleaned = ()
        self.bootstrap_sequence = cleaned or self._DEFAULT_BOOTSTRAP_SEQUENCE

        self.bootstrap_unknown_since: float | None = None
        self.bootstrap_stable_since: float | None = None
        self.bootstrap_pending_action: str | None = None
        self.bootstrap_pending_since = -1e9
        self.bootstrap_reference: np.ndarray | None = None
        self.bootstrap_step = 0
        self.bootstrap_next_probe_at = 0.0

        self.bootstrap_actions = 0
        self.bootstrap_progress_acks = 0
        self.bootstrap_timeouts = 0
        self.bootstrap_motion_holds = 0
        self.bootstrap_cycles = 0
        self.bootstrap_fast_race_entries = 0
        self.bootstrap_watchdog_kicks = 0

    @staticmethod
    def _fingerprint(frame: np.ndarray | None) -> np.ndarray | None:
        if frame is None or frame.size == 0 or frame.ndim != 3:
            return None
        h, w = frame.shape[:2]
        if h < 2 or w < 2:
            return None
        # A tiny luminance fingerprint is enough to acknowledge gross menu/screen
        # progress and costs almost nothing in the 12 Hz control loop.
        ys = np.linspace(0, h - 1, 18, dtype=np.int32)
        xs = np.linspace(0, w - 1, 32, dtype=np.int32)
        sample = frame[np.ix_(ys, xs)].astype(np.float32)
        return sample.mean(axis=2)

    @staticmethod
    def _fingerprint_delta(a: np.ndarray | None, b: np.ndarray | None) -> float:
        if a is None or b is None or a.shape != b.shape:
            return 0.0
        return float(np.mean(np.abs(a - b)) / 255.0)

    def _reset_bootstrap(self, *, reset_step: bool) -> None:
        self.bootstrap_unknown_since = None
        self.bootstrap_stable_since = None
        self.bootstrap_pending_action = None
        self.bootstrap_pending_since = -1e9
        self.bootstrap_reference = None
        if reset_step:
            self.bootstrap_step = 0

    def _advance_bootstrap_step(self, now: float) -> None:
        self.bootstrap_step += 1
        if self.bootstrap_step >= len(self.bootstrap_sequence):
            self.bootstrap_step = 0
            self.bootstrap_cycles += 1
            self.bootstrap_next_probe_at = max(
                self.bootstrap_next_probe_at,
                now + self.bootstrap_cycle_pause_seconds,
            )
        self.bootstrap_stable_since = now

    def _fast_road_takeover(self, controller: Controller, ctx: ProfileContext) -> str | None:
        if self.phase in {NfsPhase.RACING, NfsPhase.RECOVERY}:
            return None
        if self.screen is not NfsScreen.UNKNOWN:
            return None
        if self.road.confidence < self.strong_road_confidence:
            return None
        if ctx.motion < self.race_motion_threshold:
            return None
        if self.race_evidence_frames < self.strong_road_enter_frames:
            return None

        self._clear_menu_transaction()
        self.blocked_menu_screen = None
        self.drive_mode = "racer"
        self.race_entries += 1
        self.bootstrap_fast_race_entries += 1
        self._reset_bootstrap(reset_step=True)
        self._set_phase(NfsPhase.RACING, ctx.now)
        action = self._drive(controller, ctx)
        self.last_action = f"unattended fast road takeover: {action}"
        return self.last_action

    def _bootstrap_tick(self, controller: Controller, ctx: ProfileContext) -> str:
        controller.release_all()
        controller.neutral_sticks()

        if self.bootstrap_unknown_since is None:
            self.bootstrap_unknown_since = ctx.now
        unknown_age = max(0.0, ctx.now - self.bootstrap_unknown_since)

        current_fp = self._fingerprint(ctx.frame)
        if self.bootstrap_pending_action is not None:
            pending_age = max(0.0, ctx.now - self.bootstrap_pending_since)
            delta = self._fingerprint_delta(self.bootstrap_reference, current_fp)
            if delta >= self.bootstrap_progress_delta:
                completed = self.bootstrap_pending_action
                self.bootstrap_progress_acks += 1
                self.bootstrap_pending_action = None
                self.bootstrap_reference = None
                self._advance_bootstrap_step(ctx.now)
                return f"unattended bootstrap: visual progress after {completed} delta={delta:.3f}"
            if pending_age < self.bootstrap_progress_timeout_seconds:
                return (
                    f"unattended bootstrap: awaiting progress after "
                    f"{self.bootstrap_pending_action} ({pending_age:.1f}s)"
                )

            timed_out = self.bootstrap_pending_action
            self.bootstrap_timeouts += 1
            self.bootstrap_pending_action = None
            self.bootstrap_reference = None
            self._advance_bootstrap_step(ctx.now)
            return f"unattended bootstrap: {timed_out} no-op timeout; advance probe ladder"

        if ctx.now < self.bootstrap_next_probe_at:
            return "unattended bootstrap: cycle pause"

        moving_unknown = ctx.motion > self.bootstrap_motion_guard
        if moving_unknown and unknown_age < self.bootstrap_force_after_seconds:
            self.bootstrap_motion_holds += 1
            self.bootstrap_stable_since = None
            return (
                f"unattended bootstrap: observe moving unknown motion={ctx.motion:.3f}"
            )

        if self.bootstrap_stable_since is None:
            self.bootstrap_stable_since = ctx.now
            return "unattended bootstrap: establishing stable unknown screen"

        stable_age = max(0.0, ctx.now - self.bootstrap_stable_since)
        if stable_age < self.bootstrap_stable_seconds:
            return f"unattended bootstrap: stable-screen wait {stable_age:.1f}s"

        action = self.bootstrap_sequence[self.bootstrap_step]
        ready = ctx.now >= self.next_menu_action_at
        result = self._safe_menu_tap(controller, action, ctx)
        if not ready:
            return f"unattended bootstrap: {result}"

        self.bootstrap_actions += 1
        self.bootstrap_pending_action = action
        self.bootstrap_pending_since = ctx.now
        self.bootstrap_reference = current_fp
        self.bootstrap_next_probe_at = ctx.now + self.bootstrap_settle_seconds
        return (
            f"unattended bootstrap: probe {action} "
            f"step={self.bootstrap_step + 1}/{len(self.bootstrap_sequence)}"
        )

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        action = super().tick(controller, ctx)

        if self.phase is NfsPhase.RACING:
            self._reset_bootstrap(reset_step=True)
            return action

        fast = self._fast_road_takeover(controller, ctx)
        if fast is not None:
            return fast

        # Any positive semantic remains authoritative. Templates are accelerators,
        # not prerequisites; the probe ladder only owns genuinely UNKNOWN frames.
        if self.screen is not NfsScreen.UNKNOWN:
            self._reset_bootstrap(reset_step=True)
            return action

        if self.phase is NfsPhase.RECOVERY or not self.unattended_bootstrap_enabled:
            return action

        self.last_action = self._bootstrap_tick(controller, ctx)
        return self.last_action

    def recover(self, controller: Controller) -> str:
        # The shared motion watchdog can fire while sitting on an unknown menu.
        # Never arm reverse/steer racing recovery unless we actually own gameplay.
        if self.phase not in {NfsPhase.RACING, NfsPhase.RECOVERY}:
            controller.release_all()
            controller.neutral_sticks()
            controller.tap("start", 0.08)
            self.bootstrap_watchdog_kicks += 1
            return "nfs unattended bootstrap watchdog: tap start"
        return super().recover(controller)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        pending_age = None
        if self.bootstrap_pending_action is not None:
            pending_age = round(max(0.0, ctx.now - self.bootstrap_pending_since), 2)
        state.update(
            {
                "nfs_policy_version": 6,
                "nfs_unattended_bootstrap_enabled": self.unattended_bootstrap_enabled,
                "nfs_bootstrap_step": self.bootstrap_step,
                "nfs_bootstrap_pending_action": self.bootstrap_pending_action,
                "nfs_bootstrap_pending_age": pending_age,
                "nfs_bootstrap_actions": self.bootstrap_actions,
                "nfs_bootstrap_progress_acks": self.bootstrap_progress_acks,
                "nfs_bootstrap_timeouts": self.bootstrap_timeouts,
                "nfs_bootstrap_motion_holds": self.bootstrap_motion_holds,
                "nfs_bootstrap_cycles": self.bootstrap_cycles,
                "nfs_bootstrap_fast_race_entries": self.bootstrap_fast_race_entries,
                "nfs_bootstrap_watchdog_kicks": self.bootstrap_watchdog_kicks,
                "nfs_strong_road_confidence": self.strong_road_confidence,
                "nfs_strong_road_enter_frames": self.strong_road_enter_frames,
            }
        )
        return state
