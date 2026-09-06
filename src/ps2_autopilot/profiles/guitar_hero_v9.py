from __future__ import annotations

from dataclasses import replace

import cv2
import numpy as np

from .base import ProfileContext
from .guitar_hero_types import GuitarHeroPhase, GuitarHeroScreen
from .guitar_hero_v8 import GuitarHeroV8Profile


class GuitarHeroV9Profile(GuitarHeroV8Profile):
    """V9: own the live failure card, enforce configured difficulty, and mask unused frets.

    The retained V8 failure capture exposed three independent lifecycle problems:

    * the live SONG FAILED card was being mistaken for the title screen after a
      supervisor restart, so the policy pressed Start instead of choosing a failure
      action;
    * config requested Easy, but the failed run reports MEDIUM.  The stylized GH1
      difficulty poster defeats the legacy selected-row detector, and the old fallback
      incorrectly assumed a fresh Quick Play screen always starts on Easy;
    * Easy/Medium do not use all five fret lanes, so visual noise on disabled lanes
      should never become an input during calibration.

    V9 keeps V8's hardened highway tracker, adds explicit retained-topology ownership
    for the failure card, homes the difficulty selector to the top before applying the
    configured difficulty, and masks lanes that do not exist at that difficulty.
    """

    policy_version = 9

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.failed_card_threshold = float(self.cfg.get("failed_card_threshold", 0.72))
        self.failed_song_action = str(self.cfg.get("failed_song_action", "new_song")).strip().lower()
        if self.failed_song_action not in {"retry", "new_song"}:
            raise ValueError("Guitar Hero failed_song_action must be retry or new_song")
        self.difficulty_home_presses = max(3, int(self.cfg.get("difficulty_home_presses", 3)))
        self.active_lane_count = {"easy": 3, "medium": 4, "hard": 5, "expert": 5}[
            self.difficulty
        ]

        self._failed_card_score_value = 0.0
        self._failed_card_active = False
        self.failed_card_episodes = 0
        self.difficulty_home_transactions = 0
        self.inactive_lane_hits_suppressed = 0

    @staticmethod
    def _mask_ratio(mask: np.ndarray) -> float:
        if mask.size == 0:
            return 0.0
        return float(np.count_nonzero(mask) / mask.size)

    def _failed_card_score(self, frame: np.ndarray) -> float:
        """Recognize the retained GH1 SONG FAILED card without OCR.

        The card has a large dark central plaque with dense white type, a saturated
        yellow RETRY row, a green CONTINUE footer badge, and notably no adjacent red
        BACK badge.  That final negative cue separates it from the orange difficulty
        poster, which uses the same green footer but also carries a red BACK badge.
        """

        if frame.size == 0:
            return 0.0

        work = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
        h, w = hsv.shape[:2]

        plaque = hsv[int(h * 0.13) : int(h * 0.83), int(w * 0.18) : int(w * 0.82)]
        white_area = hsv[int(h * 0.18) : int(h * 0.80), int(w * 0.22) : int(w * 0.78)]
        retry_area = hsv[int(h * 0.53) : int(h * 0.70), int(w * 0.29) : int(w * 0.73)]
        green_footer = hsv[int(h * 0.88) : int(h * 0.99), int(w * 0.10) : int(w * 0.32)]
        red_footer = hsv[int(h * 0.88) : int(h * 0.99), int(w * 0.27) : int(w * 0.45)]

        white = cv2.inRange(white_area, (0, 0, 145), (179, 100, 255))
        yellow = cv2.inRange(retry_area, (17, 100, 110), (45, 255, 255))
        green = cv2.inRange(green_footer, (30, 65, 45), (100, 255, 255))
        red_lo = cv2.inRange(red_footer, (0, 90, 45), (12, 255, 255))
        red_hi = cv2.inRange(red_footer, (168, 90, 45), (179, 255, 255))
        red = cv2.bitwise_or(red_lo, red_hi)

        dark_ratio = float(np.mean(plaque[:, :, 2] < 85)) if plaque.size else 0.0
        white_ratio = self._mask_ratio(white)
        yellow_ratio = self._mask_ratio(yellow)
        green_ratio = self._mask_ratio(green)
        red_ratio = self._mask_ratio(red)

        if dark_ratio < 0.42:
            return 0.0
        if white_ratio < 0.11:
            return 0.0
        if yellow_ratio < 0.055:
            return 0.0
        if green_ratio < 0.018:
            return 0.0
        if red_ratio > 0.045:
            return 0.0

        return min(
            1.0,
            min(1.0, dark_ratio / 0.62) * 0.25
            + min(1.0, white_ratio / 0.22) * 0.28
            + min(1.0, yellow_ratio / 0.14) * 0.30
            + min(1.0, green_ratio / 0.05) * 0.17,
        )

    def _classify(self, ctx: ProfileContext, obs):
        failed_score = self._failed_card_score(ctx.frame)
        self._failed_card_score_value = failed_score
        if failed_score >= self.failed_card_threshold:
            if not self._failed_card_active:
                self.failed_card_episodes += 1
            self._failed_card_active = True
            # A restarted supervisor can arrive here with BOOT/title state even though
            # the emulator is already sitting on SONG FAILED.  Failure ownership must
            # therefore preempt all inherited title/difficulty heuristics.
            self._title_splash_active = False
            self._difficulty_card_active = False
            return GuitarHeroScreen.FAILED

        self._failed_card_active = False
        return super()._classify(ctx, obs)

    def _play_tick(self, controller, obs, now: float) -> str:
        strengths = list(obs.hit_strengths)
        sustains = list(obs.sustains)
        for index in range(self.active_lane_count, 5):
            if strengths[index] > 0.0:
                self.inactive_lane_hits_suppressed += 1
            strengths[index] = 0.0
            sustains[index] = False
        filtered = replace(obs, hit_strengths=tuple(strengths), sustains=tuple(sustains))
        return super()._play_tick(controller, filtered, now)

    def _tick_difficulty_poster(self, controller, ctx: ProfileContext, obs) -> str:
        now = ctx.now
        self.phase = GuitarHeroPhase.MENU
        self.route_stage = "difficulty"
        if not self._screen_stable(now):
            return "difficulty poster settling"

        tag = f"difficulty_home_to_{self.difficulty}"
        if self._menu_plan_tag != tag:
            # GH1 remembers the last selected difficulty.  V7's generic row detector
            # cannot reliably read the stylized poster, so never assume Easy.  Three
            # Up presses are enough to clamp any of the four rows to Easy, then move
            # down to the configured target and confirm.
            actions = ["up"] * self.difficulty_home_presses
            actions += ["down"] * self.difficulty_index
            actions += ["confirm"]
            self._queue_plan(tag, actions)
            self.difficulty_home_transactions += 1

        action = self._drive_menu_queue(controller, obs, now)
        if action is not None:
            if not self._menu_queue and action.endswith("confirm"):
                self._first_difficulty_selection = False
                self.phase = GuitarHeroPhase.AWAIT_GAMEPLAY
                self.route_stage = "song"
                self._launch_started_at = now
            return action
        return f"difficulty {self.difficulty} selected"

    def _tick_failed_card(self, controller, ctx: ProfileContext, obs) -> str:
        now = ctx.now
        self._release_gameplay_inputs(controller)
        self.phase = GuitarHeroPhase.POST_SONG
        self.route_stage = "post_song"
        if self._song_active:
            self.songs_failed += 1
            self._song_active = False
        if not self._screen_stable(now):
            return "song failed screen settling"

        if self.failed_song_action == "new_song":
            tag = "failed_new_song"
            actions = ["down", "confirm"]
        else:
            tag = "failed_retry"
            actions = ["confirm"]

        self._queue_plan(tag, actions)
        action = self._drive_menu_queue(controller, obs, now)
        if action is not None:
            if not self._menu_queue and action.endswith("confirm"):
                if self.failed_song_action == "new_song":
                    # Return through setlist/difficulty so V9 can enforce the configured
                    # difficulty instead of retrying the retained Medium run forever.
                    self.phase = GuitarHeroPhase.MENU
                    self.route_stage = "setlist"
                    self._song_advance_pending = 0
                else:
                    self.phase = GuitarHeroPhase.AWAIT_GAMEPLAY
                    self.route_stage = "song"
                    self._launch_started_at = now
            return action
        return f"failed song {self.failed_song_action} selected; wait for transition"

    def _tick_non_gameplay(self, controller, ctx: ProfileContext, obs) -> str:
        if self.screen == GuitarHeroScreen.FAILED and self._failed_card_active:
            return self._tick_failed_card(controller, ctx, obs)
        if self.screen == GuitarHeroScreen.DIFFICULTY and self._difficulty_card_active:
            return self._tick_difficulty_poster(controller, ctx, obs)
        return super()._tick_non_gameplay(controller, ctx, obs)

    def recover(self, controller) -> str:
        if self._failed_card_active:
            self._release_gameplay_inputs(controller)
            return "Guitar Hero failed card owns recovery; wait for failure transaction"
        return super().recover(controller)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "gh_policy_version": self.policy_version,
                "gh_failed_card_score": round(self._failed_card_score_value, 3),
                "gh_failed_card_active": self._failed_card_active,
                "gh_failed_card_episodes": self.failed_card_episodes,
                "gh_failed_song_action": self.failed_song_action,
                "gh_active_lane_count": self.active_lane_count,
                "gh_difficulty_home_transactions": self.difficulty_home_transactions,
                "gh_inactive_lane_hits_suppressed": self.inactive_lane_hits_suppressed,
            }
        )
        return state
