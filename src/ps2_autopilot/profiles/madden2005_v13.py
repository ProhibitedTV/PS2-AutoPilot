from __future__ import annotations

import re

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_menu import MaddenScreen, MenuAssessment, find_ocr_line
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005 import MaddenPhase
from .madden2005_v12 import Madden2005V12Profile


class Madden2005V13Profile(Madden2005V12Profile):
    """Recognize and safely exit Madden's real END OF GAME menu.

    Live testing showed the postgame menu can OCR as END OF GAME / STATS/INFO /
    GRUDGE MATCH / END GAME without yielding FINAL SCORE. It also opens with
    STATS/INFO highlighted, so blindly pressing Cross can enter stats instead of
    returning to the exhibition flow. v0.6.4 makes the menu itself authoritative
    and confirms only a visually verified END GAME row.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.postgame_seek_steps = 0
        self.postgame_confirm_attempts = 0
        self.postgame_exit_pending_until = -1e9

    @staticmethod
    def _compact(text: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", text.upper())

    def _looks_like_postgame_menu(self) -> bool:
        compact = self._compact(self.last_ocr.text)
        if "ENDOFGAME" in compact:
            return True
        option_hits = sum(
            marker in compact
            for marker in ("STATSINFO", "GRUDGEMATCH", "ENDGAME")
        )
        return option_hits >= 2

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)

        if self._looks_like_postgame_menu():
            self.menu_assessment = MenuAssessment(
                MaddenScreen.FINAL,
                0.995,
                "Madden END OF GAME postgame menu",
            )
            if self.phase != MaddenPhase.GAME_OVER:
                self._transition_phase(MaddenPhase.GAME_OVER, ctx.now)
                self.postgame_seek_steps = 0
                self.postgame_confirm_attempts = 0
                self.postgame_exit_pending_until = -1e9
        return obs

    def _postgame(self, controller: Controller, now: float) -> str:
        controller.neutral_sticks()

        if now < self.postgame_exit_pending_until:
            self.current_action = "final: END GAME submitted; waiting for postgame transition"
            return self.current_action

        if now < self.next_action_at:
            return self.current_action

        end_game_line = find_ocr_line(self.last_ocr, "END GAME")
        highlight = self.menu_highlight

        if highlight is not None and highlight.confidence >= 0.24:
            selected = self._compact(highlight.text)
            if "ENDGAME" in selected and "ENDOFGAME" not in selected:
                controller.tap("cross", 0.08)
                self.postgame_confirm_attempts += 1
                self.postgame_exit_pending_until = now + 3.0
                self.next_action_at = self.postgame_exit_pending_until
                self.current_action = (
                    "final: verified END GAME -> CROSS; wait for transition"
                )
                return self.current_action

            if end_game_line is not None:
                direction = "down" if end_game_line.y > highlight.y else "up"
                controller.tap(direction, 0.07)
                self.postgame_seek_steps += 1
                self.next_action_at = now + 0.50
                self.current_action = (
                    f"final: move {direction} toward END GAME "
                    f"({highlight.text} -> END GAME)"
                )
                return self.current_action

        # END GAME is the bottom row on the observed postgame menu. A bounded Down
        # seek is non-destructive; Cross remains forbidden until the selected row
        # itself is visually verified.
        if end_game_line is not None and self.postgame_seek_steps < 5:
            controller.tap("down", 0.07)
            self.postgame_seek_steps += 1
            self.next_action_at = now + 0.55
            self.current_action = (
                f"final: safe DOWN seek for END GAME {self.postgame_seek_steps}/5"
            )
            return self.current_action

        self.next_action_at = now + 0.75
        self.current_action = "final: END GAME row unverified; hold and rescan"
        return self.current_action

    def _policy_tick(self, controller: Controller, ctx: ProfileContext) -> str:
        obs = self._observe(ctx)

        queued = self._run_queue(controller, ctx.now)
        if queued:
            return queued

        soft_recovery = self._soft_stall_recovery(controller, ctx.now)
        if soft_recovery and self.phase != MaddenPhase.GAME_OVER:
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
            return self._postgame(controller, ctx.now)
        return self._menu(controller, obs, ctx.now)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "postgame_menu_detected": self._looks_like_postgame_menu(),
                "postgame_seek_steps": self.postgame_seek_steps,
                "postgame_confirm_attempts": self.postgame_confirm_attempts,
                "postgame_exit_pending": ctx.now < self.postgame_exit_pending_until,
            }
        )
        return state
