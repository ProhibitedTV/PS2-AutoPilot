from __future__ import annotations

import re

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_menu import MaddenScreen
from ps2_autopilot.madden_runtime import RuntimeDirective
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005 import MaddenPhase
from .madden2005_v15 import Madden2005V15Profile


class Madden2005V16Profile(Madden2005V15Profile):
    """Verify PLAY NOW selection and give front-end reacquisition exclusive control.

    Live v0.6.6 proved the postgame exit itself: END GAME was verified and submitted,
    then Madden returned to the top-level front-end. The remaining failure was that
    the semantic watchdog injected Triangle/Start while the carousel seeker was
    working, and the inherited main-menu policy could Cross merely because PLAY NOW
    appeared somewhere in OCR rather than because the selected tile was trustworthy.

    v0.6.7 treats the recognized front-end as a dedicated lifecycle state. It clears
    stale postgame timing, suppresses generic recovery, adapts its seek axis when a
    direction is not changing the visible tile, and requires stable PLAY NOW evidence
    before a single transactional Cross.
    """

    name = "madden2005"

    FRONTEND_MARKERS = (
        "PLAYNOW",
        "FOOTBALL101",
        "MADDEN101",
        "FRANCHISE",
        "MINICAMP",
        "MYMADDEN",
        "FEATURES",
    )
    SEEK_DIRECTIONS = ("left", "up", "right", "down")

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.frontend_play_now_confirm_seconds = max(
            0.35, float(cfg.get("frontend_play_now_confirm_seconds", 0.85))
        )
        self.frontend_axis_stall_steps = max(
            2, int(cfg.get("frontend_axis_stall_steps", 3))
        )
        self.frontend_reacquired_at = -1e9
        self.frontend_last_marker: str | None = None
        self.frontend_marker_changed_at = -1e9
        self.frontend_play_now_candidate_since = -1e9
        self.frontend_axis_index = 0
        self.frontend_unchanged_steps = 0
        self.frontend_verified_crosses = 0
        self.frontend_recovery_suppressed = 0

    @staticmethod
    def _compact_frontend(text: str) -> str:
        return re.sub(r"[^A-Z0-9]", "", text.upper())

    @classmethod
    def _frontend_markers(cls, text: str) -> tuple[str, ...]:
        compact = cls._compact_frontend(text)
        return tuple(marker for marker in cls.FRONTEND_MARKERS if marker in compact)

    def _frontend_marker(self) -> str | None:
        markers = self._frontend_markers(self.last_ocr.text)
        # A single top-level title is useful selected-tile evidence. If several
        # titles are visible simultaneously, keep seeking rather than guessing.
        return markers[0] if len(markers) == 1 else None

    def _frontend_active(self) -> bool:
        return (
            self.menu_assessment.screen == MaddenScreen.MAIN_MENU
            and self._looks_like_frontend_carousel_text(self.last_ocr.text)
        )

    def _highlight_is_play_now(self) -> bool:
        highlight = self.menu_highlight
        return bool(
            highlight is not None
            and highlight.confidence >= 0.24
            and "PLAYNOW" in self._compact_frontend(highlight.text)
        )

    def _highlight_conflicts_with_play_now(self) -> bool:
        highlight = self.menu_highlight
        if highlight is None or highlight.confidence < 0.24:
            return False
        compact = self._compact_frontend(highlight.text)
        return any(marker in compact for marker in self.FRONTEND_MARKERS if marker != "PLAYNOW")

    def _play_now_ready(self, now: float) -> bool:
        if self._highlight_is_play_now():
            return True
        if self._highlight_conflicts_with_play_now():
            return False
        return (
            self.frontend_last_marker == "PLAYNOW"
            and self.frontend_play_now_candidate_since > -1e8
            and now - self.frontend_play_now_candidate_since
            >= self.frontend_play_now_confirm_seconds
        )

    def _note_frontend_marker(self, now: float) -> None:
        marker = self._frontend_marker()
        if marker != self.frontend_last_marker:
            self.frontend_last_marker = marker
            self.frontend_marker_changed_at = now
            self.frontend_unchanged_steps = 0

        if marker == "PLAYNOW":
            if self.frontend_play_now_candidate_since < -1e8:
                self.frontend_play_now_candidate_since = now
        else:
            self.frontend_play_now_candidate_since = -1e9

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        was_frontend = self._frontend_active()
        obs = super()._observe(ctx)
        is_frontend = self._frontend_active()

        if is_frontend:
            # This is known, purposeful navigation. Do not let the semantic-stall
            # watchdog inject Triangle/Start while the carousel seeker is moving.
            self.runtime_monitor.last_progress_at = ctx.now
            self.runtime_monitor.recovery_level = 0
            self.runtime_monitor.next_recovery_at = 0.0

            if not was_frontend or self.frontend_reacquired_at < -1e8:
                self.frontend_reacquired_at = ctx.now
                # Clear stale final/presentation delays so the front-end becomes
                # interactive immediately after END GAME finishes transitioning.
                self.next_action_at = min(self.next_action_at, ctx.now)
                self.menu.next_action_at = min(self.menu.next_action_at, ctx.now)
                self.current_action = "main menu: reacquired front-end; locate PLAY NOW"

            self._note_frontend_marker(ctx.now)
        else:
            self.frontend_play_now_candidate_since = -1e9
        return obs

    def _verified_play_now_cross(self, controller: Controller, now: float) -> str:
        self.frontend_verified_crosses += 1
        self.menu._tap(
            controller,
            "cross",
            now,
            MaddenScreen.MAIN_MENU,
            (
                MaddenScreen.TEAM_SELECT,
                MaddenScreen.CONTROLLER_SELECT,
                MaddenScreen.MATCHUP,
                MaddenScreen.GAME_SETTINGS,
            ),
            delay=1.2,
        )
        self.menu.current_action = "main menu: verified PLAY NOW -> CROSS once"
        self.current_action = self.menu.current_action
        return self.current_action

    def _menu(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        if not self._frontend_active():
            return super()._menu(controller, obs, now)

        # Once Cross has created a transaction, let the existing verified menu
        # navigator own retries/destination confirmation. Never start a second
        # carousel action while that transaction is pending.
        if self.menu.pending is not None:
            return super()._menu(controller, obs, now)

        controller.neutral_sticks()
        self._note_frontend_marker(now)

        if self._play_now_ready(now):
            return self._verified_play_now_cross(controller, now)

        # Once PLAY NOW first appears as the sole top-level title, stop moving long
        # enough to confirm that OCR has actually settled on it. Without this hold,
        # the seeker could immediately nudge away from the target during the same
        # frame that created the candidate.
        if self.frontend_last_marker == "PLAYNOW" and not self._highlight_conflicts_with_play_now():
            remaining = max(
                0.0,
                self.frontend_play_now_confirm_seconds
                - max(0.0, now - self.frontend_play_now_candidate_since),
            )
            self.next_action_at = now + min(0.35, max(0.10, remaining))
            self.current_action = (
                f"main menu: PLAY NOW candidate; hold for verification ({remaining:.2f}s)"
            )
            return self.current_action

        if now < self.next_action_at:
            return self.current_action

        # If a direction does not change the selected top-level title for several
        # attempts, rotate axes. This makes the seeker robust to different Madden
        # front-end layouts instead of assuming the menu is always horizontal.
        if self.frontend_unchanged_steps >= self.frontend_axis_stall_steps:
            self.frontend_axis_index = (self.frontend_axis_index + 1) % len(self.SEEK_DIRECTIONS)
            self.frontend_unchanged_steps = 0

        direction = self.SEEK_DIRECTIONS[self.frontend_axis_index]
        marker_before = self.frontend_last_marker or "unresolved"
        controller.tap(direction, 0.07)
        self.frontend_carousel_seek_steps += 1
        self.frontend_unchanged_steps += 1
        self.next_action_at = now + 0.75
        self.current_action = (
            f"main menu: {direction.upper()} seek PLAY NOW "
            f"({self.frontend_carousel_seek_steps}; tile={marker_before})"
        )
        return self.current_action

    def _progress_recover(
        self,
        controller: Controller,
        directive: RuntimeDirective,
        now: float,
    ) -> str:
        if self._frontend_active():
            controller.neutral_sticks()
            self.frontend_recovery_suppressed += 1
            self.runtime_monitor.last_progress_at = now
            self.runtime_monitor.recovery_level = 0
            self.current_action = "main menu: generic recovery suppressed; PLAY NOW seeker owns state"
            return self.current_action
        return super()._progress_recover(controller, directive, now)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "frontend_reacquired": self.frontend_reacquired_at > -1e8,
                "frontend_current_marker": self.frontend_last_marker,
                "frontend_marker_age": (
                    round(max(0.0, ctx.now - self.frontend_marker_changed_at), 2)
                    if self.frontend_marker_changed_at > -1e8
                    else None
                ),
                "frontend_play_now_candidate_age": (
                    round(max(0.0, ctx.now - self.frontend_play_now_candidate_since), 2)
                    if self.frontend_play_now_candidate_since > -1e8
                    else None
                ),
                "frontend_seek_axis": self.SEEK_DIRECTIONS[self.frontend_axis_index],
                "frontend_unchanged_steps": self.frontend_unchanged_steps,
                "frontend_verified_crosses": self.frontend_verified_crosses,
                "frontend_recovery_suppressed": self.frontend_recovery_suppressed,
            }
        )
        return state
