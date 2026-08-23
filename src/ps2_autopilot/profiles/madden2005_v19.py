from __future__ import annotations

import re

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005 import MaddenPhase
from .madden2005_v18 import Madden2005V18Profile


class Madden2005V19Profile(Madden2005V18Profile):
    """Make the postgame profile-save prompt deterministic and write-safe.

    Live v0.7.0 correctly recognized Madden's profile save UI but treated every
    memory-card screen as a Triangle-backout target. The actual postgame prompt is
    modal and ignores Triangle: YES is selected by default, with CONTINUE WITHOUT
    SAVING immediately below it. Repeated Triangle therefore left the 24/7 loop
    stuck forever.

    v0.7.1 distinguishes that modal from the lower-level memory-card slot picker.
    On the profile-save prompt it seeks CONTINUE WITHOUT SAVING, verifies that row
    through the existing highlight OCR, submits Cross exactly once, then waits for
    SELECT TEAMS / the next lifecycle state. Generic memory-card slot screens retain
    the conservative Triangle-only escape policy from v17.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.profile_save_prompt_visible = False
        self.profile_save_prompt_submitted = False
        self.profile_save_seek_steps = 0
        self.profile_save_confirms = 0
        self.profile_save_last_choice: str | None = None

    @staticmethod
    def _save_compact(text: str | None) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())

    @classmethod
    def _looks_like_profile_save_prompt(cls, text: str | None) -> bool:
        compact = cls._save_compact(text)
        return (
            "CONTINUEWITHOUTSAVING" in compact
            and (
                "SAVEYOURPROFILE" in compact
                or "DOYOUWANTTOSAVE" in compact
                or "ENABLEAUTOSAVE" in compact
            )
        )

    def _profile_save_highlight(self) -> str | None:
        highlight = self.menu_highlight
        if highlight is None or highlight.confidence < 0.20:
            return None
        compact = self._save_compact(highlight.text)
        if "CONTINUEWITHOUTSAVING" in compact:
            return "continue"
        if "ENABLEAUTOSAVE" in compact:
            return "autosave"
        # Keep YES intentionally strict so incidental words cannot authorize a
        # memory-card write. The only write-capable row on this prompt is YES.
        if compact in {"YES", "XYES"}:
            return "yes"
        return None

    def _reset_profile_save_prompt(self) -> None:
        self.profile_save_prompt_visible = False
        self.profile_save_prompt_submitted = False
        self.profile_save_seek_steps = 0
        self.profile_save_last_choice = None

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)
        visible = self._looks_like_profile_save_prompt(self.last_ocr.text)

        if visible:
            if not self.profile_save_prompt_visible:
                self.profile_save_prompt_submitted = False
                self.profile_save_seek_steps = 0
            self.profile_save_prompt_visible = True
            self.save_screen_visible = True
            self.pregame_presentation = False
            self.pregame_reason = None
            self._set_phase_immediate(MaddenPhase.MENU, ctx.now)
            self.runtime_monitor.last_progress_at = ctx.now
            self.runtime_monitor.recovery_level = 0
            self.runtime_monitor.next_recovery_at = 0.0
        elif self.profile_save_prompt_visible:
            # The modal disappeared. The next state's normal policy now owns the
            # controller; in the observed flow this is SELECT TEAMS.
            self._reset_profile_save_prompt()

        return obs

    def _safe_save_backout(self, controller: Controller, now: float) -> str:
        if not self.profile_save_prompt_visible:
            return super()._safe_save_backout(controller, now)

        controller.neutral_sticks()
        self.queue.clear()
        self.runtime_monitor.last_progress_at = now
        self.runtime_monitor.recovery_level = 0
        self.runtime_monitor.next_recovery_at = 0.0

        if self.profile_save_prompt_submitted:
            self.current_action = (
                "save: Continue Without Saving submitted; wait for next lifecycle state"
            )
            return self.current_action

        choice = self._profile_save_highlight()
        self.profile_save_last_choice = choice

        if now < self.next_action_at:
            return self.current_action

        if choice == "continue":
            controller.tap("cross", 0.08)
            self.profile_save_prompt_submitted = True
            self.profile_save_confirms += 1
            self.next_action_at = now + 2.0
            self.current_action = (
                "save: verified CONTINUE WITHOUT SAVING -> CROSS once; never write profile"
            )
            return self.current_action

        # The prompt defaults to YES. If highlight OCR sees YES, one DOWN reaches
        # CONTINUE WITHOUT SAVING. If it sees AUTOSAVE, one UP reaches it. When
        # highlight OCR is unavailable, DOWN is still safe because Cross remains
        # forbidden until CONTINUE WITHOUT SAVING is positively verified.
        direction = "up" if choice == "autosave" else "down"
        controller.tap(direction, 0.07)
        self.profile_save_seek_steps += 1
        self.next_action_at = now + 0.55
        self.current_action = (
            f"save: {direction.upper()} seek CONTINUE WITHOUT SAVING "
            f"({self.profile_save_seek_steps}; selected={choice or 'unverified'})"
        )
        return self.current_action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "profile_save_prompt_visible": self.profile_save_prompt_visible,
                "profile_save_prompt_submitted": self.profile_save_prompt_submitted,
                "profile_save_seek_steps": self.profile_save_seek_steps,
                "profile_save_confirms": self.profile_save_confirms,
                "profile_save_last_choice": self.profile_save_last_choice,
            }
        )
        if self.profile_save_prompt_visible:
            state["raw_menu_screen"] = str(state.get("menu_screen") or "save")
            state["menu_screen"] = "save"
            state["menu_reason"] = (
                "profile save prompt; seek verified Continue Without Saving"
            )
        return state
