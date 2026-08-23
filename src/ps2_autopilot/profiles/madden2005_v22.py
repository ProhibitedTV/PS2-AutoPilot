from __future__ import annotations

import re

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_runtime import RuntimeDirective
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005 import MaddenPhase
from .madden2005_v21 import Madden2005V21Profile


class Madden2005V22Profile(Madden2005V21Profile):
    """Dismiss Madden's EA SPORTS Bio missing-profile modal without saving.

    A long unattended v0.7.4 run completed seven games, then reached the main menu
    EA SPORTS Bio prompt shown when no Bio exists on the memory card. The modal is
    not a normal memory-card SAVE screen: its rows are Retry / Save / Cancel and
    Triangle does nothing. The inherited conservative save handler therefore sat
    forever issuing harmless-but-useless Triangle backouts.

    v22 treats this as its own lifecycle state. It recognizes the Bio wording,
    locates Madden's red selected-row bar directly from the frame, walks downward
    until CANCEL is visibly selected, and only then presses Cross. Cross is never
    allowed while SAVE is selected, so the 24/7 loop stays write-safe.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.ea_bio_modal_visible = False
        self.ea_bio_selected_row: str | None = None
        self.ea_bio_moves = 0
        self.ea_bio_cancel_confirms = 0
        self.ea_bio_unverified_probes = 0
        self.ea_bio_first_seen_at = -1e9

    @staticmethod
    def _bio_compact(text: str | None) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())

    @classmethod
    def _looks_like_ea_bio_modal(cls, text: str | None) -> bool:
        compact = cls._bio_compact(text)
        return (
            "MEMORYCARD" in compact
            and "NOEASPORTS" in compact
            and "BIOFOUND" in compact
            and "SAVEONENOW" in compact
            and "RETRY" in compact
            and "SAVE" in compact
        )

    @staticmethod
    def _detect_ea_bio_highlight(frame: np.ndarray | None) -> str | None:
        """Return Retry/Save/Cancel from the red selection bar in the Bio modal."""
        if frame is None or not isinstance(frame, np.ndarray) or frame.size == 0:
            return None

        try:
            h, w = frame.shape[:2]
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        except (ValueError, cv2.error):
            return None

        # The modal is centered. Limit color detection to its three option rows so
        # the red main-menu background and top dialog border cannot become the
        # selected-row candidate.
        x0, x1 = int(w * 0.35), int(w * 0.66)
        y0, y1 = int(h * 0.48), int(h * 0.74)
        if x1 <= x0 or y1 <= y0:
            return None

        roi = hsv[y0:y1, x0:x1]
        hue, sat, val = cv2.split(roi)
        red = (
            (sat >= 80)
            & (val >= 45)
            & ((hue <= 18) | (hue >= 168))
        ).astype(np.uint8)

        count, _, stats, centroids = cv2.connectedComponentsWithStats(red, 8)
        candidates: list[tuple[int, float]] = []
        for idx in range(1, count):
            x, y, width, height, area = [int(v) for v in stats[idx]]
            if area < max(180, int(w * h * 0.0009)):
                continue
            if width < int(w * 0.10) or width > int(w * 0.30):
                continue
            if height < int(h * 0.025) or height > int(h * 0.085):
                continue
            cx, cy = centroids[idx]
            absolute_cx = x0 + float(cx)
            absolute_cy = y0 + float(cy)
            if not (w * 0.40 <= absolute_cx <= w * 0.62):
                continue
            candidates.append((area, absolute_cy / max(1.0, float(h))))

        if not candidates:
            return None

        _, center_y = max(candidates, key=lambda item: item[0])
        if center_y < 0.575:
            return "retry"
        if center_y < 0.642:
            return "save"
        if center_y <= 0.715:
            return "cancel"
        return None

    def _reset_ea_bio_modal(self) -> None:
        self.ea_bio_modal_visible = False
        self.ea_bio_selected_row = None
        self.ea_bio_unverified_probes = 0
        self.ea_bio_first_seen_at = -1e9

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)
        visible = self._looks_like_ea_bio_modal(self.last_ocr.text)

        if visible:
            if not self.ea_bio_modal_visible:
                self.ea_bio_first_seen_at = ctx.now
                self.ea_bio_unverified_probes = 0
            self.ea_bio_modal_visible = True
            self.ea_bio_selected_row = self._detect_ea_bio_highlight(ctx.frame)

            # This modal mentions MEMORY CARD and SAVE, but it is not the profile
            # save prompt or slot picker. It must own input before save handling.
            self.save_screen_visible = False
            self.pregame_presentation = False
            self.pregame_reason = None
            self._set_phase_immediate(MaddenPhase.MENU, ctx.now)
            self.runtime_monitor.last_progress_at = ctx.now
            self.runtime_monitor.recovery_level = 0
            self.runtime_monitor.next_recovery_at = 0.0
        elif self.ea_bio_modal_visible:
            self._reset_ea_bio_modal()

        return obs

    def _ea_bio_cancel(self, controller: Controller, now: float) -> str:
        controller.neutral_sticks()
        self.queue.clear()
        self.runtime_monitor.last_progress_at = now
        self.runtime_monitor.recovery_level = 0
        self.runtime_monitor.next_recovery_at = 0.0

        if now < self.next_action_at:
            return self.current_action

        selected = self.ea_bio_selected_row
        if selected == "cancel":
            controller.tap("cross", 0.08)
            self.ea_bio_cancel_confirms += 1
            self.next_action_at = now + 2.0
            self.current_action = (
                "EA Bio: verified CANCEL highlight -> CROSS once; never choose Save"
            )
            return self.current_action

        if selected in {"retry", "save"}:
            controller.tap("down", 0.07)
            self.ea_bio_moves += 1
            self.next_action_at = now + 0.60
            self.current_action = (
                f"EA Bio: selected={selected.upper()} -> DOWN toward CANCEL "
                f"({self.ea_bio_moves})"
            )
            return self.current_action

        # Selection verification is intentionally fail-closed. One DOWN probe is
        # safe on the observed default SAVE row and gives the red-bar detector a
        # fresh position to read, but Cross remains forbidden until CANCEL is seen.
        age = max(0.0, now - self.ea_bio_first_seen_at)
        if age >= 1.0 and self.ea_bio_unverified_probes < 1:
            controller.tap("down", 0.07)
            self.ea_bio_unverified_probes += 1
            self.ea_bio_moves += 1
            self.next_action_at = now + 0.75
            self.current_action = "EA Bio: highlight unverified -> one DOWN probe; Cross locked"
            return self.current_action

        self.next_action_at = now + 0.50
        self.current_action = "EA Bio: waiting for verified row highlight; Cross locked"
        return self.current_action

    def _menu(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        if self.ea_bio_modal_visible:
            return self._ea_bio_cancel(controller, now)
        return super()._menu(controller, obs, now)

    def _soft_stall_recovery(self, controller: Controller, now: float) -> str | None:
        if self.ea_bio_modal_visible:
            return self._ea_bio_cancel(controller, now)
        return super()._soft_stall_recovery(controller, now)

    def _progress_recover(
        self,
        controller: Controller,
        directive: RuntimeDirective,
        now: float,
    ) -> str:
        if self.ea_bio_modal_visible:
            return self._ea_bio_cancel(controller, now)
        return super()._progress_recover(controller, directive, now)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "ea_bio_modal_visible": self.ea_bio_modal_visible,
                "ea_bio_selected_row": self.ea_bio_selected_row,
                "ea_bio_moves": self.ea_bio_moves,
                "ea_bio_cancel_confirms": self.ea_bio_cancel_confirms,
                "ea_bio_unverified_probes": self.ea_bio_unverified_probes,
            }
        )
        if self.ea_bio_modal_visible:
            state["raw_menu_screen"] = str(state.get("menu_screen") or "unknown")
            state["menu_screen"] = "bio_dialog"
            state["menu_reason"] = (
                "EA SPORTS Bio missing-profile modal; verify CANCEL before Cross"
            )
        return state
