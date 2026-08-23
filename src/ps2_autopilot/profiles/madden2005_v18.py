from __future__ import annotations

import math

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_menu import MaddenScreen
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005_v17 import Madden2005V17Profile


class Madden2005V18Profile(Madden2005V17Profile):
    """v0.7 performance/broadcast polish without disturbing the lifecycle loop.

    Recurring OCR is now performed by MaddenOCR's latest-frame background worker;
    this profile exposes worker freshness/backpressure telemetry, keeps the most
    recent trustworthy score-bug values latched for the public overlay, and uses
    high-confidence controlled-player geometry to avoid wasting defensive tackle
    inputs while the selected defender is still far from the action target.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        # v9 constructs the OCR instance so older profiles stay compatible. v18
        # owns the new runtime mode switches before the first frame is observed.
        self.ocr.async_enabled = bool(cfg.get("ocr_async_enabled", True))
        self.ocr.bootstrap_sync = bool(cfg.get("ocr_bootstrap_sync", True))

        self.defense_contact_distance = max(
            0.12, min(0.80, float(cfg.get("defense_contact_distance", 0.34)))
        )
        self.defense_far_action_delay_seconds = max(
            0.10, min(0.80, float(cfg.get("defense_far_action_delay_seconds", 0.28)))
        )
        self.defense_action_holds = 0
        self.defense_last_target_distance: float | None = None

        self._broadcast_team_a: str | None = None
        self._broadcast_score_a: int | None = None
        self._broadcast_team_b: str | None = None
        self._broadcast_score_b: int | None = None
        self._team_select_visible = False

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)

        # A new SELECT TEAMS screen is an unambiguous boundary between games.
        # Clear the previous game's latched public score exactly once here.
        team_select = self.menu_assessment.screen == MaddenScreen.TEAM_SELECT
        if team_select and not self._team_select_visible:
            self._broadcast_team_a = None
            self._broadcast_score_a = None
            self._broadcast_team_b = None
            self._broadcast_score_b = None
        self._team_select_visible = team_select
        return obs

    def _defense_live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        spatial = self.last_spatial
        self.defense_last_target_distance = None

        if (
            self._spatial_fresh(now)
            and spatial.controlled is not None
            and spatial.controlled.confidence >= self.spatial_controlled_confidence
            and spatial.target_confidence >= self.spatial_target_confidence
        ):
            dx = spatial.target_x - spatial.controlled.x
            dy = spatial.target_y - spatial.controlled.y
            distance = math.hypot(dx, dy)
            self.defense_last_target_distance = distance
            if distance > self.defense_contact_distance and now >= self.next_action_at:
                # Parent policies still perform all steering. Moving the action
                # deadline forward simply prevents a random contact button while
                # high-confidence geometry says the controlled defender is far.
                self.next_action_at = now + self.defense_far_action_delay_seconds
                self.defense_action_holds += 1
                super()._defense_live(controller, obs, now)
                self.current_action = (
                    f"defense: close pursuit ({distance:.2f} from target); hold contact move"
                )
                return self.current_action

        return super()._defense_live(controller, obs, now)

    def _update_broadcast_latch(self) -> None:
        team_a, score_a, team_b, score_b = self._broadcast_score()
        if (
            team_a is not None
            and team_b is not None
            and score_a is not None
            and score_b is not None
        ):
            self._broadcast_team_a = team_a
            self._broadcast_score_a = score_a
            self._broadcast_team_b = team_b
            self._broadcast_score_b = score_b

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        self._update_broadcast_latch()
        state.update(self.ocr.telemetry(ctx.now))
        state.update(
            {
                "broadcast_team_a": self._broadcast_team_a,
                "broadcast_score_a": self._broadcast_score_a,
                "broadcast_team_b": self._broadcast_team_b,
                "broadcast_score_b": self._broadcast_score_b,
                "defense_contact_distance": round(self.defense_contact_distance, 3),
                "defense_target_distance": (
                    None
                    if self.defense_last_target_distance is None
                    else round(self.defense_last_target_distance, 3)
                ),
                "defense_action_holds": self.defense_action_holds,
            }
        )
        return state
