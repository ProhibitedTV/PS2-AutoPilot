from __future__ import annotations

import time

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_menu import MenuHighlight, detect_menu_highlight
from ps2_autopilot.madden_runtime import MaddenRuntimeMonitor, RuntimeDirective
from ps2_autopilot.madden_vision import MaddenObservation, MaddenVisualState

from .base import ProfileContext
from .madden2005 import MaddenPhase
from .madden2005_v4 import Madden2005V4Profile


class Madden2005V5Profile(Madden2005V4Profile):
    """24/7-oriented Madden player with verified menus and semantic recovery."""

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.started_at = time.monotonic()
        self.menu_highlight: MenuHighlight | None = None
        self.runtime_monitor = MaddenRuntimeMonitor(cfg)
        self.last_progress_directive: RuntimeDirective | None = None
        self.pause_start_attempts = 0
        self.pause_resume_attempts = 0

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        old_phase = self.phase
        obs = super()._observe(ctx)
        if self.phase in {MaddenPhase.MENU, MaddenPhase.TRANSITION, MaddenPhase.GAME_OVER, MaddenPhase.PAUSED}:
            self.menu_highlight = detect_menu_highlight(ctx.frame, self.last_ocr)
        else:
            self.menu_highlight = None
        if old_phase == MaddenPhase.PAUSED and self.phase != MaddenPhase.PAUSED:
            self.pause_start_attempts = 0
            self.pause_resume_attempts = 0
        return obs

    def _menu(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        return self.menu.act(
            controller,
            self.menu_assessment,
            now,
            transition=obs.state == MaddenVisualState.TRANSITION,
            snapshot=self.last_ocr,
            highlight=self.menu_highlight,
        )

    def _paused(self, controller: Controller, now: float) -> str:
        """Get out of Madden's pause menu without wandering through its options.

        Start is the safest first choice because it toggles pause. If the game is
        still semantically paused after several attempts, Cross is used as a
        fallback for the normally highlighted Resume Game row. We never navigate
        down toward settings/quit while trying to recover.
        """

        controller.neutral_sticks()
        if now < self.next_action_at:
            return self.current_action

        paused_for = max(0.0, now - self.phase_since)
        if self.pause_start_attempts < 3 or paused_for < 5.0:
            controller.tap("start", 0.08)
            self.pause_start_attempts += 1
            self.next_action_at = now + 1.55
            self.current_action = f"pause: START resume attempt {self.pause_start_attempts}"
            return self.current_action

        controller.tap("cross", 0.08)
        self.pause_resume_attempts += 1
        self.next_action_at = now + 1.55
        self.current_action = f"pause: CROSS Resume Game fallback {self.pause_resume_attempts}"
        return self.current_action

    def _policy_tick(self, controller: Controller, ctx: ProfileContext) -> str:
        obs = self._observe(ctx)

        queued = self._run_queue(controller, ctx.now)
        if queued:
            return queued

        soft_recovery = self._soft_stall_recovery(controller, ctx.now)
        if soft_recovery:
            return soft_recovery

        if self.phase == MaddenPhase.PLAYCALL:
            return self._playcall(controller, ctx.now)
        if self.phase == MaddenPhase.KICKING:
            return self._kicking(controller, ctx.now)
        if self.phase == MaddenPhase.PRE_SNAP:
            return self._pre_snap(controller, ctx.now)
        if self.phase == MaddenPhase.LIVE:
            return self._live(controller, obs, ctx.now)
        if self.phase == MaddenPhase.POST_PLAY:
            return self._post_play(controller, ctx.now)
        if self.phase == MaddenPhase.PAUSED:
            return self._paused(controller, ctx.now)
        if self.phase == MaddenPhase.GAME_OVER:
            controller.neutral_sticks()
            if ctx.now >= self.next_action_at:
                controller.tap("cross", 0.08)
                self.next_action_at = ctx.now + 2.0
            self.current_action = "final: advance toward post-game"
            return self.current_action
        return self._menu(controller, obs, ctx.now)

    def _progress_recover(
        self,
        controller: Controller,
        directive: RuntimeDirective,
        now: float,
    ) -> str:
        level = directive.level
        if self.phase == MaddenPhase.PAUSED:
            # Never let the generic menu unwinder scroll around a pause menu.
            # Retry only the two known-safe resume mechanisms.
            return self._paused(controller, now)
        if self.phase in {MaddenPhase.MENU, MaddenPhase.TRANSITION, MaddenPhase.GAME_OVER}:
            return self.menu.request_recovery(controller, level, now)

        controller.neutral_sticks()
        if self.phase == MaddenPhase.PLAYCALL:
            controller.tap("triangle", 0.07)
            self.playcall_armed = False
            self.queue.clear()
            self.next_action_at = now + 1.2
            self.current_action = f"progress recovery L{level}: reset playcall"
        elif self.phase == MaddenPhase.PRE_SNAP:
            controller.tap("cross", 0.07)
            self.last_snap_at = now
            self.next_action_at = now + 1.5
            self.current_action = f"progress recovery L{level}: force snap probe"
        elif self.phase == MaddenPhase.POST_PLAY:
            controller.tap("cross", 0.07)
            self.next_action_at = now + 1.1
            self.current_action = f"progress recovery L{level}: advance post-play"
        else:
            controller.tap("start", 0.08)
            self.next_action_at = now + 1.5
            self.current_action = f"progress recovery L{level}: state probe"
        return self.current_action

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        action = self._policy_tick(controller, ctx)
        state = super().telemetry(ctx)
        state.update(
            {
                "menu_screen": self.menu_assessment.screen.value,
                "menu_confidence": round(self.menu_assessment.confidence, 2),
                "menu_reason": self.menu_assessment.reason,
                "ocr_text": self.last_ocr.text[:220],
                "quarter": self.situation.quarter,
                "down": self.situation.down,
            }
        )
        state.update(self.menu.telemetry())
        directive = self.runtime_monitor.update(ctx.frame, state, action, ctx.now)
        self.last_progress_directive = directive
        if directive is not None:
            action = self._progress_recover(controller, directive, ctx.now)
        return action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(self.menu.telemetry())
        state.update(self.runtime_monitor.telemetry(ctx.now))
        state["runtime_hours"] = round(max(0.0, ctx.now - self.started_at) / 3600.0, 2)
        state["pause_start_attempts"] = self.pause_start_attempts
        state["pause_resume_attempts"] = self.pause_resume_attempts
        if self.last_progress_directive is not None:
            state["progress_recovery_reason"] = self.last_progress_directive.reason
            state["progress_recovery_stalled"] = round(
                self.last_progress_directive.stalled_seconds, 1
            )
        else:
            state["progress_recovery_reason"] = None
            state["progress_recovery_stalled"] = 0.0
        return state

    def recover(self, controller: Controller) -> str:
        self.runtime_monitor.note_hard_recovery()
        return super().recover(controller)
