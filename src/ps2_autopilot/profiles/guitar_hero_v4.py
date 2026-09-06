from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np

from .base import ProfileContext
from .guitar_hero_types import GuitarHeroPhase, GuitarHeroScreen
from .guitar_hero_v3 import GuitarHeroV3Profile


class GuitarHeroV4Profile(GuitarHeroV3Profile):
    """V4: harden GH1 title ownership and reject decorative false highways.

    The retained live title screen exposed two V2/V3 weaknesses at once: the large
    colored Guitar Hero logo could look enough like a fret-receptor layout to claim
    GAMEPLAY, and the same logo/prompt could make the permissive menu fallback claim
    MAIN_MENU. V4 gives the actual title-card topology priority, requires stronger
    receptor evidence for image-only gameplay ownership, and refuses image-only menu
    ownership unless several real text rows are present.
    """

    policy_version = 4

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.title_splash_threshold = float(self.cfg.get("title_splash_threshold", 0.72))
        self.gameplay_receptor_floor = float(self.cfg.get("gameplay_receptor_floor", 0.78))
        self.image_menu_min_rows = max(2, int(self.cfg.get("image_menu_min_rows", 3)))
        self._title_splash_score_value = 0.0
        self.title_splash_episodes = 0
        self._title_splash_active = False

    @staticmethod
    def _ratio(mask: np.ndarray) -> float:
        if mask.size == 0:
            return 0.0
        return float(np.count_nonzero(mask) / mask.size)

    def _title_splash_score(self, frame: np.ndarray) -> float:
        """Detect GH1's PRESS ANY BUTTON title card without OCR.

        The live title has a huge pale/white central logo, a separate bright prompt
        line low in the frame, and a mostly dark brick background. Requiring all three
        signals is much safer than treating arbitrary yellow/white pixels as a menu.
        """

        if frame.size == 0:
            return 0.0
        work = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        logo = hsv[int(h * 0.12) : int(h * 0.72), int(w * 0.17) : int(w * 0.83)]
        prompt = hsv[int(h * 0.72) : int(h * 0.86), int(w * 0.22) : int(w * 0.78)]
        background = gray[int(h * 0.05) : int(h * 0.95), :]

        logo_white = cv2.inRange(logo, (0, 0, 135), (179, 110, 255))
        prompt_white = cv2.inRange(prompt, (0, 0, 140), (179, 115, 255))
        prompt_yellow = cv2.inRange(prompt, (16, 100, 100), (45, 255, 255))

        logo_ratio = self._ratio(logo_white)
        prompt_ratio = self._ratio(prompt_white)
        prompt_yellow_ratio = self._ratio(prompt_yellow)
        dark_ratio = float(np.mean(background < 105)) if background.size else 0.0

        if logo_ratio < 0.10:
            return 0.0
        if prompt_ratio < 0.035:
            return 0.0
        if dark_ratio < 0.55:
            return 0.0
        if prompt_yellow_ratio > 0.055:
            return 0.0

        return min(
            1.0,
            min(1.0, logo_ratio / 0.18) * 0.45
            + min(1.0, prompt_ratio / 0.075) * 0.35
            + min(1.0, dark_ratio / 0.80) * 0.20,
        )

    @staticmethod
    def _image_text_row_count(frame: np.ndarray) -> int:
        if frame.size == 0:
            return 0
        work = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_AREA)
        rows, _ = GuitarHeroV4Profile._text_rows(work)
        return len(rows)

    @staticmethod
    def _text_rows(frame: np.ndarray) -> tuple[list[float], float | None]:
        """Reuse the V2 vision row extractor without coupling to its instance internals."""

        from ps2_autopilot.guitar_hero_vision import GuitarHeroVision

        return GuitarHeroVision._text_rows(frame)

    def _classify(self, ctx: ProfileContext, obs):
        template_name = ctx.template.name if ctx.template is not None else None
        template_screen = None
        if ctx.template is not None and ctx.template.score >= self.template_threshold:
            template_screen = self._template_screen(template_name)

        # Exact retained templates remain authoritative over image-only guards.
        if template_screen is not None:
            self._title_splash_score_value = self._title_splash_score(ctx.frame)
            return template_screen

        title_score = self._title_splash_score(ctx.frame)
        self._title_splash_score_value = title_score
        if title_score >= self.title_splash_threshold and self.phase != GuitarHeroPhase.AWAIT_GAMEPLAY:
            if not self._title_splash_active:
                self.title_splash_episodes += 1
            self._title_splash_active = True
            return GuitarHeroScreen.TITLE
        self._title_splash_active = False

        # A valid GH1 highway has a persistent five-receptor row. Three incidental
        # logo colors are not enough. Preserve V2's hit-confidence metric, but demote
        # image-only GAMEPLAY when the receptor layout itself is weak.
        guarded_obs = obs
        if obs.receptor_confidence < self.gameplay_receptor_floor:
            guarded_obs = replace(obs, gameplay_confidence=0.0)

        screen = super()._classify(ctx, guarded_obs)

        # The old selected-row fallback can find a plausible row from arbitrary logo
        # colors. For image-only main/difficulty ownership require actual stacked text.
        if screen in {GuitarHeroScreen.MAIN_MENU, GuitarHeroScreen.DIFFICULTY}:
            row_count = self._image_text_row_count(ctx.frame)
            if row_count < self.image_menu_min_rows:
                if ctx.motion >= self.presentation_motion_threshold:
                    return GuitarHeroScreen.PRESENTATION
                return GuitarHeroScreen.UNKNOWN
        return screen

    def _tick_non_gameplay(self, controller, ctx: ProfileContext, obs):
        if self.screen == GuitarHeroScreen.TITLE:
            # Reacquire the boot route even if an earlier false positive already moved
            # the state machine into MENU/SETLIST/GAMEPLAY during a live session.
            self.route_stage = "boot"
            self.phase = GuitarHeroPhase.BOOT
            self._song_active = False
        return super()._tick_non_gameplay(controller, ctx, obs)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "gh_policy_version": self.policy_version,
                "gh_title_splash_score": round(self._title_splash_score_value, 3),
                "gh_title_splash_active": self._title_splash_active,
                "gh_title_splash_episodes": self.title_splash_episodes,
                "gh_gameplay_receptor_floor": self.gameplay_receptor_floor,
            }
        )
        return state
