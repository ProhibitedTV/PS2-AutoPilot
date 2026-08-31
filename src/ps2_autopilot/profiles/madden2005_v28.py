from __future__ import annotations

from ps2_autopilot.madden_menu import MaddenScreen, MenuAssessment
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005 import MaddenPhase
from .madden2005_v27 import Madden2005V27Profile


class Madden2005V28Profile(Madden2005V27Profile):
    """Reacquire Madden's title/main-menu root after a stale pregame latch.

    Live V27 evidence exposed a lifecycle edge rather than a football-policy issue.
    The pregame guard correctly held inputs on a loading/player-card screen, but a
    later title screen was OCR'd as ``PRESSSTART BUTTON``. The older title
    classifier expects spaced prompt text, so the screen stayed UNKNOWN while
    V17's intentionally sticky ``pregame_active`` latch converted UNKNOWN into a
    protected stadium cinematic forever.

    V28 keeps the valuable pregame presentation guard, but gives unmistakable root
    menu evidence higher authority:

    * compact PRESSSTART / PRESSANYBUTTON prompts identify the title screen;
    * compact PLAYNOW identifies the main menu when the shared classifier misses it;
    * title/main-menu evidence clears stale pregame presentation ownership;
    * the phase is immediately returned to MENU so the verified navigator can press
      Start / enter Play Now instead of a generic recovery macro.

    This is deliberately narrow. LOADING, weather cards, player cards, and unknown
    stadium cinematics remain hands-off presentation while a real pregame is active.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.root_menu_reacquisitions = 0
        self.root_menu_compact_overrides = 0
        self.root_menu_last_reason = "none"

    @classmethod
    def _compact_root_screen(cls, text: str | None) -> MaddenScreen | None:
        compact = cls._compact(text)
        if any(
            marker in compact
            for marker in (
                "PRESSSTART",
                "PRESSTHESTART",
                "PRESSANYBUTTON",
                "PRESSABUTTON",
            )
        ):
            return MaddenScreen.TITLE
        if "PLAYNOW" in compact:
            return MaddenScreen.MAIN_MENU
        return None

    def _apply_root_reacquisition(
        self,
        screen: MaddenScreen,
        now: float,
        *,
        reason: str,
    ) -> bool:
        if screen not in {MaddenScreen.TITLE, MaddenScreen.MAIN_MENU}:
            return False

        stale_owner = self.pregame_active or self.pregame_presentation
        stale_phase = self.phase != MaddenPhase.MENU

        self.pregame_active = False
        self.pregame_presentation = False
        self.pregame_reason = None
        if stale_phase:
            self._set_phase_immediate(MaddenPhase.MENU, now)

        # Root-menu evidence is hard progress. Do not let a watchdog immediately
        # undo semantic reacquisition with an unrelated recovery input.
        self.runtime_monitor.last_progress_at = now
        self.runtime_monitor.recovery_level = 0
        self.runtime_monitor.next_recovery_at = 0.0

        if stale_owner or stale_phase:
            self.root_menu_reacquisitions += 1
        self.root_menu_last_reason = reason
        return True

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)
        screen = self.menu_assessment.screen

        # Only override an UNKNOWN classifier result. If another semantic screen was
        # recognized, that evidence remains authoritative.
        if screen == MaddenScreen.UNKNOWN:
            compact_root = self._compact_root_screen(self.last_ocr.text)
            if compact_root is not None:
                screen = compact_root
                self.menu_assessment = MenuAssessment(
                    compact_root,
                    0.99,
                    f"v28 compact root OCR: {compact_root.value}",
                )
                self.root_menu_compact_overrides += 1

        if screen in {MaddenScreen.TITLE, MaddenScreen.MAIN_MENU}:
            self._apply_root_reacquisition(
                screen,
                ctx.now,
                reason=self.menu_assessment.reason,
            )
        return obs

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "madden_policy_version": "v28",
                "root_menu_reacquisitions": self.root_menu_reacquisitions,
                "root_menu_compact_overrides": self.root_menu_compact_overrides,
                "root_menu_last_reason": self.root_menu_last_reason,
            }
        )
        return state
