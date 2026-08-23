from __future__ import annotations

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_runtime import RuntimeDirective
from ps2_autopilot.madden_runtime_hygiene import (
    ContextAwareMaddenRuntimeMonitor,
    semantic_context,
)
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005 import MaddenPhase
from .madden2005_v10 import Madden2005V10Profile


class Madden2005V11Profile(Madden2005V10Profile):
    """Separate true navigation unknowns from normal Madden field/presentation states."""

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        # Replace the generic runtime monitor with one that understands that a
        # menu classifier being UNKNOWN during gameplay is normal and harmless.
        self.runtime_monitor = ContextAwareMaddenRuntimeMonitor(cfg)
        self.navigation_unknown_suppressed = 0
        self.last_navigation_context: str | None = None

    def _navigation_context(self) -> str | None:
        obs = self.last_observation
        spatial = getattr(self, "last_spatial", None)
        state = {
            "phase": self.phase.value,
            "menu_screen": self.menu_assessment.screen.value,
            "game_state": None if obs is None else obs.state.value,
            "field_green": None if obs is None else obs.green_ratio,
            "ocr_text": self.last_ocr.text,
            "spatial_players": 0 if spatial is None else spatial.player_count,
            "spatial_fresh": bool(
                spatial is not None
                and getattr(spatial, "available", False)
                and getattr(self, "last_spatial_at", -1e9) > -1e8
            ),
        }
        return semantic_context(state)

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)
        self.last_navigation_context = self._navigation_context()
        return obs

    def _menu(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        context = self._navigation_context()
        if self.menu_assessment.screen.value == "unknown" and context is not None:
            controller.neutral_sticks()
            self.navigation_unknown_suppressed += 1
            self.next_action_at = max(self.next_action_at, now + 0.40)
            if context == "presentation":
                self.current_action = "presentation: known replay/stats/break context; hold inputs"
            elif context == "playcall":
                self.current_action = "playcall: known PICK A PLAY context; hold for classifier"
            else:
                self.current_action = "field: known gameplay context; suppress menu recovery"
            return self.current_action
        return super()._menu(controller, obs, now)

    def _progress_recover(
        self,
        controller: Controller,
        directive: RuntimeDirective,
        now: float,
    ) -> str:
        context = self._navigation_context()
        if self.phase in {MaddenPhase.MENU, MaddenPhase.TRANSITION} and context is not None:
            controller.neutral_sticks()
            self.navigation_unknown_suppressed += 1
            self.current_action = f"{context}: suppress semantic recovery; context is known"
            self.next_action_at = now + 0.75
            return self.current_action
        return super()._progress_recover(controller, directive, now)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        raw_screen = str(state.get("menu_screen") or "")
        context = semantic_context(state)
        state["raw_menu_screen"] = raw_screen
        state["semantic_context"] = context
        state["navigation_unknown_suppressed"] = self.navigation_unknown_suppressed

        # Viewer/debug console should describe the actual game context rather than
        # shouting UNKNOWN simply because the menu classifier has nothing to do on
        # a football field or a normal Madden presentation screen.
        if raw_screen == "unknown" and context is not None:
            state["menu_screen"] = context
            if context == "field":
                role = str(state.get("possession") or "unknown")
                confidence = float(state.get("possession_confidence") or 0.0)
                state["menu_reason"] = f"active field; {role} {confidence:.0%}"
            elif context == "presentation":
                state["menu_reason"] = "recognized replay/stats/quarter presentation"
            else:
                state["menu_reason"] = "recognized playcall context"
        return state
