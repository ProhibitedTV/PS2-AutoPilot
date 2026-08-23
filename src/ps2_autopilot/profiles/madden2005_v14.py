from __future__ import annotations

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_menu import MaddenScreen, MenuAssessment
from ps2_autopilot.madden_runtime_hygiene import semantic_context
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005 import MaddenPhase
from .madden2005_v13 import Madden2005V13Profile


class Madden2005V14Profile(Madden2005V13Profile):
    """Treat Madden stats pages as known navigation and back out intentionally.

    A v0.6.3 process entered STATS/INFO from the END OF GAME menu before the safer
    postgame navigator landed. Fresh screenshots showed two related screens:
    the STATS/INFO tab selector and a deeper GAME STATS table. Both are safe to
    escape with Triangle. This recovery is also safe if the same stats pages were
    entered from the pause menu: Triangle simply returns to that parent menu.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.stats_screen_visible = False
        self.stats_backout_attempts = 0
        self.stats_backout_pending_until = -1e9

    def _looks_like_stats_screen(self) -> bool:
        return semantic_context(
            {
                "phase": "menu",
                "menu_screen": MaddenScreen.UNKNOWN.value,
                "ocr_text": self.last_ocr.text,
                "field_green": 0.0,
            }
        ) == "stats"

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)
        was_visible = self.stats_screen_visible
        self.stats_screen_visible = self._looks_like_stats_screen()

        if self.stats_screen_visible:
            # Keep the raw screen semantically known without pretending this is
            # automatically postgame; the same stats UI can be reached from pause.
            self.menu_assessment = MenuAssessment(
                MaddenScreen.UNKNOWN,
                0.97,
                "recognized Madden stats submenu/table",
            )
        elif was_visible:
            self.stats_backout_pending_until = -1e9
        return obs

    def _stats_backout(self, controller: Controller, now: float) -> str:
        controller.neutral_sticks()

        if now < self.stats_backout_pending_until:
            self.current_action = "stats: Triangle submitted; waiting for parent menu"
            return self.current_action
        if now < self.next_action_at:
            return self.current_action

        controller.tap("triangle", 0.08)
        self.stats_backout_attempts += 1
        self.stats_backout_pending_until = now + 1.25
        self.next_action_at = self.stats_backout_pending_until
        self.current_action = (
            f"stats: TRIANGLE back to parent menu {self.stats_backout_attempts}"
        )
        return self.current_action

    def _policy_tick(self, controller: Controller, ctx: ProfileContext) -> str:
        obs = self._observe(ctx)

        # Stats recovery gets first refusal. Clear anything queued from the prior
        # phase so no stale Cross/menu input can enter another stats page.
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

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "stats_screen_visible": self.stats_screen_visible,
                "stats_backout_attempts": self.stats_backout_attempts,
                "stats_backout_pending": ctx.now < self.stats_backout_pending_until,
            }
        )
        return state
