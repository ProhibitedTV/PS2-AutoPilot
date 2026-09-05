from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .guitar_hero_types import GuitarHeroScreen
from .guitar_hero_v2 import GuitarHeroV2Profile


class GuitarHeroV3Profile(GuitarHeroV2Profile):
    """V3: own GH1's first-run controller instruction card safely.

    The live first-run screen titled "USING THE GUITAR CONTROLLER" contains several
    colored guitar/fret drawings in the same lower-half search region used by V2's
    note-highway detector. Those decorations can accidentally form a plausible
    five-color receptor layout, causing the profile to claim GAMEPLAY and wait forever.

    V3 gives the instruction-card topology priority over note-highway vision. The card
    explicitly says Continue = Green button; on GH1's DualShock path Green is L2. We
    therefore emit one bounded L2 transaction, wait through the visual transition, and
    only then return ownership to V2. This also prevents the global recovery path from
    injecting unrelated Start/Cancel inputs while the instruction screen is present.
    """

    policy_version = 3

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.controller_tutorial_threshold = float(
            self.cfg.get("controller_tutorial_threshold", 0.72)
        )
        self.controller_tutorial_settle_seconds = float(
            self.cfg.get("controller_tutorial_settle_seconds", 0.30)
        )
        self.controller_tutorial_retry_seconds = float(
            self.cfg.get("controller_tutorial_retry_seconds", 2.0)
        )
        self.controller_tutorial_max_attempts = max(
            1, int(self.cfg.get("controller_tutorial_max_attempts", 3))
        )
        self.controller_tutorial_transition_grace_seconds = float(
            self.cfg.get("controller_tutorial_transition_grace_seconds", 0.75)
        )

        self._controller_tutorial_active = False
        self._controller_tutorial_since = -1e9
        self._controller_tutorial_last_seen = -1e9
        self._controller_tutorial_last_input = -1e9
        self._controller_tutorial_attempts = 0
        self._controller_tutorial_score = 0.0
        self.controller_tutorial_episodes = 0
        self.controller_tutorial_inputs = 0

    @staticmethod
    def _ratio(mask: np.ndarray) -> float:
        if mask.size == 0:
            return 0.0
        return float(np.count_nonzero(mask) / mask.size)

    def _instruction_card_score(self, frame: np.ndarray) -> float:
        """Detect the distinctive GH1 controller-instruction poster without OCR.

        The first-run card has four simultaneous structural signals that normal note
        highways do not: a broad pale headline banner, dense black headline ink, a
        large green paper/body field, and a green CONTINUE badge at bottom-left.
        Requiring all four keeps decorative stage colors from stealing ownership.
        """

        if frame.size == 0:
            return 0.0
        work = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
        gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        top_hsv = hsv[int(h * 0.05) : int(h * 0.34), int(w * 0.12) : int(w * 0.88)]
        top_gray = gray[int(h * 0.05) : int(h * 0.34), int(w * 0.12) : int(w * 0.88)]
        body_hsv = hsv[int(h * 0.18) : int(h * 0.88), int(w * 0.10) : int(w * 0.88)]
        continue_hsv = hsv[int(h * 0.84) : int(h * 0.99), int(w * 0.02) : int(w * 0.34)]

        top_pale = cv2.inRange(top_hsv, (0, 0, 120), (179, 115, 255))
        body_green = cv2.inRange(body_hsv, (30, 40, 45), (100, 255, 255))
        continue_green = cv2.inRange(continue_hsv, (30, 65, 50), (100, 255, 255))

        pale_ratio = self._ratio(top_pale)
        dark_ratio = float(np.mean(top_gray < 90)) if top_gray.size else 0.0
        body_green_ratio = self._ratio(body_green)
        continue_green_ratio = self._ratio(continue_green)

        # Fail closed unless the card topology is present. The live capture is well
        # above every floor, leaving useful headroom for 4:3/16:9 and filtering.
        if pale_ratio < 0.34:
            return 0.0
        if dark_ratio < 0.085:
            return 0.0
        if body_green_ratio < 0.12:
            return 0.0
        if continue_green_ratio < 0.025:
            return 0.0

        return min(
            1.0,
            min(1.0, pale_ratio / 0.50) * 0.45
            + min(1.0, dark_ratio / 0.18) * 0.20
            + min(1.0, body_green_ratio / 0.24) * 0.20
            + min(1.0, continue_green_ratio / 0.06) * 0.15,
        )

    def _tutorial_evidence(self, ctx: ProfileContext) -> float:
        template_score = 0.0
        if ctx.template is not None and ctx.template.score >= self.template_threshold:
            name = ctx.template.name.lower()
            if any(
                needle in name
                for needle in (
                    "controller_tutorial",
                    "guitar_controller",
                    "using_the_guitar",
                    "controller_instruction",
                )
            ):
                template_score = float(ctx.template.score)
        return max(template_score, self._instruction_card_score(ctx.frame))

    def _tutorial_tick(self, controller: Controller, ctx: ProfileContext, score: float) -> str:
        now = ctx.now
        if not self._controller_tutorial_active:
            self._controller_tutorial_active = True
            self._controller_tutorial_since = now
            self._controller_tutorial_attempts = 0
            self.controller_tutorial_episodes += 1

        self._controller_tutorial_last_seen = now
        self._controller_tutorial_score = score

        # Present this as input-silent presentation to the rest of the runtime while
        # clearing any stale menu transaction left over from entering Quick Play.
        self._update_screen(GuitarHeroScreen.PRESENTATION, now)
        self._release_gameplay_inputs(controller)

        stable_for = now - self._controller_tutorial_since
        since_input = now - self._controller_tutorial_last_input
        ready = stable_for >= self.controller_tutorial_settle_seconds
        retry_ready = (
            self._controller_tutorial_attempts == 0
            or since_input >= self.controller_tutorial_retry_seconds
        )
        if (
            ready
            and retry_ready
            and self._controller_tutorial_attempts < self.controller_tutorial_max_attempts
        ):
            controller.release_all()
            controller.neutral_sticks()
            controller.tap("l2", 0.06)
            self.menu_inputs += 1
            self.controller_tutorial_inputs += 1
            self._controller_tutorial_attempts += 1
            self._controller_tutorial_last_input = now
            return (
                "controller tutorial: Continue with green/L2 "
                f"({self._controller_tutorial_attempts}/{self.controller_tutorial_max_attempts})"
            )

        if self._controller_tutorial_attempts >= self.controller_tutorial_max_attempts:
            return "controller tutorial: L2 retry budget exhausted; wait for visual progress"
        return "controller tutorial settling; wait before green/L2"

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        score = self._tutorial_evidence(ctx)
        self._controller_tutorial_score = score
        if score >= self.controller_tutorial_threshold:
            return self._tutorial_tick(controller, ctx, score)

        if self._controller_tutorial_active:
            age = ctx.now - self._controller_tutorial_last_seen
            if age <= self.controller_tutorial_transition_grace_seconds:
                self._update_screen(GuitarHeroScreen.PRESENTATION, ctx.now)
                self._release_gameplay_inputs(controller)
                return "controller tutorial transition grace; wait for destination"
            self._controller_tutorial_active = False
            self._controller_tutorial_attempts = 0

        return super().tick(controller, ctx)

    def recover(self, controller: Controller) -> str:
        if self._controller_tutorial_active:
            self._release_gameplay_inputs(controller)
            return "Guitar Hero controller tutorial owns recovery; wait for bounded L2 path"
        return super().recover(controller)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "gh_policy_version": self.policy_version,
                "gh_controller_tutorial_score": round(self._controller_tutorial_score, 3),
                "gh_controller_tutorial_active": self._controller_tutorial_active,
                "gh_controller_tutorial_attempts": self._controller_tutorial_attempts,
                "gh_controller_tutorial_episodes": self.controller_tutorial_episodes,
                "gh_controller_tutorial_inputs": self.controller_tutorial_inputs,
            }
        )
        return state
