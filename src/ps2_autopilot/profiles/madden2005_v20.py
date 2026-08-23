from __future__ import annotations

import re

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_menu import MaddenScreen
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005_v19 import Madden2005V19Profile


class Madden2005V20Profile(Madden2005V19Profile):
    """Rotate Play Now matchups instead of accepting Madden's remembered defaults.

    The v0.7.1 lifecycle was intentionally conservative at SELECT TEAMS: once the
    screen was recognized it immediately confirmed the current matchup. In a 24/7
    stream that makes Madden repeatedly reuse the same teams. v20 gives team select
    exclusive, bounded control long enough to cycle both sides, then randomizes
    whether AutoPilot controls the away or home team on CONTROLLER SELECT.

    The captured Madden 2005 team-select layout opens with the HOME selector active.
    UP/DOWN cycles that team, LEFT moves to the AWAY selector, and UP/DOWN cycles the
    away team. L1/R1 are deliberately never touched so Historic Teams remain out of
    the autonomous rotation.
    """

    name = "madden2005"

    TEAM_MARKERS = (
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
    )

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.team_rotation_enabled = bool(cfg.get("team_rotation_enabled", True))
        self.team_rotation_min_steps = max(1, int(cfg.get("team_rotation_min_steps", 1)))
        self.team_rotation_max_steps = max(
            self.team_rotation_min_steps,
            min(10, int(cfg.get("team_rotation_max_steps", 6))),
        )
        self.team_rotation_action_seconds = max(
            0.22, float(cfg.get("team_rotation_action_seconds", 0.42))
        )
        self.team_rotation_settle_seconds = max(
            0.65, float(cfg.get("team_rotation_settle_seconds", 1.10))
        )

        self.team_select_visible = False
        self.team_rotation_plan: list[str] = []
        self.team_rotation_index = 0
        self.team_rotation_settle_until = 0.0
        self.team_rotation_guard_nudges = 0
        self.team_rotation_games = 0
        self.team_rotation_steps = 0
        self.team_rotation_entry_matchup: tuple[str, str] | None = None
        self.team_rotation_current_matchup: tuple[str, str] | None = None
        self.team_rotation_last_matchup: tuple[str, str] | None = None
        self.team_rotation_home_direction: str | None = None
        self.team_rotation_away_direction: str | None = None
        self.team_rotation_home_steps = 0
        self.team_rotation_away_steps = 0
        self.controlled_side = "home"
        self.controlled_side_moves = 0

    @staticmethod
    def _team_compact(text: str | None) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())

    @classmethod
    def _extract_matchup(cls, text: str | None) -> tuple[str, str] | None:
        """Best-effort away/home nickname pair from OCR, preserving screen order."""
        found: list[str] = []
        for raw in str(text or "").split("|"):
            compact = cls._team_compact(raw)
            if not compact:
                continue
            marker = next((team for team in cls.TEAM_MARKERS if team in compact), None)
            if marker is None or marker in found:
                continue
            found.append(marker)
            if len(found) == 2:
                return found[0], found[1]
        return None

    def _start_team_rotation(self, now: float) -> None:
        self.team_rotation_entry_matchup = self._extract_matchup(self.last_ocr.text)
        self.team_rotation_current_matchup = self.team_rotation_entry_matchup
        self.team_rotation_home_direction = self.rng.choice(("up", "down"))
        self.team_rotation_away_direction = self.rng.choice(("up", "down"))
        self.team_rotation_home_steps = self.rng.randint(
            self.team_rotation_min_steps, self.team_rotation_max_steps
        )
        self.team_rotation_away_steps = self.rng.randint(
            self.team_rotation_min_steps, self.team_rotation_max_steps
        )
        self.controlled_side = self.rng.choice(("away", "home"))

        self.team_rotation_plan = (
            [self.team_rotation_home_direction] * self.team_rotation_home_steps
            + ["left"]
            + [self.team_rotation_away_direction] * self.team_rotation_away_steps
        )
        self.team_rotation_index = 0
        self.team_rotation_settle_until = 0.0
        self.team_rotation_guard_nudges = 0
        self.team_rotation_games += 1
        self.queue.clear()
        self.next_action_at = min(self.next_action_at, now)

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)
        team_select = self.menu_assessment.screen == MaddenScreen.TEAM_SELECT

        if team_select:
            current = self._extract_matchup(self.last_ocr.text)
            if current is not None:
                self.team_rotation_current_matchup = current
            if not self.team_select_visible:
                self.team_select_visible = True
                if self.team_rotation_enabled:
                    self._start_team_rotation(ctx.now)
            self.runtime_monitor.last_progress_at = ctx.now
            self.runtime_monitor.recovery_level = 0
            self.runtime_monitor.next_recovery_at = 0.0
        elif self.team_select_visible:
            if self.team_rotation_current_matchup is not None:
                self.team_rotation_last_matchup = self.team_rotation_current_matchup
            self.team_select_visible = False
            self.team_rotation_plan = []
            self.team_rotation_index = 0
            self.team_rotation_settle_until = 0.0

        return obs

    def _accept_pending_destination(self, screen: MaddenScreen) -> None:
        pending = self.menu.pending
        if pending is not None and screen in pending.expected:
            self.menu.verified_transitions += 1
            self.menu._clear_pending()

    def _rotate_team_select(self, controller: Controller, now: float) -> str:
        screen = MaddenScreen.TEAM_SELECT
        self.menu._seen(screen, now)
        self._accept_pending_destination(screen)
        controller.neutral_sticks()
        self.queue.clear()

        # Honor the transaction's post-Cross settling delay, but do not hand the
        # destination back to the base navigator: it would immediately Cross and
        # bypass rotation on the same tick that verified TEAM SELECT.
        if now < self.menu.next_action_at or now < self.next_action_at:
            return self.current_action

        if self.team_rotation_index < len(self.team_rotation_plan):
            action = self.team_rotation_plan[self.team_rotation_index]
            controller.tap(action, 0.07)
            self.team_rotation_index += 1
            self.team_rotation_steps += 1
            self.next_action_at = now + self.team_rotation_action_seconds
            self.team_rotation_settle_until = 0.0
            self.current_action = (
                f"team select: rotate {action.upper()} "
                f"({self.team_rotation_index}/{len(self.team_rotation_plan)})"
            )
            return self.current_action

        if self.team_rotation_settle_until <= 0.0:
            self.team_rotation_settle_until = now + self.team_rotation_settle_seconds
            self.current_action = "team select: rotation complete; settle and verify matchup"
            return self.current_action
        if now < self.team_rotation_settle_until:
            return self.current_action

        current = self._extract_matchup(self.last_ocr.text)
        if current is not None:
            self.team_rotation_current_matchup = current

        # The random plan always changes both teams by at least one step. This
        # additional bounded nudge covers a missed D-pad input or a stale/repeated
        # matchup read without ever risking an unbounded team-select loop.
        repeated = self.team_rotation_current_matchup is not None and (
            self.team_rotation_current_matchup == self.team_rotation_entry_matchup
            or self.team_rotation_current_matchup == self.team_rotation_last_matchup
        )
        if repeated and self.team_rotation_guard_nudges < 2:
            controller.tap(self.team_rotation_away_direction or "down", 0.07)
            self.team_rotation_guard_nudges += 1
            self.team_rotation_steps += 1
            self.team_rotation_settle_until = now + self.team_rotation_settle_seconds
            self.next_action_at = now + self.team_rotation_action_seconds
            self.current_action = (
                "team select: repeated matchup detected; nudge away team "
                f"({self.team_rotation_guard_nudges}/2)"
            )
            return self.current_action

        self.team_rotation_last_matchup = self.team_rotation_current_matchup
        self.menu._tap(
            controller,
            "cross",
            now,
            screen,
            (MaddenScreen.CONTROLLER_SELECT, MaddenScreen.MATCHUP, MaddenScreen.GAME_SETTINGS),
            delay=1.2,
        )
        matchup = self.team_rotation_current_matchup
        label = "unknown" if matchup is None else f"{matchup[0]} @ {matchup[1]}"
        self.current_action = (
            f"team select: rotated matchup {label}; CROSS once; control={self.controlled_side}"
        )
        self.menu.current_action = self.current_action
        return self.current_action

    def _controller_side(self, controller: Controller, now: float) -> str:
        screen = MaddenScreen.CONTROLLER_SELECT
        self.menu._seen(screen, now)
        self._accept_pending_destination(screen)

        controller.neutral_sticks()
        if now < self.menu.next_action_at or now < self.next_action_at:
            return self.current_action

        if not self.menu.controller_side_moved:
            direction = "left" if self.controlled_side == "away" else "right"
            controller.tap(direction, 0.07)
            self.menu.controller_side_moved = True
            self.controlled_side_moves += 1
            self.menu.next_action_at = now + 0.55
            self.next_action_at = now + 0.55
            self.current_action = f"controller select: choose {self.controlled_side.upper()} side"
            self.menu.current_action = self.current_action
            return self.current_action

        return super()._menu(controller, self.last_observation, now)

    def _menu(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        screen = self.menu_assessment.screen
        if self.team_rotation_enabled and screen == MaddenScreen.TEAM_SELECT:
            return self._rotate_team_select(controller, now)
        if self.team_rotation_enabled and screen == MaddenScreen.CONTROLLER_SELECT:
            return self._controller_side(controller, now)
        return super()._menu(controller, obs, now)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "team_rotation_enabled": self.team_rotation_enabled,
                "team_rotation_games": self.team_rotation_games,
                "team_rotation_steps": self.team_rotation_steps,
                "team_rotation_plan_index": self.team_rotation_index,
                "team_rotation_plan_length": len(self.team_rotation_plan),
                "team_rotation_guard_nudges": self.team_rotation_guard_nudges,
                "team_rotation_entry_matchup": self.team_rotation_entry_matchup,
                "team_rotation_current_matchup": self.team_rotation_current_matchup,
                "team_rotation_last_matchup": self.team_rotation_last_matchup,
                "controlled_side": self.controlled_side,
                "controlled_side_moves": self.controlled_side_moves,
            }
        )
        return state
