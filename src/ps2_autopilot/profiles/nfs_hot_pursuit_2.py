from __future__ import annotations

from enum import Enum
import math
import time

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.nfs_hp2_vision import RoadObservation, estimate_road

from .base import GameProfile, ProfileContext


class NfsPhase(str, Enum):
    CALIBRATION = "calibration"
    TITLE = "title"
    MAIN_MENU = "main_menu"
    RACE_SETUP = "race_setup"
    LOADING = "loading"
    COUNTDOWN = "countdown"
    RACING = "racing"
    RECOVERY = "recovery"
    RESULTS = "results"


class NfsHotPursuit2V1Profile(GameProfile):
    """Initial autonomous racing policy for Need for Speed: Hot Pursuit 2.

    V1 intentionally separates two confidence domains:

    * menus only receive input when a captured template is recognized;
    * racing may be entered from repeated high-confidence road observations, which
      lets an operator manually reach a race before the menu template set exists.

    Unknown screens therefore fail closed instead of receiving random confirms.
    """

    name = "nfs_hot_pursuit_2"

    def __init__(self, cfg: dict) -> None:
        self.cfg = dict(cfg)
        self.phase = NfsPhase.CALIBRATION
        self.phase_since = 0.0
        self.last_action = "boot"
        self.next_menu_action_at = 0.0
        self.results_visible_since: float | None = None

        self.template_threshold = float(cfg.get("template_threshold", 0.84))
        self.menu_action_seconds = max(0.35, float(cfg.get("menu_action_seconds", 1.0)))
        self.results_hold_seconds = max(1.0, float(cfg.get("results_hold_seconds", 5.0)))

        self.sample_width = max(180, int(cfg.get("road_sample_width", 360)))
        self.road_color_distance = max(12.0, float(cfg.get("road_color_distance", 42.0)))
        self.race_enter_confidence = float(cfg.get("race_enter_confidence", 0.56))
        self.drive_confidence = float(cfg.get("drive_confidence", 0.34))
        self.race_enter_frames = max(1, int(cfg.get("race_enter_frames", 5)))
        self.race_motion_threshold = max(0.0, float(cfg.get("race_motion_threshold", 0.006)))
        self.road_loss_recovery_seconds = max(
            0.5, float(cfg.get("road_loss_recovery_seconds", 2.2))
        )

        self.steering_gain = float(cfg.get("steering_gain", 1.05))
        self.curvature_gain = float(cfg.get("curvature_gain", 0.72))
        self.steering_smoothing = max(0.0, min(0.95, float(cfg.get("steering_smoothing", 0.42))))
        self.max_steer = max(0.25, min(1.0, float(cfg.get("max_steer", 0.95))))
        self.corner_brake_threshold = max(
            0.40, min(1.0, float(cfg.get("corner_brake_threshold", 0.84)))
        )
        self.corner_brake_interval_seconds = max(
            0.3, float(cfg.get("corner_brake_interval_seconds", 0.85))
        )

        # Default PS2 controls for HP2. Keeping these configurable makes calibration
        # independent of a user's PCSX2 remap.
        self.accelerate_action = str(cfg.get("accelerate_action", "cross"))
        self.brake_action = str(cfg.get("brake_action", "square"))

        self.recovery_reverse_seconds = max(
            0.25, float(cfg.get("recovery_reverse_seconds", 0.70))
        )
        self.recovery_forward_seconds = max(
            0.25, float(cfg.get("recovery_forward_seconds", 0.85))
        )

        self.road = RoadObservation.unavailable()
        self.race_evidence_frames = 0
        self.road_lost_since: float | None = None
        self.last_steer = 0.0
        self.last_brake_at = -1e9
        self.race_entries = 0
        self.recoveries = 0
        self.recovery_reason: str | None = None
        self.recovery_started_at = -1e9
        self.last_template: str | None = None
        self.last_template_score = 0.0

    @staticmethod
    def _clamp(value: float, limit: float = 1.0) -> float:
        return max(-limit, min(limit, float(value)))

    def _set_phase(self, phase: NfsPhase, now: float) -> None:
        if phase is self.phase:
            return
        self.phase = phase
        self.phase_since = now
        if phase is not NfsPhase.RESULTS:
            self.results_visible_since = None

    @staticmethod
    def _template_semantic(name: str | None) -> str | None:
        if not name:
            return None
        normalized = name.strip().lower().replace("-", "_").replace(" ", "_")
        if "wrong_way" in normalized:
            return "wrong_way"
        if "results" in normalized or "race_finish" in normalized or "finish_screen" in normalized:
            return "results"
        if "countdown" in normalized or normalized.endswith("_go"):
            return "countdown"
        if "loading" in normalized:
            return "loading"
        if "race_hud" in normalized or "gameplay" in normalized:
            return "racing"
        if "race_setup" in normalized or "event_select" in normalized or "car_select" in normalized:
            return "race_setup"
        if "main_menu" in normalized or "quick_race" in normalized:
            return "main_menu"
        if "press_start" in normalized or "title" in normalized:
            return "title"
        return None

    def _recognized_template(self, ctx: ProfileContext) -> tuple[str | None, str | None]:
        template = ctx.template
        if template is None or template.score < self.template_threshold:
            self.last_template = None
            self.last_template_score = 0.0
            return None, None
        self.last_template = template.name
        self.last_template_score = float(template.score)
        return template.name, self._template_semantic(template.name)

    def _observe_road(self, ctx: ProfileContext) -> None:
        self.road = estimate_road(
            ctx.frame,
            sample_width=self.sample_width,
            color_distance=self.road_color_distance,
        )
        race_like = (
            self.road.confidence >= self.race_enter_confidence
            and ctx.motion >= self.race_motion_threshold
        )
        if race_like:
            self.race_evidence_frames += 1
        else:
            self.race_evidence_frames = max(0, self.race_evidence_frames - 1)

    def _safe_menu_tap(self, controller: Controller, action: str, ctx: ProfileContext) -> str:
        controller.release_all()
        controller.neutral_sticks()
        if ctx.now < self.next_menu_action_at:
            return f"{self.phase.value}: waiting"
        controller.tap(action, 0.08)
        self.next_menu_action_at = ctx.now + self.menu_action_seconds
        return f"{self.phase.value}: tap {action}"

    def _start_recovery(self, controller: Controller, ctx: ProfileContext, reason: str) -> str:
        controller.release_all()
        controller.neutral_sticks()
        self.recoveries += 1
        self.recovery_reason = reason
        self.recovery_started_at = ctx.now
        self._set_phase(NfsPhase.RECOVERY, ctx.now)
        self.road_lost_since = None
        self.last_action = f"recovery start: {reason}"
        return self.last_action

    def _tick_recovery(self, controller: Controller, ctx: ProfileContext) -> str:
        age = max(0.0, ctx.now - self.recovery_started_at)
        direction = -math.copysign(0.82, self.last_steer) if abs(self.last_steer) > 0.08 else 0.82

        if age < self.recovery_reverse_seconds:
            controller.release(self.accelerate_action)
            controller.hold(self.brake_action)
            controller.set_left_stick(direction, 0.0)
            return f"recovery reverse: {self.recovery_reason or 'road loss'}"

        if age < self.recovery_reverse_seconds + self.recovery_forward_seconds:
            controller.release(self.brake_action)
            controller.hold(self.accelerate_action)
            controller.set_left_stick(-direction * 0.72, 0.0)
            return f"recovery forward: {self.recovery_reason or 'road loss'}"

        controller.release_all()
        controller.neutral_sticks()
        self.recovery_reason = None
        if self.road.confidence >= self.drive_confidence:
            self._set_phase(NfsPhase.RACING, ctx.now)
            return "recovery complete: road reacquired"
        self._set_phase(NfsPhase.CALIBRATION, ctx.now)
        return "recovery complete: awaiting road/template evidence"

    def _drive(self, controller: Controller, ctx: ProfileContext) -> str:
        if self.road.confidence < self.drive_confidence:
            controller.release(self.accelerate_action)
            controller.neutral_sticks()
            if self.road_lost_since is None:
                self.road_lost_since = ctx.now
            if ctx.now - self.road_lost_since >= self.road_loss_recovery_seconds:
                return self._start_recovery(controller, ctx, "road confidence lost")
            return f"racing: coast road_conf={self.road.confidence:.2f}"

        self.road_lost_since = None
        raw = self.steering_gain * self.road.center_x + self.curvature_gain * self.road.curvature
        raw = self._clamp(raw, self.max_steer)
        alpha = 1.0 - self.steering_smoothing
        steer = self.last_steer * self.steering_smoothing + raw * alpha
        steer = self._clamp(steer, self.max_steer)
        self.last_steer = steer

        controller.hold(self.accelerate_action)
        controller.set_left_stick(steer, 0.0)

        braking = False
        if (
            abs(steer) >= self.corner_brake_threshold
            and ctx.now - self.last_brake_at >= self.corner_brake_interval_seconds
        ):
            controller.release(self.accelerate_action)
            controller.tap(self.brake_action, 0.055)
            controller.hold(self.accelerate_action)
            self.last_brake_at = ctx.now
            braking = True

        return (
            f"racing: steer={steer:+.2f} road={self.road.confidence:.2f}"
            + (" corner-brake" if braking else "")
        )

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        if self.phase_since == 0.0:
            self.phase_since = ctx.now

        _, semantic = self._recognized_template(ctx)
        self._observe_road(ctx)

        if self.phase is NfsPhase.RECOVERY:
            self.last_action = self._tick_recovery(controller, ctx)
            return self.last_action

        if semantic == "wrong_way" and self.phase is NfsPhase.RACING:
            return self._start_recovery(controller, ctx, "wrong-way HUD")

        if semantic == "results":
            self._set_phase(NfsPhase.RESULTS, ctx.now)
        elif semantic == "loading":
            self._set_phase(NfsPhase.LOADING, ctx.now)
        elif semantic == "countdown":
            self._set_phase(NfsPhase.COUNTDOWN, ctx.now)
        elif semantic == "racing":
            if self.phase is not NfsPhase.RACING:
                self.race_entries += 1
            self._set_phase(NfsPhase.RACING, ctx.now)
        elif semantic == "race_setup":
            self._set_phase(NfsPhase.RACE_SETUP, ctx.now)
        elif semantic == "main_menu":
            self._set_phase(NfsPhase.MAIN_MENU, ctx.now)
        elif semantic == "title":
            self._set_phase(NfsPhase.TITLE, ctx.now)
        elif self.race_evidence_frames >= self.race_enter_frames:
            if self.phase is not NfsPhase.RACING:
                self.race_entries += 1
            self._set_phase(NfsPhase.RACING, ctx.now)

        if self.phase is NfsPhase.RACING:
            self.last_action = self._drive(controller, ctx)
            return self.last_action

        if self.phase is NfsPhase.TITLE:
            self.last_action = self._safe_menu_tap(controller, "start", ctx)
            return self.last_action

        if self.phase in {NfsPhase.MAIN_MENU, NfsPhase.RACE_SETUP}:
            self.last_action = self._safe_menu_tap(controller, "confirm", ctx)
            return self.last_action

        if self.phase is NfsPhase.RESULTS:
            controller.release_all()
            controller.neutral_sticks()
            if self.results_visible_since is None:
                self.results_visible_since = ctx.now
            if ctx.now - self.results_visible_since >= self.results_hold_seconds:
                self.last_action = self._safe_menu_tap(controller, "confirm", ctx)
            else:
                self.last_action = "results: preserve presentation"
            return self.last_action

        if self.phase in {NfsPhase.LOADING, NfsPhase.COUNTDOWN}:
            controller.release_all()
            controller.neutral_sticks()
            self.last_action = f"{self.phase.value}: preserve presentation"
            return self.last_action

        # No safe semantic. V1 deliberately does nothing here. The operator can
        # manually reach a race while building the initial menu-template corpus.
        controller.release_all()
        controller.neutral_sticks()
        self.last_action = "calibration: fail-closed awaiting template or road evidence"
        return self.last_action

    def recover(self, controller: Controller) -> str:
        # The shared watchdog may call recover without a ProfileContext. Do not use
        # the generic Start/Confirm/Cancel sequence in a racing game; neutralize the
        # pad and hand ownership back to the profile's timed recovery on the next tick.
        controller.release_all()
        controller.neutral_sticks()
        self.recoveries += 1
        self.recovery_reason = "shared motion watchdog"
        self.recovery_started_at = time.monotonic()
        self.phase = NfsPhase.RECOVERY
        return "nfs recovery armed: shared motion watchdog"

    def telemetry(self, ctx: ProfileContext) -> dict:
        return {
            "nfs_phase": self.phase.value,
            "nfs_phase_seconds": round(max(0.0, ctx.now - self.phase_since), 2),
            "nfs_road_confidence": round(self.road.confidence, 3),
            "nfs_road_center_x": round(self.road.center_x, 3),
            "nfs_road_curvature": round(self.road.curvature, 3),
            "nfs_road_width": round(self.road.width, 3),
            "nfs_road_coverage": round(self.road.coverage, 3),
            "nfs_road_center_contact": round(self.road.center_contact, 3),
            "nfs_steer": round(self.last_steer, 3),
            "nfs_race_evidence_frames": self.race_evidence_frames,
            "nfs_race_entries": self.race_entries,
            "nfs_recoveries": self.recoveries,
            "nfs_recovery_reason": self.recovery_reason,
            "nfs_template": self.last_template,
            "nfs_template_score": round(self.last_template_score, 3),
        }
