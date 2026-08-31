from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.madden_menu import MaddenScreen, MenuAssessment

from .base import ProfileContext
from .madden2005 import MaddenPhase, PlayIntent, Possession, QueuedTap
from .madden2005_v28 import Madden2005V28Profile


class Madden2005V29Profile(Madden2005V28Profile):
    """Turn live UNKNOWN evidence into narrow, football-safe reacquisition.

    The V27/V28 live stack exposed two distinct OCR failure modes:

    * title text can degrade from ``PRESSSTART BUTTON`` to separate ``PRESS`` and
      ``BUTTON`` tokens while MADDEN/2005 remain visible;
    * Madden's defensive formation screen can lose essentially all useful OCR even
      though the lower-third playbook chrome is visually unmistakable.

    V29 does not weaken the existing UNKNOWN safety policy. Instead it adds two
    narrow evidence channels:

    * degraded Madden-title evidence may reacquire TITLE;
    * while a game is already known active, the characteristic red-left/dark-right
      playbook chrome may reacquire PLAYCALL.

    When that visual play-call evidence is strong but OCR still cannot identify a
    formation or named play, V29 uses Madden's own Ask Madden recommendations once
    instead of falling back to the legacy random formation macro. If the suggested
    play names remain unreadable, one of the three game-recommended cards is chosen.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.playcall_visual_red_ratio = max(
            0.40, min(0.90, float(cfg.get("playcall_visual_red_ratio", 0.58)))
        )
        self.playcall_visual_right_dark_ratio = max(
            0.45, min(0.95, float(cfg.get("playcall_visual_right_dark_ratio", 0.66)))
        )
        self.visual_playcall_signature = False
        self.visual_playcall_red_ratio = 0.0
        self.visual_playcall_right_dark_ratio = 0.0
        self.visual_playcall_reacquisitions = 0
        self.ask_madden_bootstraps = 0
        self.ask_madden_recommendation_fallbacks = 0
        self.ask_madden_attempted = False

    @classmethod
    def _compact_root_screen(cls, text: str | None) -> MaddenScreen | None:
        screen = super()._compact_root_screen(text)
        if screen is not None:
            return screen
        compact = cls._compact(text)
        # Live OCR sometimes drops START entirely while preserving enough of the
        # surrounding title prompt to make the root screen unambiguous.
        if (
            "MADDEN" in compact
            and "2005" in compact
            and "PRESS" in compact
            and "BUTTON" in compact
        ):
            return MaddenScreen.TITLE
        return None

    @staticmethod
    def _playcall_chrome_metrics(frame: np.ndarray) -> tuple[float, float]:
        if frame is None or frame.size == 0 or frame.ndim != 3:
            return 0.0, 0.0
        h, w = frame.shape[:2]
        if h < 120 or w < 200:
            return 0.0, 0.0

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        y0 = int(h * 0.64)
        y1 = max(y0 + 1, int(h * 0.96))
        split = int(w * 0.39)
        x1 = max(split + 1, int(w * 0.98))

        left = hsv[y0:y1, :split]
        right = hsv[y0:y1, split:x1]
        if left.size == 0 or right.size == 0:
            return 0.0, 0.0

        lh, ls, lv = cv2.split(left)
        _, _, rv = cv2.split(right)
        red = (((lh < 15) | (lh > 170)) & (ls > 80) & (lv > 50)).mean()
        right_dark = (rv < 100).mean()
        return float(red), float(right_dark)

    def _looks_like_visual_playcall(self, frame: np.ndarray) -> bool:
        red, right_dark = self._playcall_chrome_metrics(frame)
        self.visual_playcall_red_ratio = red
        self.visual_playcall_right_dark_ratio = right_dark
        return (
            red >= self.playcall_visual_red_ratio
            and right_dark >= self.playcall_visual_right_dark_ratio
        )

    def _apply_visual_playcall_reacquisition(self, now: float) -> None:
        already_owned = self.phase == MaddenPhase.PLAYCALL and self.visual_playcall_signature
        self.menu_assessment = MenuAssessment(
            MaddenScreen.PLAYCALL,
            0.93,
            (
                "v29 visual playcall chrome; "
                f"red={self.visual_playcall_red_ratio:.2f}, "
                f"right_dark={self.visual_playcall_right_dark_ratio:.2f}"
            ),
        )
        self.pregame_active = False
        self.pregame_presentation = False
        self.pregame_reason = None
        self._set_phase_immediate(MaddenPhase.PLAYCALL, now)
        self.runtime_monitor.last_progress_at = now
        self.runtime_monitor.recovery_level = 0
        self.runtime_monitor.next_recovery_at = 0.0
        if not already_owned:
            self.visual_playcall_reacquisitions += 1

    def _observe(self, ctx: ProfileContext):
        obs = super()._observe(ctx)
        screen = self.menu_assessment.screen
        signature = False

        # Never promote arbitrary red UI to PLAYCALL. The V21 active-game latch is
        # a required second factor, and recognized semantic screens keep priority.
        if (
            screen in {MaddenScreen.UNKNOWN, MaddenScreen.DIALOG}
            and self._recent_active_gameplay(ctx.now)
        ):
            signature = self._looks_like_visual_playcall(ctx.frame)
            if signature:
                self._apply_visual_playcall_reacquisition(ctx.now)

        self.visual_playcall_signature = signature
        return obs

    def _transition_phase(self, new_phase: MaddenPhase, now: float) -> None:
        old = self.phase
        super()._transition_phase(new_phase, now)
        if self.phase == old:
            return
        if new_phase == MaddenPhase.PLAYCALL:
            self.ask_madden_attempted = False
        elif old == MaddenPhase.PLAYCALL:
            self.visual_playcall_signature = False

    def _plan_current_playcall(self) -> None:
        if self.possession == Possession.DEFENSE and self.possession_confidence >= 0.55:
            self.planned_play = PlayIntent.DEFENSE
            self.playcall_decision_reason = f"defense {self._defense_style()} by down/distance"
        else:
            self.planned_play = self._choose_offensive_intent()

    def _arm_playcall(self, now: float) -> None:
        if not self.visual_playcall_signature:
            super()._arm_playcall(now)
            return

        self.queue.clear()
        self._plan_current_playcall()

        # If OCR recovered enough information after the visual reacquisition, keep
        # the richer V26 semantic policy rather than forcing Ask Madden.
        if self._visible_play_candidates(self.planned_play) or super()._formation_level():
            super()._arm_playcall(now)
            return

        if not self.ask_madden_attempted:
            # On Madden 2005's formation screen Square is the fixed Ask Madden path.
            # This delegates personnel/formation sanity to the game's own coach AI
            # when our small-text OCR cannot honestly read the opponent package.
            self.queue.append(QueuedTap(now, "square"))
            self.ask_madden_attempted = True
            self.ask_madden_bootstraps += 1
            self.playcall_armed = True
            self.playcall_selection_mode = "ask-madden-bootstrap"
            self.playcall_selected_label = "ASKMADDEN"
            self.playcall_selected_button = "square"
            self.next_action_at = now + max(0.90, self.ocr.interval_seconds + 0.18)
            self.current_action = (
                f"playcall: OCR-sparse visual screen -> Ask Madden once "
                f"({self.planned_play.value})"
            )
            return

        # If the recommendation card names remain unreadable, choose among Madden's
        # three recommendations rather than resurrecting the old random formation
        # navigation macro. This is bounded uncertainty over coach-approved plays.
        button = self.rng.choice(["square", "cross", "circle"])
        self.queue.append(QueuedTap(now, button))
        self.ask_madden_recommendation_fallbacks += 1
        self.playcall_armed = True
        self.playcall_selection_mode = "ask-madden-recommendation"
        self.playcall_selected_label = "MADDENRECOMMENDATION"
        self.playcall_selected_button = button
        self.next_action_at = now + self.playcall_action_seconds
        self.current_action = (
            f"playcall: choose Madden recommendation -> {button} "
            f"({self.planned_play.value})"
        )

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "madden_policy_version": "v29",
                "visual_playcall_signature": self.visual_playcall_signature,
                "visual_playcall_red_ratio": round(self.visual_playcall_red_ratio, 3),
                "visual_playcall_right_dark_ratio": round(
                    self.visual_playcall_right_dark_ratio, 3
                ),
                "visual_playcall_reacquisitions": self.visual_playcall_reacquisitions,
                "ask_madden_bootstraps": self.ask_madden_bootstraps,
                "ask_madden_recommendation_fallbacks": (
                    self.ask_madden_recommendation_fallbacks
                ),
                "ask_madden_attempted": self.ask_madden_attempted,
            }
        )
        return state
