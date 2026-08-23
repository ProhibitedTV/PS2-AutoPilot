from __future__ import annotations

import re

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_menu import MaddenScreen, MenuAssessment, find_ocr_line
from ps2_autopilot.madden_runtime import RuntimeDirective
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005 import MaddenPhase
from .madden2005_v14 import Madden2005V14Profile


class Madden2005V15Profile(Madden2005V14Profile):
    """Make the postgame route deterministic and reacquire Play Now afterward.

    Live v0.6.5 runs showed a subtle loop: generic visual phase inference could
    momentarily leave GAME_OVER before OCR reasserted the END OF GAME menu. Every
    re-entry restarted the 12-second presentation hold, while the semantic watchdog
    could inject Triangle/Start and send the agent back into stats. The result looked
    like circling the final menu instead of simply moving to END GAME.

    v0.6.6 locks the recognized postgame menu until END GAME is deliberately
    submitted, gives the dedicated final navigator exclusive control, and recognizes
    Madden's front-end carousel (including FOOTBALL 101) so the next exhibition can
    seek PLAY NOW without blindly confirming the wrong mode.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.postgame_phase_exit_suppressed = 0
        self.frontend_carousel_seek_steps = 0
        self.frontend_carousel_direction = "left"

    @staticmethod
    def _compact_text(text: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", text.upper())

    @classmethod
    def _looks_like_frontend_carousel_text(cls, text: str) -> bool:
        compact = cls._compact_text(text)
        # Do not reinterpret actual submodes/drill screens as the top-level carousel.
        nested = (
            "FRANCHISESETUP",
            "SELECTDRILL",
            "STARTDRILL",
            "TRAININGCAMP",
            "ROSTERMANAGEMENT",
            "CREATEAPLAYER",
        )
        if any(marker in compact for marker in nested):
            return False
        return any(
            marker in compact
            for marker in (
                "PLAYNOW",
                "FOOTBALL101",
                "MADDEN101",
                "FRANCHISE",
                "MINICAMP",
                "MYMADDEN",
                "FEATURES",
            )
        )

    def _transition_phase(self, new_phase: MaddenPhase, now: float) -> None:
        # Madden's postgame screen itself is visually menu-like. The base visual
        # phase detector can therefore try GAME_OVER -> MENU before the current OCR
        # pass reclassifies END OF GAME back to FINAL. That transient hop used to
        # restart the 12-second presentation hold on every cycle. Keep GAME_OVER
        # sticky while the *previous* OCR still proves the postgame menu is present.
        if (
            self.phase == MaddenPhase.GAME_OVER
            and new_phase in {MaddenPhase.MENU, MaddenPhase.TRANSITION}
            and now >= self.postgame_exit_pending_until
            and self._looks_like_postgame_menu()
        ):
            self.candidate_phase = None
            self.postgame_phase_exit_suppressed += 1
            return
        super()._transition_phase(new_phase, now)

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)

        # The supplied postgame-exit screenshot lands on Madden's top-level carousel
        # with FOOTBALL 101 selected. That is a normal MAIN_MENU state, not an unknown
        # screen and not permission to press Cross. PLAY NOW will be sought explicitly.
        if (
            not self.stats_screen_visible
            and not self._looks_like_postgame_menu()
            and self._looks_like_frontend_carousel_text(self.last_ocr.text)
        ):
            self.menu_assessment = MenuAssessment(
                MaddenScreen.MAIN_MENU,
                0.97,
                "Madden front-end carousel",
            )
            if self.phase not in {MaddenPhase.LIVE, MaddenPhase.KICKING, MaddenPhase.MENU}:
                self._transition_phase(MaddenPhase.MENU, ctx.now)
        return obs

    def _postgame(self, controller: Controller, now: float) -> str:
        controller.neutral_sticks()

        if now < self.postgame_exit_pending_until:
            self.current_action = "final: END GAME submitted; waiting for main menu"
            return self.current_action
        if now < self.next_action_at:
            return self.current_action

        end_game_line = find_ocr_line(self.last_ocr, "END GAME")
        highlight = self.menu_highlight

        # Prefer exact known-row semantics over generic y geometry. The observed
        # menu is STATS/INFO -> GRUDGE MATCH -> END GAME.
        if highlight is not None and highlight.confidence >= 0.24:
            selected = self._compact_text(highlight.text)
            if selected == "ENDGAME":
                controller.tap("cross", 0.08)
                self.postgame_confirm_attempts += 1
                self.postgame_exit_pending_until = now + 4.0
                self.next_action_at = self.postgame_exit_pending_until
                self.current_action = "final: verified END GAME -> CROSS once; wait for main menu"
                return self.current_action

            if "STATSINFO" in selected or "GRUDGEMATCH" in selected:
                controller.tap("down", 0.07)
                self.postgame_seek_steps += 1
                self.next_action_at = now + 0.55
                self.current_action = (
                    f"final: DOWN toward END GAME from {highlight.text} "
                    f"({self.postgame_seek_steps})"
                )
                return self.current_action

            if end_game_line is not None:
                direction = "down" if end_game_line.y > highlight.y else "up"
                controller.tap(direction, 0.07)
                self.postgame_seek_steps += 1
                self.next_action_at = now + 0.60
                self.current_action = (
                    f"final: {direction.upper()} toward verified END GAME "
                    f"({highlight.text})"
                )
                return self.current_action

        # The observed END OF GAME menu opens on STATS/INFO. If highlight detection
        # is temporarily weak, two bounded DOWN probes are safer than Triangle/Start
        # and should land on the bottom END GAME row. Cross is still forbidden until
        # OCR/highlight verifies END GAME itself.
        if end_game_line is not None and self.postgame_seek_steps < 2:
            controller.tap("down", 0.07)
            self.postgame_seek_steps += 1
            self.next_action_at = now + 0.65
            self.current_action = (
                f"final: deterministic DOWN seek {self.postgame_seek_steps}/2 toward END GAME"
            )
            return self.current_action

        self.next_action_at = now + 0.65
        self.current_action = "final: END GAME visible but selection unverified; hold and rescan"
        return self.current_action

    def _menu(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        if self.menu_assessment.screen == MaddenScreen.MAIN_MENU:
            compact = self._compact_text(self.last_ocr.text)
            if "PLAYNOW" in compact:
                self.frontend_carousel_seek_steps = 0
                self.frontend_carousel_direction = "left"
                return super()._menu(controller, obs, now)

            if self._looks_like_frontend_carousel_text(self.last_ocr.text):
                controller.neutral_sticks()
                if now < self.next_action_at:
                    return self.current_action

                # Never Cross a non-PLAY-NOW carousel tile. Sweep left first; if an
                # edge/non-wrapping layout defeats that, sweep right on the next block.
                if self.frontend_carousel_seek_steps and self.frontend_carousel_seek_steps % 8 == 0:
                    self.frontend_carousel_direction = (
                        "right" if self.frontend_carousel_direction == "left" else "left"
                    )
                controller.tap(self.frontend_carousel_direction, 0.07)
                self.frontend_carousel_seek_steps += 1
                self.next_action_at = now + 0.70
                self.current_action = (
                    f"main menu: {self.frontend_carousel_direction.upper()} seek PLAY NOW "
                    f"({self.frontend_carousel_seek_steps})"
                )
                return self.current_action

        return super()._menu(controller, obs, now)

    def _progress_recover(
        self,
        controller: Controller,
        directive: RuntimeDirective,
        now: float,
    ) -> str:
        # Belt-and-suspenders protection: even if a runtime directive somehow leaks
        # through, FINAL and STATS are exclusively owned by their dedicated policies.
        if self.phase == MaddenPhase.GAME_OVER:
            controller.neutral_sticks()
            self.current_action = "final: generic recovery suppressed; END GAME navigator owns state"
            return self.current_action
        if self.stats_screen_visible:
            controller.neutral_sticks()
            self.current_action = "stats: generic recovery suppressed; Triangle backout owns state"
            return self.current_action
        return super()._progress_recover(controller, directive, now)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "postgame_phase_exit_suppressed": self.postgame_phase_exit_suppressed,
                "frontend_carousel_seek_steps": self.frontend_carousel_seek_steps,
                "frontend_carousel_direction": self.frontend_carousel_direction,
                "frontend_carousel_detected": self._looks_like_frontend_carousel_text(
                    self.last_ocr.text
                ),
            }
        )
        return state
