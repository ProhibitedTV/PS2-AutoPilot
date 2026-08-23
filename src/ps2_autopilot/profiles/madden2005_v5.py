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

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)
        if self.phase in {MaddenPhase.MENU, MaddenPhase.TRANSITION, MaddenPhase.GAME_OVER}:
            self.menu_highlight = detect_menu_highlight(ctx.frame, self.last_ocr)
        else:
            self.menu_highlight = None
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

    def _progress_recover(
        self,
        controller: Controller,
        directive: RuntimeDirective,
        now: float,
    ) -> str:
        level = directive.level
        if self.phase in {MaddenPhase.MENU, MaddenPhase.TRANSITION, MaddenPhase.PAUSED, MaddenPhase.GAME_OVER}:
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
        action = super().tick(controller, ctx)
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
