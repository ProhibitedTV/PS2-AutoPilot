from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_menu import MaddenScreen, MenuAssessment

from .base import ProfileContext
from .madden2005 import MaddenPhase
from .madden2005_v31 import Madden2005V31Profile


class Madden2005V32Profile(Madden2005V31Profile):
    """Make live play-call reacquisition theme-agnostic and quarter breaks input-safe.

    A V31 live bundle from Bills/Jets exposed two gaps that did not exist in the
    earlier Miami calibration footage:

    * Madden's play-call chrome may use a neutral gray left control panel plus a
      dark team-colored diagram panel instead of the saturated red panel used to
      calibrate V29. The geometry is the same, but V29's red-only signature misses
      it and can leave an obvious play-call screen inside active-game ambiguity.
    * END OF 3RD QUARTER was correctly recognized as presentation while its OCR
      marker remained readable, but once the following cheerleader/crowd shot lost
      that text the policy could fall back to generic MENU recovery and emit
      Triangle during broadcast presentation.

    V32 keeps the proven V29 red signature and adds a second neutral-panel variant.
    It also latches explicit Q1/Q2/Q3/halftime presentation until a real play-call,
    kicking screen, controller-help modal, root/final menu, or a bounded timeout
    proves that presentation ownership should end.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.playcall_visual_neutral_ratio = max(
            0.55, min(0.92, float(cfg.get("playcall_visual_neutral_ratio", 0.70)))
        )
        self.visual_playcall_left_neutral_ratio = 0.0
        self.visual_playcall_variant = "none"
        self.theme_playcall_reacquisitions = 0

        self.quarter_break_max_hold_seconds = max(
            20.0, min(90.0, float(cfg.get("quarter_break_max_hold_seconds", 45.0)))
        )
        self.quarter_break_active = False
        self.quarter_break_reason: str | None = None
        self.quarter_break_started_at = -1e9
        self.quarter_break_detections = 0
        self.quarter_break_hold_ticks = 0
        self.quarter_break_releases = 0
        self.quarter_break_timeouts = 0
        self._quarter_break_owns_presentation = False

    @staticmethod
    def _neutral_playcall_left_ratio(frame: np.ndarray) -> float:
        if frame is None or frame.size == 0 or frame.ndim != 3:
            return 0.0
        h, w = frame.shape[:2]
        if h < 120 or w < 200:
            return 0.0

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        y0 = int(h * 0.64)
        y1 = max(y0 + 1, int(h * 0.96))
        split = int(w * 0.39)
        left = hsv[y0:y1, :split]
        if left.size == 0:
            return 0.0

        _, sat, val = cv2.split(left)
        # The Bills/Jets live playbook used a broad flat gray control panel. Keep
        # this deliberately separate from the legacy saturated-red detector so a
        # generic dark broadcast shot cannot satisfy the new signature by itself.
        neutral_mid = (sat < 60) & (val > 80) & (val < 230)
        return float(neutral_mid.mean())

    def _looks_like_visual_playcall(self, frame: np.ndarray) -> bool:
        red, right_dark = self._playcall_chrome_metrics(frame)
        neutral = self._neutral_playcall_left_ratio(frame)
        self.visual_playcall_red_ratio = red
        self.visual_playcall_right_dark_ratio = right_dark
        self.visual_playcall_left_neutral_ratio = neutral

        legacy_red = (
            red >= self.playcall_visual_red_ratio
            and right_dark >= self.playcall_visual_right_dark_ratio
        )
        neutral_panel = (
            neutral >= self.playcall_visual_neutral_ratio
            and right_dark >= self.playcall_visual_right_dark_ratio
        )
        if legacy_red:
            self.visual_playcall_variant = "red"
        elif neutral_panel:
            self.visual_playcall_variant = "neutral"
        else:
            self.visual_playcall_variant = "none"
        return legacy_red or neutral_panel

    def _apply_visual_playcall_reacquisition(self, now: float) -> None:
        before = self.visual_playcall_reacquisitions
        variant = self.visual_playcall_variant
        super()._apply_visual_playcall_reacquisition(now)
        if self.visual_playcall_reacquisitions > before and variant == "neutral":
            self.theme_playcall_reacquisitions += 1
        self.menu_assessment = MenuAssessment(
            MaddenScreen.PLAYCALL,
            0.94,
            (
                f"v32 {variant} visual playcall chrome; "
                f"red={self.visual_playcall_red_ratio:.2f}, "
                f"neutral={self.visual_playcall_left_neutral_ratio:.2f}, "
                f"right_dark={self.visual_playcall_right_dark_ratio:.2f}"
            ),
        )

    @classmethod
    def _quarter_break_marker(cls, text: str | None) -> str | None:
        compact = cls._compact(text)
        markers = (
            ("ENDOF1STQUARTER", "end of 1st quarter"),
            ("END1STQUARTER", "end of 1st quarter"),
            ("ENDOF2NDQUARTER", "end of 2nd quarter"),
            ("END2NDQUARTER", "end of 2nd quarter"),
            ("HALFTIME", "halftime"),
            ("ENDOFHALF", "halftime"),
            ("ENDOF3RDQUARTER", "end of 3rd quarter"),
            ("END3RDQUARTER", "end of 3rd quarter"),
        )
        for marker, reason in markers:
            if marker in compact:
                return reason
        return None

    def _activate_quarter_break(self, reason: str, now: float) -> None:
        if not self.quarter_break_active:
            self.quarter_break_detections += 1
            self.quarter_break_started_at = now
        self.quarter_break_active = True
        self.quarter_break_reason = reason

    def _quarter_break_resume_visible(self) -> bool:
        if self.controller_help_visible:
            return True
        if self.visual_playcall_signature:
            return True
        if self.menu_assessment.screen in {
            MaddenScreen.PLAYCALL,
            MaddenScreen.KICKING,
            MaddenScreen.PAUSED,
            MaddenScreen.FINAL,
            MaddenScreen.TITLE,
            MaddenScreen.MAIN_MENU,
        }:
            return True
        return self._looks_like_coin_or_playcall(self.last_ocr.text)

    def _clear_quarter_break(self, *, timeout: bool = False) -> None:
        if self.quarter_break_active:
            if timeout:
                self.quarter_break_timeouts += 1
            else:
                self.quarter_break_releases += 1
        self.quarter_break_active = False
        self.quarter_break_reason = None
        self.quarter_break_started_at = -1e9
        if self._quarter_break_owns_presentation:
            self.pregame_presentation = False
            if str(self.pregame_reason or "").startswith("quarter break:"):
                self.pregame_reason = None
            self._quarter_break_owns_presentation = False

    def _own_quarter_break(self, now: float) -> None:
        self.pregame_presentation = True
        self.pregame_reason = f"quarter break: {self.quarter_break_reason or 'presentation'}"
        self._quarter_break_owns_presentation = True
        self.queue.clear()
        self.playcall_armed = False
        self.kick_armed = False
        if self.phase not in {MaddenPhase.GAME_OVER, MaddenPhase.PAUSED}:
            self._set_phase_immediate(MaddenPhase.TRANSITION, now)
        self.runtime_monitor.last_progress_at = now
        self.runtime_monitor.recovery_level = 0
        self.runtime_monitor.next_recovery_at = 0.0

    def _observe(self, ctx: ProfileContext):
        obs = super()._observe(ctx)
        marker = self._quarter_break_marker(self.last_ocr.text)
        if marker is not None:
            self._activate_quarter_break(marker, ctx.now)

        if not self.quarter_break_active:
            return obs

        if self._quarter_break_resume_visible():
            self._clear_quarter_break()
            return obs

        age = max(0.0, ctx.now - self.quarter_break_started_at)
        if age > self.quarter_break_max_hold_seconds:
            self._clear_quarter_break(timeout=True)
            return obs

        self._own_quarter_break(ctx.now)
        return obs

    def _pregame_hold(self, controller: Controller, now: float) -> str:
        action = super()._pregame_hold(controller, now)
        if not self.quarter_break_active:
            return action
        self.quarter_break_hold_ticks += 1
        self.current_action = (
            f"presentation: {self.quarter_break_reason or 'quarter break'}; hold inputs"
        )
        return self.current_action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "madden_policy_version": "v32",
                "visual_playcall_variant": self.visual_playcall_variant,
                "visual_playcall_left_neutral_ratio": round(
                    self.visual_playcall_left_neutral_ratio, 3
                ),
                "playcall_visual_neutral_ratio": round(
                    self.playcall_visual_neutral_ratio, 3
                ),
                "theme_playcall_reacquisitions": self.theme_playcall_reacquisitions,
                "quarter_break_active": self.quarter_break_active,
                "quarter_break_reason": self.quarter_break_reason,
                "quarter_break_age": (
                    round(max(0.0, ctx.now - self.quarter_break_started_at), 1)
                    if self.quarter_break_started_at > -1e8
                    else None
                ),
                "quarter_break_detections": self.quarter_break_detections,
                "quarter_break_hold_ticks": self.quarter_break_hold_ticks,
                "quarter_break_releases": self.quarter_break_releases,
                "quarter_break_timeouts": self.quarter_break_timeouts,
            }
        )
        if self.quarter_break_active:
            state["raw_menu_screen"] = str(state.get("menu_screen") or "unknown")
            state["menu_screen"] = "presentation"
            state["menu_reason"] = (
                f"v32 latched {self.quarter_break_reason or 'quarter-break'} presentation"
            )
            state["navigation_unknown_suppressed"] = True
        return state
