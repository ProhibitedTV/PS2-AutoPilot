from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.guitar_hero_vision_v10 import GuitarHeroVisionV10

from .base import ProfileContext
from .guitar_hero_types import GuitarHeroPhase, GuitarHeroScreen
from .guitar_hero_v9 import GuitarHeroV9Profile


class GuitarHeroV10Profile(GuitarHeroV9Profile):
    """V10: deterministic failure recovery plus temporal note validation."""

    policy_version = 10

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.vision = GuitarHeroVisionV10(self.cfg)
        self.failed_menu_settle_seconds = float(
            self.cfg.get("failed_menu_settle_seconds", 0.18)
        )
        self.failed_confirm_retry_seconds = float(
            self.cfg.get("failed_confirm_retry_seconds", 1.0)
        )
        self.failed_selection_tolerance = float(
            self.cfg.get("failed_selection_tolerance", 0.052)
        )
        self._failed_last_direct_input_at = -1e9
        self._failed_confirm_sent_at = -1e9
        self._failed_selected_index_value: int | None = None
        self.failed_direct_inputs = 0
        self.failed_confirm_retries = 0

    def _failed_card_score(self, frame: np.ndarray) -> float:
        """Recognize SONG FAILED regardless of which of its three rows is selected."""

        if frame.size == 0:
            return 0.0

        work = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
        h, w = hsv.shape[:2]

        plaque = hsv[int(h * 0.13) : int(h * 0.83), int(w * 0.18) : int(w * 0.82)]
        white_area = hsv[int(h * 0.18) : int(h * 0.80), int(w * 0.22) : int(w * 0.78)]
        menu_area = hsv[int(h * 0.53) : int(h * 0.80), int(w * 0.25) : int(w * 0.75)]
        green_footer = hsv[int(h * 0.88) : int(h * 0.99), int(w * 0.10) : int(w * 0.32)]
        red_footer = hsv[int(h * 0.88) : int(h * 0.99), int(w * 0.27) : int(w * 0.45)]

        white = cv2.inRange(white_area, (0, 0, 145), (179, 100, 255))
        yellow = cv2.inRange(menu_area, (17, 100, 105), (47, 255, 255))
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
        if yellow_ratio < 0.025:
            return 0.0
        if green_ratio < 0.018:
            return 0.0
        if red_ratio > 0.045:
            return 0.0

        return min(
            1.0,
            min(1.0, dark_ratio / 0.62) * 0.25
            + min(1.0, white_ratio / 0.22) * 0.28
            + min(1.0, yellow_ratio / 0.10) * 0.30
            + min(1.0, green_ratio / 0.05) * 0.17,
        )

    def _failed_selected_index(self, frame: np.ndarray) -> int | None:
        """Read RETRY / NEW SONG / QUIT from the yellow selected row."""

        if frame.size == 0:
            return None
        work = cv2.resize(frame, (640, 480), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
        h, w = hsv.shape[:2]
        y0, y1 = int(h * 0.54), int(h * 0.80)
        x0, x1 = int(w * 0.25), int(w * 0.75)
        roi = hsv[y0:y1, x0:x1]
        yellow = cv2.inRange(roi, (17, 100, 105), (47, 255, 255))
        ys, _ = np.nonzero(yellow)
        if len(ys) < 80:
            return None
        selected_y = (float(np.median(ys)) + y0) / h
        centers = (0.61, 0.68, 0.75)
        distances = [abs(selected_y - center) for center in centers]
        index = int(np.argmin(distances))
        if distances[index] > self.failed_selection_tolerance:
            return None
        return index

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

        selected = self._failed_selected_index(ctx.frame)
        self._failed_selected_index_value = selected
        if selected is None:
            return "song failed: selected row unreadable; wait"

        if now - self._failed_last_direct_input_at < self.failed_menu_settle_seconds:
            return f"song failed: wait for row {selected} settle"

        target = 1 if self.failed_song_action == "new_song" else 0
        if selected < target:
            controller.tap("down", 0.06)
            self.menu_inputs += 1
            self.failed_direct_inputs += 1
            self._failed_last_direct_input_at = now
            return f"song failed: move selection down {selected}->{selected + 1}"
        if selected > target:
            controller.tap("up", 0.06)
            self.menu_inputs += 1
            self.failed_direct_inputs += 1
            self._failed_last_direct_input_at = now
            return f"song failed: move selection up {selected}->{selected - 1}"

        if now - self._failed_confirm_sent_at < self.failed_confirm_retry_seconds:
            return f"song failed: {self.failed_song_action} confirmed; wait for transition"

        if self._failed_confirm_sent_at > -1e8:
            self.failed_confirm_retries += 1
        controller.tap("confirm", 0.08)
        self.menu_inputs += 1
        self.failed_direct_inputs += 1
        self._failed_last_direct_input_at = now
        self._failed_confirm_sent_at = now
        if self.failed_song_action == "new_song":
            self._song_advance_pending = 0
        return f"song failed: confirm {self.failed_song_action}"

    def _tick_non_gameplay(self, controller, ctx: ProfileContext, obs) -> str:
        if self.screen == GuitarHeroScreen.FAILED and self._failed_card_active:
            return self._tick_failed_card(controller, ctx, obs)
        return super()._tick_non_gameplay(controller, ctx, obs)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        vision = self.vision
        state.update(
            {
                "gh_policy_version": self.policy_version,
                "gh_failed_selected_index": self._failed_selected_index_value,
                "gh_failed_direct_inputs": self.failed_direct_inputs,
                "gh_failed_confirm_retries": self.failed_confirm_retries,
                "gh_approach_confirmed": list(vision.approach_confirmed),
                "gh_note_velocities": [round(float(value), 4) for value in vision.note_velocities],
                "gh_dynamic_trigger_gaps": [
                    round(float(value), 4) for value in vision.dynamic_trigger_gaps
                ],
                "gh_temporal_hits_suppressed": int(vision.temporal_hits_suppressed),
            }
        )
        return state
