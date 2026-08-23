from __future__ import annotations

import re

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_menu import MaddenScreen
from ps2_autopilot.madden_runtime import RuntimeDirective
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005 import MaddenPhase
from .madden2005_v20 import Madden2005V20Profile


class Madden2005V21Profile(Madden2005V20Profile):
    """Keep transient OCR/menu ambiguity from interrupting an active football game.

    Long unattended soaks showed brief UNKNOWN/DIALOG classifications during normal
    post-play camera cuts and field transitions. Those weak menu labels could hand
    control to generic navigation recovery, whose Triangle action can accidentally
    open Madden's pause menu. A second failure mode was an over-broad MEMORY CARD
    heuristic producing SAVE behavior while the game was still in progress.

    v21 adds a short active-game grace latch. Recent confidently observed football
    outranks UNKNOWN and weak DIALOG menu classifications, while explicit PAUSED,
    FINAL, PLAYCALL, KICKING, and strongly verified save UI still take precedence.
    """

    name = "madden2005"

    ACTIVE_GAMEPLAY_PHASES = {
        MaddenPhase.PLAYCALL,
        MaddenPhase.PRE_SNAP,
        MaddenPhase.LIVE,
        MaddenPhase.POST_PLAY,
        MaddenPhase.KICKING,
    }

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.active_game_unknown_grace_seconds = max(
            2.0, float(cfg.get("active_game_unknown_grace_seconds", 10.0))
        )
        self.last_confident_gameplay_at = -1e9
        self.active_game_unknown_holds = 0
        self.false_save_suppressions = 0

    @staticmethod
    def _hygiene_compact(text: str | None) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())

    @classmethod
    def _looks_like_save_screen(cls, text: str | None) -> bool:
        """Require explicit memory-card/save-modal evidence before SAVE owns input."""
        compact = cls._hygiene_compact(text)
        if "MEMORYCARD" not in compact:
            return False

        if "CONTINUEWITHOUTSAVING" in compact and any(
            marker in compact
            for marker in ("SAVEYOURPROFILE", "DOYOUWANTTOSAVE", "ENABLEAUTOSAVE")
        ):
            return True

        slot_hits = sum(
            marker in compact
            for marker in ("MEMORYCARDSLOT", "STATUSVALID", "KBFREE", "SAVETO")
        )
        return slot_hits >= 2

    def _recent_active_gameplay(self, now: float) -> bool:
        if not bool(getattr(self.runtime_monitor, "active_game", False)):
            return False
        return now - self.last_confident_gameplay_at <= self.active_game_unknown_grace_seconds

    def _ambiguous_menu_during_game(self, now: float) -> bool:
        if not self._recent_active_gameplay(now):
            return False
        return self.menu_assessment.screen in {MaddenScreen.UNKNOWN, MaddenScreen.DIALOG}

    def _hold_active_game_ambiguity(self, controller: Controller, now: float) -> str:
        controller.neutral_sticks()
        self.queue.clear()
        self.active_game_unknown_holds += 1
        self.runtime_monitor.last_progress_at = now
        self.runtime_monitor.recovery_level = 0
        self.runtime_monitor.next_recovery_at = 0.0
        self.next_action_at = max(self.next_action_at, now + 0.35)
        self.current_action = "field: active-game ambiguity grace; hold inputs"
        return self.current_action

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        phase_before = self.phase
        obs = super()._observe(ctx)

        if phase_before in self.ACTIVE_GAMEPLAY_PHASES or self.phase in self.ACTIVE_GAMEPLAY_PHASES:
            self.last_confident_gameplay_at = ctx.now
        elif self._navigation_context() in {"field", "playcall"}:
            self.last_confident_gameplay_at = ctx.now

        if self.save_screen_visible and not self._looks_like_save_screen(self.last_ocr.text):
            self.save_screen_visible = False
            self.false_save_suppressions += 1

        return obs

    def _menu(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        if self._ambiguous_menu_during_game(now):
            return self._hold_active_game_ambiguity(controller, now)
        return super()._menu(controller, obs, now)

    def _soft_stall_recovery(self, controller: Controller, now: float) -> str | None:
        if self._ambiguous_menu_during_game(now):
            self.runtime_monitor.last_progress_at = now
            return None
        return super()._soft_stall_recovery(controller, now)

    def _progress_recover(
        self,
        controller: Controller,
        directive: RuntimeDirective,
        now: float,
    ) -> str:
        if self._ambiguous_menu_during_game(now):
            return self._hold_active_game_ambiguity(controller, now)
        return super()._progress_recover(controller, directive, now)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        recent = self._recent_active_gameplay(ctx.now)
        state.update(
            {
                "active_game_unknown_grace_seconds": round(
                    self.active_game_unknown_grace_seconds, 1
                ),
                "recent_active_gameplay": recent,
                "active_game_unknown_holds": self.active_game_unknown_holds,
                "false_save_suppressions": self.false_save_suppressions,
            }
        )

        raw = str(state.get("menu_screen") or "").lower()
        if recent and raw in {"unknown", "dialog"}:
            state["raw_menu_screen"] = raw
            state["menu_screen"] = "field"
            state["menu_reason"] = "recent active football; suppress ambiguous menu recovery"
            state["navigation_unknown_suppressed"] = True
        return state
