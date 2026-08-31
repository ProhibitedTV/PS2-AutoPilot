from __future__ import annotations

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .nfs_hot_pursuit_2 import NfsPhase
from .nfs_hot_pursuit_2_v2 import NfsScreen
from .nfs_hot_pursuit_2_v4 import NfsHotPursuit2V4Profile


class NfsHotPursuit2V5Profile(NfsHotPursuit2V4Profile):
    """Traffic-stable pursuit and shortcut policy for the PS2 game.

    V5 keeps V4's conservative rule that a single image-only obstacle candidate
    cannot steer the car. When image obstacle avoidance is explicitly enabled, a
    candidate must now persist for several spatially consistent frames before it
    owns steering, and its chosen pass side is latched briefly so jitter cannot make
    the car weave left/right around traffic.

    The PS2 tracks also contain many useful shortcuts and Hot Pursuit events can
    include deliberate police ramming. Both are handled through positively calibrated
    directional template labels. Shortcuts preserve the underlying speed controller;
    police-attack templates are higher priority and use the same coast-first bounded
    avoidance path as roadblocks/spike strips.
    """

    name = "nfs_hot_pursuit_2"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        # Temporal traffic / obstacle confirmation. This is only actionable when the
        # existing obstacle_avoid_enabled opt-in is true.
        self.hazard_confirm_frames = max(2, int(cfg.get("hazard_confirm_frames", 3)))
        self.hazard_center_tolerance = max(
            0.08, min(0.80, float(cfg.get("hazard_center_tolerance", 0.28)))
        )
        self.hazard_track_smoothing = max(
            0.0, min(0.95, float(cfg.get("hazard_track_smoothing", 0.55)))
        )
        self.hazard_release_seconds = max(
            0.10, float(cfg.get("hazard_release_seconds", 0.45))
        )
        self.hazard_avoid_hold_seconds = max(
            0.15, float(cfg.get("hazard_avoid_hold_seconds", 0.75))
        )

        self.hazard_track_streak = 0
        self.hazard_track_confirmed = False
        self.hazard_track_center_x = 0.0
        self.hazard_track_proximity = 0.0
        self.hazard_track_last_seen = -1e9
        self.hazard_track_direction = 0.0
        self.hazard_track_until = -1e9
        self.hazard_track_confirmations = 0
        self.hazard_track_releases = 0
        self.hazard_track_restarts = 0

        # HP2 shortcut ownership. No generic pixel heuristic can activate this: the
        # local template name must encode the safe direction.
        self.shortcut_enabled = bool(cfg.get("shortcut_enabled", True))
        self.shortcut_hold_seconds = max(0.20, float(cfg.get("shortcut_hold_seconds", 1.45)))
        self.shortcut_strength = max(0.05, min(0.95, float(cfg.get("shortcut_strength", 0.52))))
        self.shortcut_blend = max(0.10, min(1.0, float(cfg.get("shortcut_blend", 0.62))))
        self.shortcut_cooldown_seconds = max(
            0.5, float(cfg.get("shortcut_cooldown_seconds", 3.5))
        )
        self.shortcut_until = -1e9
        self.shortcut_bias = 0.0
        self.shortcut_label: str | None = None
        self.last_shortcut_started_at = -1e9
        self.shortcut_events = 0
        self.shortcut_ticks = 0
        self.shortcut_suppressed_ticks = 0

        # Pursuit-racer anti-ram templates. A threat on the left means evade right.
        self.pursuit_evasion_enabled = bool(cfg.get("pursuit_evasion_enabled", True))
        self.pursuit_evasion_hold_seconds = max(
            0.20, float(cfg.get("pursuit_evasion_hold_seconds", 0.85))
        )
        self.pursuit_evasion_strength = max(
            0.10, min(1.0, float(cfg.get("pursuit_evasion_strength", 0.72)))
        )
        self.pursuit_threat_until = -1e9
        self.pursuit_threat_bias = 0.0
        self.pursuit_threat_kind: str | None = None
        self.pursuit_threat_events = 0
        self.pursuit_evasion_ticks = 0

    @classmethod
    def _screen_from_template(cls, name: str | None) -> NfsScreen:
        n = cls._norm(name)
        if any(
            marker in n
            for marker in (
                "shortcut_enter_",
                "shortcut_take_",
                "shortcut_commit_",
                "shortcut_exit_",
                "police_ram_",
                "cop_ram_",
                "police_attack_",
                "pursuit_attack_",
                "police_box_",
            )
        ):
            return NfsScreen.RACING
        return super()._screen_from_template(name)

    @staticmethod
    def _direction_from_label(name: str) -> float:
        if "_left" in name:
            return -1.0
        if "_right" in name:
            return 1.0
        return 0.0

    def _update_hazard_track(self, ctx: ProfileContext) -> None:
        candidate = (
            self.hazard.confidence >= self.hazard_confidence_threshold
            and self.hazard.proximity >= self.hazard_proximity_threshold
        )

        if candidate:
            consistent = (
                ctx.now - self.hazard_track_last_seen <= self.hazard_release_seconds
                and self.hazard_track_streak > 0
                and abs(self.hazard.center_x - self.hazard_track_center_x)
                <= self.hazard_center_tolerance
            )
            if consistent:
                self.hazard_track_streak += 1
                keep = self.hazard_track_smoothing
                self.hazard_track_center_x = (
                    self.hazard_track_center_x * keep + self.hazard.center_x * (1.0 - keep)
                )
                self.hazard_track_proximity = (
                    self.hazard_track_proximity * keep + self.hazard.proximity * (1.0 - keep)
                )
            else:
                if self.hazard_track_streak > 0:
                    self.hazard_track_restarts += 1
                self.hazard_track_streak = 1
                self.hazard_track_center_x = self.hazard.center_x
                self.hazard_track_proximity = self.hazard.proximity

            self.hazard_track_last_seen = ctx.now
            if self.hazard_track_streak >= self.hazard_confirm_frames:
                if not self.hazard_track_confirmed:
                    self.hazard_track_confirmations += 1
                self.hazard_track_confirmed = True
                if abs(self.hazard_track_center_x) >= 0.10:
                    desired = -1.0 if self.hazard_track_center_x > 0.0 else 1.0
                else:
                    desired = self._fallback_avoid_direction()

                # Hold the selected pass side for a short interval. A strong object
                # relocation after the latch expires can establish a new track.
                if ctx.now > self.hazard_track_until or abs(self.hazard_track_direction) < 0.5:
                    self.hazard_track_direction = desired
                self.hazard_track_until = ctx.now + self.hazard_avoid_hold_seconds
            return

        if (
            self.hazard_track_streak > 0
            and ctx.now - self.hazard_track_last_seen > self.hazard_release_seconds
        ):
            if self.hazard_track_confirmed:
                self.hazard_track_releases += 1
            self.hazard_track_streak = 0
            self.hazard_track_confirmed = False
            self.hazard_track_center_x = 0.0
            self.hazard_track_proximity = 0.0
            self.hazard_track_direction = 0.0
            self.hazard_track_until = -1e9

    def _observe_v5_templates(self, ctx: ProfileContext) -> None:
        if ctx.template is None or ctx.template.score < self.template_threshold:
            return
        n = self._norm(ctx.template.name)

        if "shortcut_cancel" in n or "shortcut_abort" in n:
            self.shortcut_until = -1e9
            self.shortcut_bias = 0.0
            self.shortcut_label = None

        if self.shortcut_enabled and "shortcut_" in n:
            direction = self._direction_from_label(n)
            owns_shortcut = any(
                marker in n
                for marker in (
                    "shortcut_enter_",
                    "shortcut_take_",
                    "shortcut_commit_",
                    "shortcut_exit_",
                )
            )
            cooldown_ready = (
                ctx.now - self.last_shortcut_started_at >= self.shortcut_cooldown_seconds
                or ctx.now <= self.shortcut_until
            )
            if owns_shortcut and abs(direction) > 0.5 and cooldown_ready:
                fresh = ctx.now > self.shortcut_until or self.shortcut_label != n
                if fresh:
                    self.shortcut_events += 1
                    self.last_shortcut_started_at = ctx.now
                self.shortcut_label = n
                self.shortcut_bias = direction
                self.shortcut_until = ctx.now + self.shortcut_hold_seconds

        if not self.pursuit_evasion_enabled:
            return
        threat_markers = (
            "police_ram_",
            "cop_ram_",
            "police_attack_",
            "pursuit_attack_",
            "police_box_",
        )
        if not any(marker in n for marker in threat_markers):
            return
        side = self._direction_from_label(n)
        if abs(side) < 0.5:
            return

        # Threat on left => move right, threat on right => move left.
        bias = -side
        kind = "police_ram" if "ram_" in n else "police_attack"
        if ctx.now > self.pursuit_threat_until or kind != self.pursuit_threat_kind:
            self.pursuit_threat_events += 1
        self.pursuit_threat_kind = kind
        self.pursuit_threat_bias = bias
        self.pursuit_threat_until = ctx.now + self.pursuit_evasion_hold_seconds

    def _observe_hazard(self, ctx: ProfileContext) -> None:
        super()._observe_hazard(ctx)
        self._update_hazard_track(ctx)
        self._observe_v5_templates(ctx)

    def _hazard_bias(self, now: float) -> tuple[float, float, str | None]:
        # Calibrated roadblock/spike/helicopter direction stays highest priority.
        if self.template_hazard_avoid_enabled and now <= self.template_hazard_until:
            direction = self.template_hazard_bias
            if abs(direction) < 0.5:
                direction = self._fallback_avoid_direction()
            return direction, self.template_hazard_strength, self.template_hazard_kind

        if self.pursuit_evasion_enabled and now <= self.pursuit_threat_until:
            self.pursuit_evasion_ticks += 1
            return (
                self.pursuit_threat_bias,
                self.pursuit_evasion_strength,
                self.pursuit_threat_kind or "police_attack",
            )

        # V5 deliberately replaces V4's single-frame vision ownership with temporal
        # confirmation plus a latched pass side.
        if (
            self.obstacle_avoid_enabled
            and self.hazard_track_confirmed
            and now <= self.hazard_track_until
            and abs(self.hazard_track_direction) >= 0.5
        ):
            severity = min(
                1.0,
                max(self.hazard.confidence, self.hazard_confidence_threshold)
                * max(0.35, self.hazard_track_proximity)
                / max(0.01, self.hazard_confidence_threshold),
            )
            return (
                self.hazard_track_direction,
                self.hazard_steer_gain * severity,
                "vision",
            )

        return 0.0, 0.0, None

    def _shortcut_active(self, now: float) -> bool:
        return (
            self.shortcut_enabled
            and now <= self.shortcut_until
            and abs(self.shortcut_bias) >= 0.5
        )

    def _drive(self, controller: Controller, ctx: ProfileContext) -> str:
        action = super()._drive(controller, ctx)
        if self.phase is not NfsPhase.RACING or self.road.confidence < self.drive_confidence:
            return action

        # Never let a shortcut bias fight a roadblock, confirmed traffic avoidance,
        # or a police ram escape. Those are immediate safety/pursuit concerns.
        _, _, hazard_source = self._hazard_bias(ctx.now)
        if hazard_source is not None:
            if self._shortcut_active(ctx.now):
                self.shortcut_suppressed_ticks += 1
            return action

        if not self._shortcut_active(ctx.now):
            return action

        target = self._clamp(
            self.last_steer + self.shortcut_bias * self.shortcut_strength,
            self.max_steer,
        )
        steer = self._clamp(
            self.last_steer * (1.0 - self.shortcut_blend) + target * self.shortcut_blend,
            self.max_steer,
        )
        self.last_steer = steer
        controller.set_left_stick(steer, 0.0)
        self.shortcut_ticks += 1
        return f"{action} shortcut={self.shortcut_label or 'directional'} steer={steer:+.2f}"

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "nfs_policy_version": 5,
                "nfs_hazard_track_streak": self.hazard_track_streak,
                "nfs_hazard_track_confirmed": self.hazard_track_confirmed,
                "nfs_hazard_track_center_x": round(self.hazard_track_center_x, 3),
                "nfs_hazard_track_proximity": round(self.hazard_track_proximity, 3),
                "nfs_hazard_track_direction": round(self.hazard_track_direction, 2),
                "nfs_hazard_track_confirmations": self.hazard_track_confirmations,
                "nfs_hazard_track_releases": self.hazard_track_releases,
                "nfs_hazard_track_restarts": self.hazard_track_restarts,
                "nfs_shortcut_active": self._shortcut_active(ctx.now),
                "nfs_shortcut_label": self.shortcut_label,
                "nfs_shortcut_events": self.shortcut_events,
                "nfs_shortcut_ticks": self.shortcut_ticks,
                "nfs_shortcut_suppressed_ticks": self.shortcut_suppressed_ticks,
                "nfs_pursuit_threat_kind": self.pursuit_threat_kind,
                "nfs_pursuit_threat_active": ctx.now <= self.pursuit_threat_until,
                "nfs_pursuit_threat_events": self.pursuit_threat_events,
                "nfs_pursuit_evasion_ticks": self.pursuit_evasion_ticks,
            }
        )
        return state
