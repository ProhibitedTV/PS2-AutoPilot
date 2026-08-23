from __future__ import annotations

import re

from ps2_autopilot.madden_menu import MaddenScreen, MenuAssessment
from ps2_autopilot.madden_vision import MaddenObservation, MaddenVisualState

from .base import ProfileContext
from .madden2005 import MaddenPhase
from .madden2005_v11 import Madden2005V11Profile


_NFL_NICKNAMES = {
    "49ERS",
    "BEARS",
    "BENGALS",
    "BILLS",
    "BRONCOS",
    "BROWNS",
    "BUCCANEERS",
    "CARDINALS",
    "CHARGERS",
    "CHIEFS",
    "COLTS",
    "COWBOYS",
    "DOLPHINS",
    "EAGLES",
    "FALCONS",
    "GIANTS",
    "JAGUARS",
    "JETS",
    "LIONS",
    "PACKERS",
    "PANTHERS",
    "PATRIOTS",
    "RAIDERS",
    "RAMS",
    "RAVENS",
    "REDSKINS",
    "SAINTS",
    "SEAHAWKS",
    "STEELERS",
    "TEXANS",
    "TITANS",
    "VIKINGS",
}


class Madden2005V12Profile(Madden2005V11Profile):
    """Recognize Madden's postgame score panel even when it never says FINAL.

    Live capture showed Madden sitting at Q4 0:00 on the large two-team score card.
    The older classifier only understood literal FINAL SCORE / GAME OVER text, so
    this valid postgame state could remain presentation/transition indefinitely.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.final_zero_clock_confirm_seconds = max(
            0.25, float(cfg.get("final_zero_clock_confirm_seconds", 0.75))
        )
        self.final_presentation_hold_seconds = max(
            5.0, float(cfg.get("final_presentation_hold_seconds", 12.0))
        )
        self.final_zero_clock_candidate_since = -1e9
        self.final_zero_clock_detected = 0

    def _team_nickname_hits(self) -> int:
        compact_words = {
            re.sub(r"[^A-Z0-9]", "", line.text.upper())
            for line in self.last_ocr.lines
        }
        text = re.sub(r"[^A-Z0-9 ]", " ", self.last_ocr.text.upper())
        hits = 0
        for nickname in _NFL_NICKNAMES:
            if nickname in compact_words or re.search(rf"\b{re.escape(nickname)}\b", text):
                hits += 1
        return hits

    def _looks_like_zero_clock_postgame(self, obs: MaddenObservation) -> bool:
        if self.situation.quarter != 4 or self.situation.clock_seconds != 0:
            return False

        compact = re.sub(r"[^A-Z0-9]", "", self.last_ocr.text.upper())
        if "PICKAPLAY" in compact or "OVERTIME" in compact:
            return False

        # Do not declare the game over while an actual final snap is still moving.
        if obs.state == MaddenVisualState.LIVE_PLAY and obs.green_ratio >= 0.30:
            return False

        # The supplied postgame frame has a large two-team score panel. Requiring
        # two NFL nicknames makes Q4 0:00 field/replay frames much less likely to
        # become false finals while remaining independent of the particular teams.
        return self._team_nickname_hits() >= 2

    def _transition_phase(self, new_phase: MaddenPhase, now: float) -> None:
        old = self.phase
        super()._transition_phase(new_phase, now)
        if self.phase == old:
            return
        if new_phase == MaddenPhase.GAME_OVER:
            self.next_action_at = max(self.next_action_at, now + self.final_presentation_hold_seconds)
            self.current_action = (
                f"final: hold postgame presentation ({self.final_presentation_hold_seconds:.0f}s)"
            )

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)

        if self.phase == MaddenPhase.GAME_OVER:
            return obs

        if self._looks_like_zero_clock_postgame(obs):
            if self.final_zero_clock_candidate_since < -1e8:
                self.final_zero_clock_candidate_since = ctx.now
            age = ctx.now - self.final_zero_clock_candidate_since
            if age >= self.final_zero_clock_confirm_seconds:
                self.menu_assessment = MenuAssessment(
                    MaddenScreen.FINAL,
                    0.98,
                    "Q4 0:00 two-team postgame score panel",
                )
                self._transition_phase(MaddenPhase.GAME_OVER, ctx.now)
                self.final_zero_clock_detected += 1
        else:
            self.final_zero_clock_candidate_since = -1e9

        return obs

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "final_zero_clock_detected": self.final_zero_clock_detected,
                "final_zero_clock_candidate_age": (
                    round(max(0.0, ctx.now - self.final_zero_clock_candidate_since), 2)
                    if self.final_zero_clock_candidate_since > -1e8
                    else None
                ),
                "final_team_nickname_hits": self._team_nickname_hits(),
            }
        )
        return state
