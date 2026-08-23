from __future__ import annotations

import re

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_menu import MaddenScreen, MenuAssessment
from ps2_autopilot.madden_runtime import RuntimeDirective
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005 import MaddenPhase
from .madden2005_v16 import Madden2005V16Profile


class Madden2005V17Profile(Madden2005V16Profile):
    """Make the Play Now -> kickoff lifecycle explicit and presentation-safe.

    Live v0.6.7 proved the full game loop, but the first fresh-game transition
    still produced false UNKNOWN states. Madden OCR emits compact labels such as
    SELECTTEAMS and SELECTSIDES, then spends a long time on loading cards,
    weather graphics, player introductions and stadium cinematics before the coin
    toss. Those screens must not receive generic Cross/Triangle recovery inputs.

    v0.6.8 recognizes the compact menu labels locally, keeps a pregame lifecycle
    latch from team selection until coin toss/playcall, treats intermediate visual
    content as passive presentation, and safely backs out of memory-card SAVE UI.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.pregame_active = False
        self.pregame_presentation = False
        self.pregame_reason: str | None = None
        self.pregame_holds = 0
        self.pregame_menu_overrides = 0
        self.pregame_phase_overrides = 0
        self.save_screen_visible = False
        self.save_backout_attempts = 0

    @staticmethod
    def _compact(text: str | None) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())

    @classmethod
    def _compact_menu_screen(cls, text: str | None) -> MaddenScreen | None:
        compact = cls._compact(text)
        if any(marker in compact for marker in ("SELECTTEAMS", "TEAMSELECT", "SELECTTEAM")):
            return MaddenScreen.TEAM_SELECT
        if any(
            marker in compact
            for marker in (
                "SELECTSIDES",
                "SELECTSIDE",
                "CHOOSESIDE",
                "CONTROLLERSELECT",
                "SELECTCONTROLLER",
            )
        ):
            return MaddenScreen.CONTROLLER_SELECT
        if any(marker in compact for marker in ("SELECTSTADIUM", "STADIUMSELECT")):
            return MaddenScreen.GAME_SETTINGS
        return None

    @classmethod
    def _looks_like_save_screen(cls, text: str | None) -> bool:
        compact = cls._compact(text)
        if "MEMORYCARD" not in compact:
            return False
        return any(marker in compact for marker in ("SAVE", "STATUSVALID", "KBFREE", "MEMORYCARDSLOT"))

    @classmethod
    def _explicit_presentation_reason(cls, text: str | None) -> str | None:
        compact = cls._compact(text)
        if "LOADING" in compact:
            return "loading/player-card presentation"

        weather_hits = sum(
            marker in compact
            for marker in ("WEATHER", "TEMPERATURE", "WIND", "HUMIDITY", "FORECAST")
        )
        if "WEATHER" in compact and weather_hits >= 3:
            return "weather presentation"

        career_hits = sum(
            marker in compact
            for marker in (
                "CAREERSTATS",
                "PASSINGYARDS",
                "TDPASSES",
                "INTERCEPTIONS",
                "COMPLETIONPCT",
                "QBRATING",
                "HEIGHT",
                "WEIGHT",
            )
        )
        if career_hits >= 4:
            return "player-card presentation"
        return None

    @classmethod
    def _looks_like_coin_or_playcall(cls, text: str | None) -> bool:
        compact = cls._compact(text)
        return "COINTOSS" in compact or "PICKAPLAY" in compact

    def _set_phase_immediate(self, phase: MaddenPhase, now: float) -> None:
        if self.phase != phase:
            self._transition_phase(phase, now)
        self.candidate_phase = None

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)
        text = self.last_ocr.text
        compact_screen = self._compact_menu_screen(text)
        explicit_presentation = self._explicit_presentation_reason(text)
        self.save_screen_visible = self._looks_like_save_screen(text)

        if compact_screen is not None and self.menu_assessment.screen != compact_screen:
            confidence = 0.99 if compact_screen in {MaddenScreen.TEAM_SELECT, MaddenScreen.CONTROLLER_SELECT} else 0.96
            self.menu_assessment = MenuAssessment(
                compact_screen,
                confidence,
                f"compact OCR marker: {compact_screen.value}",
            )
            self.pregame_menu_overrides += 1

        screen = self.menu_assessment.screen

        if screen in {
            MaddenScreen.TEAM_SELECT,
            MaddenScreen.CONTROLLER_SELECT,
            MaddenScreen.MATCHUP,
            MaddenScreen.GAME_SETTINGS,
        }:
            self.pregame_active = True
            self.pregame_presentation = False
            self.pregame_reason = None
            self._set_phase_immediate(MaddenPhase.MENU, ctx.now)

        if explicit_presentation is not None:
            # This also makes a cold restart on a loading/weather/player-card
            # screen safe even if the process did not witness SELECT TEAMS first.
            self.pregame_active = True

        if self._looks_like_coin_or_playcall(text) or screen in {
            MaddenScreen.COIN_TOSS,
            MaddenScreen.PLAYCALL,
            MaddenScreen.KICKING,
        }:
            self.pregame_active = False
            self.pregame_presentation = False
            self.pregame_reason = None
        elif self.save_screen_visible:
            self.pregame_presentation = False
            self.pregame_reason = None
            self._set_phase_immediate(MaddenPhase.MENU, ctx.now)
        elif self.pregame_active and screen == MaddenScreen.UNKNOWN:
            # Once team/controller selection has launched the matchup, all unknown
            # visual content before coin toss is presentation. This catches stadium
            # flyovers and player animations that may contain almost no OCR text.
            self.pregame_presentation = True
            self.pregame_reason = explicit_presentation or "pregame/stadium cinematic"
            if self.phase not in {MaddenPhase.GAME_OVER, MaddenPhase.PAUSED}:
                if self.phase != MaddenPhase.TRANSITION:
                    self.pregame_phase_overrides += 1
                self._set_phase_immediate(MaddenPhase.TRANSITION, ctx.now)
            self.runtime_monitor.last_progress_at = ctx.now
            self.runtime_monitor.recovery_level = 0
            self.runtime_monitor.next_recovery_at = 0.0
        else:
            self.pregame_presentation = False
            self.pregame_reason = None

        return obs

    def _safe_save_backout(self, controller: Controller, now: float) -> str:
        controller.neutral_sticks()
        self.queue.clear()
        if now >= self.next_action_at:
            controller.tap("triangle", 0.08)
            self.save_backout_attempts += 1
            self.next_action_at = now + 1.25
        self.current_action = (
            f"save: safe TRIANGLE backout {self.save_backout_attempts}; never confirm memory card"
        )
        return self.current_action

    def _pregame_hold(self, controller: Controller, now: float) -> str:
        controller.neutral_sticks()
        self.queue.clear()
        self.pregame_holds += 1
        self.runtime_monitor.last_progress_at = now
        self.runtime_monitor.recovery_level = 0
        self.runtime_monitor.next_recovery_at = 0.0
        self.next_action_at = max(self.next_action_at, now + 0.45)
        self.current_action = f"pregame: watch {self.pregame_reason or 'presentation'}; hold inputs"
        return self.current_action

    def _policy_tick(self, controller: Controller, ctx: ProfileContext) -> str:
        obs = self._observe(ctx)

        # These two contexts get first refusal before queue execution or any stall
        # recovery. That prevents loading cards/cinematics from receiving snap/menu
        # probes and prevents accidental memory-card writes.
        if self.save_screen_visible:
            return self._safe_save_backout(controller, ctx.now)
        if self.pregame_presentation:
            return self._pregame_hold(controller, ctx.now)

        # Preserve the explicit stats recovery added in v0.6.5.
        if self.stats_screen_visible:
            self.queue.clear()
            return self._stats_backout(controller, ctx.now)

        queued = self._run_queue(controller, ctx.now)
        if queued:
            return queued

        if self.phase != MaddenPhase.GAME_OVER:
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
            return self._postgame(controller, ctx.now)
        return self._menu(controller, obs, ctx.now)

    def _progress_recover(
        self,
        controller: Controller,
        directive: RuntimeDirective,
        now: float,
    ) -> str:
        if self.save_screen_visible:
            return self._safe_save_backout(controller, now)
        if self.pregame_presentation:
            return self._pregame_hold(controller, now)
        return super()._progress_recover(controller, directive, now)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "pregame_active": self.pregame_active,
                "pregame_presentation": self.pregame_presentation,
                "pregame_reason": self.pregame_reason,
                "pregame_holds": self.pregame_holds,
                "pregame_menu_overrides": self.pregame_menu_overrides,
                "pregame_phase_overrides": self.pregame_phase_overrides,
                "save_screen_visible": self.save_screen_visible,
                "save_backout_attempts": self.save_backout_attempts,
            }
        )

        # Do not archive ordinary pregame broadcast content as UNKNOWN. Preserve
        # the raw classifier value for forensic inspection.
        if self.pregame_presentation and str(state.get("menu_screen") or "") == "unknown":
            state["raw_menu_screen"] = "unknown"
            state["menu_screen"] = "presentation"
            state["menu_reason"] = self.pregame_reason or "known pregame presentation"
        if self.save_screen_visible:
            state["raw_menu_screen"] = str(state.get("menu_screen") or "unknown")
            state["menu_screen"] = "save"
            state["menu_reason"] = "recognized memory-card save screen; safe backout"
        return state
