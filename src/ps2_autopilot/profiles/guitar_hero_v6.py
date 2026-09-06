from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .guitar_hero_types import GuitarHeroPhase, GuitarHeroScreen
from .guitar_hero_v5 import GuitarHeroV5Profile


class GuitarHeroV6Profile(GuitarHeroV5Profile):
    """V6: own GH1's first-run ``NAME YOUR BAND`` setup card.

    The live card appears immediately after the controller-instruction screen and is
    intentionally styled like the rest of GH1's paper UI.  V3's broad tutorial
    detector therefore also accepted it as ``USING THE GUITAR CONTROLLER``: pale
    paper, dense dark text and a green action badge are present on both screens.

    The band-name card has one decisive extra cue: a red DELETE badge next to the
    green NEXT badge.  V6 uses that two-button footer plus the paper/title topology
    to give this screen priority over the inherited tutorial detector, then follows
    the card's explicit instruction and presses START to accept the default name.
    """

    policy_version = 6

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.band_name_threshold = float(self.cfg.get("band_name_threshold", 0.70))
        self.band_name_tutorial_guard_threshold = float(
            self.cfg.get("band_name_tutorial_guard_threshold", 0.55)
        )
        self.band_name_settle_seconds = float(self.cfg.get("band_name_settle_seconds", 0.30))
        self.band_name_retry_seconds = float(self.cfg.get("band_name_retry_seconds", 2.0))
        self.band_name_max_attempts = max(1, int(self.cfg.get("band_name_max_attempts", 3)))
        self.band_name_transition_grace_seconds = float(
            self.cfg.get("band_name_transition_grace_seconds", 0.75)
        )

        self._band_name_active = False
        self._band_name_since = -1e9
        self._band_name_last_seen = -1e9
        self._band_name_last_input = -1e9
        self._band_name_attempts = 0
        self._band_name_score_value = 0.0
        self.band_name_episodes = 0
        self.band_name_inputs = 0

    @staticmethod
    def _ratio(mask: np.ndarray) -> float:
        if mask.size == 0:
            return 0.0
        return float(np.count_nonzero(mask) / mask.size)

    def _band_name_score(self, frame: np.ndarray) -> float:
        """Detect the retained NAME YOUR BAND card without OCR.

        Four independent regions are required: a broad low-saturation paper field,
        dense dark headline ink, the green NEXT footer badge, and the adjacent red
        DELETE badge.  The red footer cue deliberately separates this card from the
        controller tutorial that previously stole ownership.
        """

        if frame.size == 0:
            return 0.0
        work = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        paper_hsv = hsv[int(h * 0.04) : int(h * 0.96), int(w * 0.10) : int(w * 0.92)]
        headline_gray = gray[int(h * 0.08) : int(h * 0.34), int(w * 0.22) : int(w * 0.82)]
        green_footer = hsv[int(h * 0.82) : int(h * 0.99), : int(w * 0.32)]
        red_footer = hsv[
            int(h * 0.82) : int(h * 0.99),
            int(w * 0.18) : int(w * 0.54),
        ]

        # GH1's paper is yellow/green-beige rather than pure white.  Keep this range
        # deliberately broad across filtering/aspect modes, but rely on footer colors
        # to make ownership specific.
        paper = cv2.inRange(paper_hsv, (15, 8, 70), (100, 190, 255))
        green = cv2.inRange(green_footer, (30, 65, 45), (100, 255, 255))
        red_a = cv2.inRange(red_footer, (0, 90, 45), (12, 255, 255))
        red_b = cv2.inRange(red_footer, (168, 90, 45), (179, 255, 255))
        red = cv2.bitwise_or(red_a, red_b)

        paper_ratio = self._ratio(paper)
        headline_dark_ratio = (
            float(np.mean(headline_gray < 105)) if headline_gray.size else 0.0
        )
        green_ratio = self._ratio(green)
        red_ratio = self._ratio(red)

        if paper_ratio < 0.35:
            return 0.0
        if headline_dark_ratio < 0.08:
            return 0.0
        if green_ratio < 0.025:
            return 0.0
        if red_ratio < 0.008:
            return 0.0

        return min(
            1.0,
            min(1.0, paper_ratio / 0.60) * 0.35
            + min(1.0, headline_dark_ratio / 0.20) * 0.25
            + min(1.0, green_ratio / 0.06) * 0.20
            + min(1.0, red_ratio / 0.02) * 0.20,
        )

    def _band_name_evidence(self, ctx: ProfileContext) -> float:
        template_score = 0.0
        if ctx.template is not None and ctx.template.score >= self.template_threshold:
            name = ctx.template.name.lower()
            if any(
                needle in name
                for needle in ("name_your_band", "band_name", "save_game_name")
            ):
                template_score = float(ctx.template.score)
        return max(template_score, self._band_name_score(ctx.frame))

    def _tutorial_evidence(self, ctx: ProfileContext) -> float:
        # A partially filtered band-name frame can dip just below its ownership
        # threshold while still strongly satisfying V3's broader tutorial detector.
        # Suppress that known collision before delegating to V5/V3.
        band_score = self._band_name_evidence(ctx)
        if band_score >= self.band_name_tutorial_guard_threshold:
            return 0.0
        return super()._tutorial_evidence(ctx)

    def _band_name_tick(self, controller: Controller, ctx: ProfileContext, score: float) -> str:
        now = ctx.now
        if not self._band_name_active:
            self._band_name_active = True
            self._band_name_since = now
            self._band_name_attempts = 0
            self.band_name_episodes += 1

        self._band_name_last_seen = now
        self._band_name_score_value = score

        # The previous live screen may have left V5's tutorial latch active.  This
        # screen has stronger evidence, so explicitly revoke that ownership.
        self._controller_tutorial_active = False
        self._controller_tutorial_attempts = 0
        self.route_stage = "boot"
        self.phase = GuitarHeroPhase.BOOT
        self._update_screen(GuitarHeroScreen.PRESENTATION, now)
        self._release_gameplay_inputs(controller)

        stable_for = now - self._band_name_since
        since_input = now - self._band_name_last_input
        ready = stable_for >= self.band_name_settle_seconds
        retry_ready = self._band_name_attempts == 0 or since_input >= self.band_name_retry_seconds

        if ready and retry_ready and self._band_name_attempts < self.band_name_max_attempts:
            controller.release_all()
            controller.neutral_sticks()
            controller.tap("start", 0.08)
            self.menu_inputs += 1
            self.band_name_inputs += 1
            self._band_name_attempts += 1
            self._band_name_last_input = now
            return (
                "band name: press Start to accept default name "
                f"({self._band_name_attempts}/{self.band_name_max_attempts})"
            )

        if self._band_name_attempts >= self.band_name_max_attempts:
            return "band name: Start retry budget exhausted; wait for visual progress"
        return "band name settling; wait before Start"

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        score = self._band_name_evidence(ctx)
        self._band_name_score_value = score
        if score >= self.band_name_threshold:
            return self._band_name_tick(controller, ctx, score)

        if self._band_name_active:
            age = ctx.now - self._band_name_last_seen
            if age <= self.band_name_transition_grace_seconds:
                self.route_stage = "boot"
                self.phase = GuitarHeroPhase.BOOT
                self._update_screen(GuitarHeroScreen.PRESENTATION, ctx.now)
                self._release_gameplay_inputs(controller)
                return "band name transition grace; wait for destination"
            self._band_name_active = False
            self._band_name_attempts = 0

        return super().tick(controller, ctx)

    def recover(self, controller: Controller) -> str:
        if self._band_name_active:
            self._release_gameplay_inputs(controller)
            return "Guitar Hero band-name setup owns recovery; wait for bounded Start path"
        return super().recover(controller)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "gh_policy_version": self.policy_version,
                "gh_band_name_score": round(self._band_name_score_value, 3),
                "gh_band_name_active": self._band_name_active,
                "gh_band_name_attempts": self._band_name_attempts,
                "gh_band_name_episodes": self.band_name_episodes,
                "gh_band_name_inputs": self.band_name_inputs,
            }
        )
        return state
