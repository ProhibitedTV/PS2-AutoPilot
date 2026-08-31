from __future__ import annotations

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_menu import MaddenScreen, MenuAssessment
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005 import MaddenPhase
from .madden2005_v29 import Madden2005V29Profile


class Madden2005V30Profile(Madden2005V29Profile):
    """Treat Madden's Controller Layout help page as a verified modal.

    The first V29 live restart happened while Madden was already displaying the
    in-game ``CONTROLLER LAYOUT`` help overlay. OCR read that page very cleanly,
    but no semantic rule owned it. The generic MENU soft-recovery path therefore
    emitted Cross (advance) and the profile then settled into transition/UNKNOWN.

    V30 gives this modal higher priority than queued gameplay inputs, soft-stall
    recovery, active-game UNKNOWN grace, and watchdog recovery. It uses Triangle,
    Madden 2005's normal "return to previous screen" menu action, and then waits
    for a *fresh* OCR snapshot before it can retry. A stale async OCR result can
    therefore never cause repeated Triangle presses to leak through after the
    overlay has already closed.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.controller_help_visible = False
        self.controller_help_detections = 0
        self.controller_help_dismiss_attempts = 0
        self.controller_help_verified_exits = 0
        self.controller_help_stale_snapshot_holds = 0
        self.controller_help_max_dismiss_attempts = max(
            1, min(5, int(cfg.get("controller_help_max_dismiss_attempts", 3)))
        )
        self.controller_help_retry_seconds = max(
            0.70, min(3.0, float(cfg.get("controller_help_retry_seconds", 1.25)))
        )
        self._controller_help_last_action_snapshot = None
        self._controller_help_last_seen_snapshot = None
        self._controller_help_pending_exit = False

    @classmethod
    def _looks_like_controller_help(cls, text: str | None) -> bool:
        compact = cls._compact(text)
        if "CONTROLLERLAYOUT" not in compact:
            return False
        control_markers = (
            "PASSING",
            "THROWTO",
            "THROWBALLAWAY",
            "TURNPASSINGICONSONOFF",
            "PUMPFAKE",
            "RECEIVER",
            "ACTIVETEAM",
        )
        return sum(marker in compact for marker in control_markers) >= 2

    def _own_controller_help(self, ctx: ProfileContext) -> None:
        was_visible = self.controller_help_visible
        self.controller_help_visible = True
        self._controller_help_last_seen_snapshot = self.last_ocr
        if not was_visible:
            self.controller_help_detections += 1

        self.menu_assessment = MenuAssessment(
            MaddenScreen.DIALOG,
            0.99,
            "v30 controller-layout help overlay",
        )

        # Modal ownership must beat every previously scheduled football/menu input.
        self.queue.clear()
        self.playcall_armed = False
        self.kick_armed = False
        if self.phase != MaddenPhase.MENU:
            self._set_phase_immediate(MaddenPhase.MENU, ctx.now)

        self.runtime_monitor.last_progress_at = ctx.now
        self.runtime_monitor.recovery_level = 0
        self.runtime_monitor.next_recovery_at = 0.0

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        was_visible = self.controller_help_visible
        previous_snapshot = self._controller_help_last_seen_snapshot
        obs = super()._observe(ctx)
        looks_help = self._looks_like_controller_help(self.last_ocr.text)

        if looks_help:
            self._own_controller_help(ctx)
            return obs

        # Do not clear modal ownership merely because the async OCR worker has not
        # produced a new result yet. Only a fresh post-action OCR snapshot may
        # release the latch.
        fresh_snapshot = self.last_ocr is not previous_snapshot
        if was_visible and self._controller_help_pending_exit and fresh_snapshot:
            self.controller_help_visible = False
            self._controller_help_pending_exit = False
            self._controller_help_last_action_snapshot = None
            self._controller_help_last_seen_snapshot = self.last_ocr
            self.controller_help_verified_exits += 1
        elif was_visible:
            self.controller_help_visible = True
            self.menu_assessment = MenuAssessment(
                MaddenScreen.DIALOG,
                0.99,
                "v30 controller-layout exit pending fresh OCR",
            )
            self.queue.clear()
            self.playcall_armed = False
            self.kick_armed = False
            if self.phase != MaddenPhase.MENU:
                self._set_phase_immediate(MaddenPhase.MENU, ctx.now)

        return obs

    def _controller_help(self, controller: Controller, now: float) -> str:
        controller.neutral_sticks()
        self.queue.clear()
        self.playcall_armed = False
        self.kick_armed = False
        self.runtime_monitor.last_progress_at = now
        self.runtime_monitor.recovery_level = 0
        self.runtime_monitor.next_recovery_at = 0.0

        if self._controller_help_pending_exit:
            # The same OCR object means the async worker has not yet confirmed what
            # happened after Triangle. Holding here prevents a stale help snapshot
            # from generating a second input on the underlying game screen.
            if self.last_ocr is self._controller_help_last_action_snapshot:
                self.controller_help_stale_snapshot_holds += 1
                self.current_action = "help: TRIANGLE submitted; wait for fresh OCR"
                return self.current_action

            if now < self.next_action_at:
                self.current_action = "help: post-dismiss OCR verification"
                return self.current_action

        if self.controller_help_dismiss_attempts >= self.controller_help_max_dismiss_attempts:
            self.current_action = "help: controller layout still visible; safe hold after retries"
            return self.current_action

        if now < self.next_action_at:
            return self.current_action

        controller.tap("triangle", 0.08)
        self.controller_help_dismiss_attempts += 1
        self._controller_help_last_action_snapshot = self.last_ocr
        self._controller_help_pending_exit = True
        self.next_action_at = now + self.controller_help_retry_seconds
        self.current_action = (
            "help: controller layout -> TRIANGLE return "
            f"{self.controller_help_dismiss_attempts}/{self.controller_help_max_dismiss_attempts}"
        )
        return self.current_action

    def _menu(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        if self.controller_help_visible:
            return self._controller_help(controller, now)
        return super()._menu(controller, obs, now)

    def _soft_stall_recovery(self, controller: Controller, now: float) -> str | None:
        if self.controller_help_visible:
            self.runtime_monitor.last_progress_at = now
            return None
        return super()._soft_stall_recovery(controller, now)

    def _progress_recover(self, controller: Controller, directive, now: float) -> str:
        if self.controller_help_visible:
            return self._controller_help(controller, now)
        return super()._progress_recover(controller, directive, now)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "madden_policy_version": "v30",
                "controller_help_visible": self.controller_help_visible,
                "controller_help_detections": self.controller_help_detections,
                "controller_help_dismiss_attempts": self.controller_help_dismiss_attempts,
                "controller_help_verified_exits": self.controller_help_verified_exits,
                "controller_help_stale_snapshot_holds": self.controller_help_stale_snapshot_holds,
                "controller_help_pending_exit": self._controller_help_pending_exit,
            }
        )
        return state
