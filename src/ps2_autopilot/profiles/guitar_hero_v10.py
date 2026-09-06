from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.guitar_hero_vision_v10 import GuitarHeroVisionV10

from .base import ProfileContext
from .guitar_hero_types import GuitarHeroPhase, GuitarHeroScreen
from .guitar_hero_v9 import GuitarHeroV9Profile


class GuitarHeroV10Profile(GuitarHeroV9Profile):
    """V10: make failure recovery deterministic and require temporal note motion.

    The live V8/V9 calibration proved that getting to a song is no longer enough: we
    need to separate lifecycle reliability from gameplay quality. V10 keeps the V9
    menu route and Easy-difficulty enforcement, but removes the generic transactional
    menu queue from the SONG FAILED card and validates gameplay hits across time.
    """

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

    def _failed_selected_index(self, frame: np.ndarray) -> int | None:
        """Read RETRY / NEW SONG / QUIT from the yellow selected row.

        The failure card is far more stable than the concert background. Restricting
        yellow evidence to the dark central menu plaque lets us read the selected row
        directly instead of assuming RETRY and hoping a generic signature transaction
        notices the highlight move.
        """

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
            # Fail closed rather than blindly walking onto QUIT. The live retained
            # failure card has a strong yellow row; if we cannot read it, preserve the
            # screen and expose that fact in telemetry for the next calibration pass.
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
