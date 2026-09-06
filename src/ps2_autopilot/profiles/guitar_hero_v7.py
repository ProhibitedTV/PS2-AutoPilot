from __future__ import annotations

import cv2
import numpy as np

from .base import ProfileContext
from .guitar_hero_types import GuitarHeroPhase, GuitarHeroScreen
from .guitar_hero_v6 import GuitarHeroV6Profile


class GuitarHeroV7Profile(GuitarHeroV6Profile):
    """V7: recognize GH1's real Quick Play difficulty poster.

    The retained live difficulty screen is not a conventional four-row white/yellow
    menu.  It is an orange/brown band poster with large black silhouettes, white
    DIFFICULTY/EASY/MEDIUM/HARD/EXPERT lettering, and the standard green CONTINUE +
    red BACK footer.  V2's generic row detector therefore reports too little
    difficulty evidence and, after the setlist transition temporarily moves the phase
    to PRESENTATION, the runtime falls back to bounded boot Start probes.

    V7 gives the real poster topology explicit ownership before the inherited generic
    classifier.  Once claimed, V2's existing difficulty transaction remains in charge:
    fresh Quick Play opens on Easy, so the configured difficulty can be selected and
    confirmed without changing gameplay semantics.
    """

    policy_version = 7

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.difficulty_card_threshold = float(
            self.cfg.get("difficulty_card_threshold", 0.72)
        )
        self._difficulty_card_score_value = 0.0
        self._difficulty_card_active = False
        self.difficulty_card_episodes = 0

    @staticmethod
    def _ratio(mask: np.ndarray) -> float:
        if mask.size == 0:
            return 0.0
        return float(np.count_nonzero(mask) / mask.size)

    def _difficulty_card_score(self, frame: np.ndarray) -> float:
        """Detect the retained GH1 difficulty poster without OCR.

        Five simultaneous cues make this much more specific than the old row-count
        heuristic: an orange/brown central poster, a large dark silhouette mass,
        bright lower-poster lettering, a green CONTINUE footer badge, and an adjacent
        red BACK footer badge.  The combination rejects the earlier controller,
        band-name, save and title cards seen in the same live first-run lifecycle.
        """

        if frame.size == 0:
            return 0.0

        work = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
        h, w = hsv.shape[:2]

        poster = hsv[int(h * 0.03) : int(h * 0.88), int(w * 0.24) : int(w * 0.76)]
        lower_text = hsv[int(h * 0.53) : int(h * 0.82), int(w * 0.28) : int(w * 0.72)]
        green_footer = hsv[int(h * 0.88) : int(h * 0.99), int(w * 0.10) : int(w * 0.30)]
        red_footer = hsv[int(h * 0.88) : int(h * 0.99), int(w * 0.27) : int(w * 0.43)]

        poster_orange = cv2.inRange(poster, (3, 60, 50), (30, 255, 255))
        lower_white = cv2.inRange(lower_text, (0, 0, 140), (179, 110, 255))
        footer_green = cv2.inRange(green_footer, (30, 65, 45), (100, 255, 255))
        red_lo = cv2.inRange(red_footer, (0, 90, 45), (12, 255, 255))
        red_hi = cv2.inRange(red_footer, (168, 90, 45), (179, 255, 255))
        footer_red = cv2.bitwise_or(red_lo, red_hi)

        orange_ratio = self._ratio(poster_orange)
        dark_ratio = float(np.mean(poster[:, :, 2] < 90)) if poster.size else 0.0
        white_ratio = self._ratio(lower_white)
        green_ratio = self._ratio(footer_green)
        red_ratio = self._ratio(footer_red)

        # Fail closed unless the complete retained topology is present.  The current
        # live capture sits comfortably above every floor.
        if orange_ratio < 0.22:
            return 0.0
        if dark_ratio < 0.35:
            return 0.0
        if white_ratio < 0.05:
            return 0.0
        if green_ratio < 0.018:
            return 0.0
        if red_ratio < 0.12:
            return 0.0

        return min(
            1.0,
            min(1.0, orange_ratio / 0.40) * 0.28
            + min(1.0, dark_ratio / 0.55) * 0.24
            + min(1.0, white_ratio / 0.12) * 0.18
            + min(1.0, green_ratio / 0.05) * 0.12
            + min(1.0, red_ratio / 0.25) * 0.18,
        )

    def _difficulty_card_evidence(self, ctx: ProfileContext) -> float:
        template_score = 0.0
        if ctx.template is not None and ctx.template.score >= self.template_threshold:
            name = ctx.template.name.lower()
            if any(
                needle in name
                for needle in (
                    "difficulty",
                    "choose_difficulty",
                    "difficulty_select",
                )
            ):
                template_score = float(ctx.template.score)
        return max(template_score, self._difficulty_card_score(ctx.frame))

    def _classify(self, ctx: ProfileContext, obs):
        score = self._difficulty_card_evidence(ctx)
        self._difficulty_card_score_value = score
        if score >= self.difficulty_card_threshold and self.phase != GuitarHeroPhase.PLAYING:
            if not self._difficulty_card_active:
                self.difficulty_card_episodes += 1
            self._difficulty_card_active = True
            # Explicitly revoke title ownership from V4's broad title-card guard.  The
            # runtime will set MENU/difficulty ownership on the same tick.
            self._title_splash_active = False
            return GuitarHeroScreen.DIFFICULTY

        self._difficulty_card_active = False
        return super()._classify(ctx, obs)

    def recover(self, controller) -> str:
        if self._difficulty_card_active:
            self._release_gameplay_inputs(controller)
            return "Guitar Hero difficulty poster owns recovery; wait for menu transaction"
        return super().recover(controller)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "gh_policy_version": self.policy_version,
                "gh_difficulty_card_score": round(self._difficulty_card_score_value, 3),
                "gh_difficulty_card_active": self._difficulty_card_active,
                "gh_difficulty_card_episodes": self.difficulty_card_episodes,
            }
        )
        return state
