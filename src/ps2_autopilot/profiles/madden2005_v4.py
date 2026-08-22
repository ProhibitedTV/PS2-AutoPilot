from __future__ import annotations

import math

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_menu import (
    GameSituation,
    MaddenMenuNavigator,
    MaddenScreen,
    classify_madden_screen,
    parse_game_situation,
)
from ps2_autopilot.madden_ocr import MaddenOCR, OCRSnapshot
from ps2_autopilot.madden_vision import MaddenObservation, MaddenVisualState

from .base import ProfileContext
from .madden2005 import (
    Madden2005Profile as TemporalMadden2005Profile,
    MaddenPhase,
    PlayIntent,
    Possession,
    QueuedTap,
)


class Madden2005V4Profile(TemporalMadden2005Profile):
    """Madden 2005 player with OCR-guided menus and situational football policy."""

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.ocr = MaddenOCR(
            enabled=bool(cfg.get("ocr_enabled", True)),
            interval_seconds=float(cfg.get("ocr_interval_seconds", 0.70)),
            min_width=int(cfg.get("ocr_min_width", 960)),
            min_confidence=float(cfg.get("ocr_min_confidence", 0.42)),
        )
        self.menu = MaddenMenuNavigator(float(cfg.get("menu_action_seconds", 1.05)))
        self.last_ocr = OCRSnapshot((), "", False, "OCR not run")
        self.menu_assessment = classify_madden_screen(self.last_ocr)
        self.situation = GameSituation()
        self.motion_target_y = 0.0
        self.receiver_button: str | None = None
        self.receiver_controlled = False
        self.throwaway_attempted = False

    @staticmethod
    def _motion_centroid_xy(
        previous: np.ndarray | None, current: np.ndarray
    ) -> tuple[float, float]:
        if previous is None or previous.shape != current.shape:
            return 0.0, 0.0
        h, w = current.shape[:2]
        y0, y1 = int(h * 0.16), int(h * 0.93)
        x0, x1 = int(w * 0.05), int(w * 0.95)
        a = previous[y0:y1, x0:x1]
        b = current[y0:y1, x0:x1]
        a = cv2.resize(a, (240, 135), interpolation=cv2.INTER_AREA)
        b = cv2.resize(b, (240, 135), interpolation=cv2.INTER_AREA)
        ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
        gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
        diff = cv2.absdiff(ga, gb)
        _, mask = cv2.threshold(diff, 18, 255, cv2.THRESH_BINARY)
        mask = cv2.medianBlur(mask, 5)
        ys, xs = np.nonzero(mask)
        if len(xs) < 28:
            return 0.0, 0.0
        cx = float(xs.mean() / max(mask.shape[1] - 1, 1)) * 2.0 - 1.0
        cy = float(ys.mean() / max(mask.shape[0] - 1, 1)) * 2.0 - 1.0
        return self_clamp(cx), self_clamp(cy)

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)
        self.last_ocr = self.ocr.read(ctx.frame, ctx.now)
        self.menu_assessment = classify_madden_screen(self.last_ocr)
        parsed = parse_game_situation(self.last_ocr)
        if parsed.down is not None or parsed.quarter is not None or parsed.clock_seconds is not None:
            self.situation = parsed

        _, self.motion_target_y = self._motion_centroid_xy(ctx.previous_frame, ctx.frame)

        screen = self.menu_assessment.screen
        semantic_phase = {
            MaddenScreen.PLAYCALL: MaddenPhase.PLAYCALL,
            MaddenScreen.KICKING: MaddenPhase.KICKING,
            MaddenScreen.PAUSED: MaddenPhase.PAUSED,
            MaddenScreen.FINAL: MaddenPhase.GAME_OVER,
        }.get(screen)
        if semantic_phase is not None and self.menu_assessment.confidence >= 0.80:
            if semantic_phase != self.phase:
                self._transition_phase(semantic_phase, ctx.now)
        elif screen in {
            MaddenScreen.TITLE,
            MaddenScreen.MAIN_MENU,
            MaddenScreen.WRONG_MODE,
            MaddenScreen.TEAM_SELECT,
            MaddenScreen.CONTROLLER_SELECT,
            MaddenScreen.MATCHUP,
            MaddenScreen.GAME_SETTINGS,
            MaddenScreen.COIN_TOSS,
            MaddenScreen.DIALOG,
        }:
            if self.menu_assessment.confidence >= 0.78 and self.phase not in {
                MaddenPhase.LIVE,
                MaddenPhase.KICKING,
            }:
                if self.phase != MaddenPhase.MENU:
                    self._transition_phase(MaddenPhase.MENU, ctx.now)
        return obs

    def _choose_offensive_intent(self) -> PlayIntent:
        s = self.situation
        if s.down is None:
            return PlayIntent.PASS if self.rng.random() < 0.46 else PlayIntent.RUN
        if s.goal_to_go:
            return PlayIntent.RUN if (s.distance or 2) <= 3 else PlayIntent.PASS
        distance = s.distance
        if distance is None:
            return PlayIntent.PASS if self.rng.random() < 0.48 else PlayIntent.RUN
        if s.down == 4:
            return PlayIntent.RUN if distance <= 1 else PlayIntent.PASS
        if s.down == 3:
            return PlayIntent.RUN if distance <= 2 else PlayIntent.PASS
        if s.down == 2 and distance >= 7:
            return PlayIntent.PASS
        if distance <= 3:
            return PlayIntent.RUN
        if s.down == 1:
            return PlayIntent.RUN if self.rng.random() < 0.57 else PlayIntent.PASS
        return PlayIntent.PASS if self.rng.random() < 0.52 else PlayIntent.RUN

    def _arm_playcall(self, now: float) -> None:
        self.queue.clear()
        if self.possession == Possession.DEFENSE and self.possession_confidence >= 0.55:
            self.planned_play = PlayIntent.DEFENSE
        else:
            self.planned_play = self._choose_offensive_intent()

        text = self.last_ocr.text.upper()
        formation_level = any(
            token in text
            for token in ("FORMATION", "SINGLEBACK", "I FORM", "SHOTGUN", "GOAL LINE")
        ) and "ASK MADDEN" not in text

        if self.planned_play == PlayIntent.RUN:
            buttons = ["cross", "square", "circle"]
            weights = [0.42, 0.34, 0.24]
        elif self.planned_play == PlayIntent.PASS:
            buttons = ["circle", "square", "cross"]
            weights = [0.38, 0.36, 0.26]
        else:
            buttons = ["cross", "square", "circle"]
            weights = [0.40, 0.30, 0.30]
        choice = self.rng.choices(buttons, weights=weights, k=1)[0]

        if formation_level:
            self.queue.append(QueuedTap(now, "cross"))
            self.queue.append(QueuedTap(now + 0.58, choice))
            self.next_action_at = now + 1.35
            self.current_action = f"playcall: default formation -> {choice} ({self.planned_play.value})"
        else:
            self.queue.append(QueuedTap(now, choice))
            self.next_action_at = now + self.playcall_action_seconds
            self.current_action = f"playcall: {choice} ({self.planned_play.value})"
        self.playcall_armed = True

    def _transition_phase(self, new_phase: MaddenPhase, now: float) -> None:
        old = self.phase
        super()._transition_phase(new_phase, now)
        if old != new_phase and new_phase == MaddenPhase.LIVE:
            self.receiver_button = None
            self.receiver_controlled = False
            self.throwaway_attempted = False

    def _menu(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        return self.menu.act(
            controller,
            self.menu_assessment,
            now,
            transition=obs.state == MaddenVisualState.TRANSITION,
        )

    def _offense_live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        elapsed = max(0.0, now - self.play_started_at)
        correction = self._clamp(obs.field_center_x * 0.45, -0.42, 0.42)

        if self.planned_play == PlayIntent.PASS and not self.pass_thrown:
            pocket_x = math.sin(now * 0.9) * 0.08
            controller.set_left_stick(self._clamp(correction * 0.25 + pocket_x, -0.22, 0.22), 0.04)
            controller.set_right_stick(0.0, 0.0)
            if now < self.next_action_at:
                return self.current_action

            if not self.pass_icons_requested and elapsed >= 0.48:
                controller.tap("cross", 0.055)
                self.pass_icons_requested = True
                self.next_action_at = now + self.rng.uniform(0.62, 0.95)
                self.current_action = "offense: passing icons"
                return self.current_action

            if self.pass_icons_requested and elapsed >= 1.05:
                receiver = self.rng.choices(
                    ["cross", "circle", "square", "l1", "r1"],
                    weights=[0.20, 0.24, 0.22, 0.17, 0.17],
                    k=1,
                )[0]
                duration = self.rng.uniform(0.055, 0.12)
                controller.tap(receiver, duration)
                self.receiver_button = receiver
                self.pass_thrown = True
                self.pass_attempts += 1
                self.live_action_count += 1
                self.next_action_at = now + 0.28
                self.current_action = f"offense: throw {receiver}"
                return self.current_action

            if elapsed >= 2.8 and not self.throwaway_attempted:
                controller.tap("triangle", 0.05)
                self.throwaway_attempted = True
                self.next_action_at = now + 0.45
                self.current_action = "offense: throw away under pressure"
                return self.current_action

            self.next_action_at = now + 0.16
            self.current_action = "offense: scan receivers"
            return self.current_action

        if self.planned_play == PlayIntent.PASS and self.pass_thrown:
            controller.set_left_stick(correction, 1.0)
            controller.set_right_stick(0.0, 0.0)
            if now < self.next_action_at:
                return self.current_action
            if not self.receiver_controlled:
                controller.tap("cross", 0.045)
                self.receiver_controlled = True
                self.next_action_at = now + 0.18
                self.current_action = "offense: take receiver"
                return self.current_action
            if not self.catch_attempted:
                controller.tap("triangle", 0.055)
                self.catch_attempted = True
                self.next_action_at = now + 0.36
                self.current_action = "offense: catch"
                return self.current_action
            controller.tap("circle", 0.055)
            self.next_action_at = now + 0.48
            self.current_action = "offense: receiver sprint"
            return self.current_action

        wander = math.sin(now * 1.12) * self.steering_wander * 0.40
        controller.set_left_stick(self._clamp(correction + wander, -0.64, 0.64), 1.0)
        controller.set_right_stick(0.0, 0.0)
        if now < self.next_action_at:
            return self.current_action

        roll = self.rng.random()
        if roll < 0.48:
            controller.tap("cross", 0.06)
            action = "offense: sprint"
        elif roll < 0.63:
            move = self.rng.choice(["l1", "r1"])
            controller.tap(move, 0.055)
            action = f"offense: juke {move}"
        elif roll < 0.74:
            controller.tap("circle", 0.055)
            action = "offense: spin"
        elif roll < 0.86:
            arm = self.rng.choice(["l2", "r2"])
            controller.tap(arm, 0.06)
            action = f"offense: stiff-arm {arm}"
        elif roll < 0.96:
            controller.tap("triangle", 0.05)
            action = "offense: protect ball"
        else:
            controller.tap("square", 0.06)
            action = "offense: dive for marker"
        self.live_action_count += 1
        self.next_action_at = now + self.rng.uniform(0.38, 0.62)
        self.current_action = action
        return action

    def _defense_live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        target_x = self._clamp(obs.motion_center_x * 0.70 + obs.field_center_x * 0.15, -0.90, 0.90)
        target_y = self._clamp(-self.motion_target_y * 0.72 + 0.28, -0.90, 1.0)
        controller.set_left_stick(target_x, target_y)
        controller.set_right_stick(0.0, 0.0)

        if now < self.next_action_at:
            return self.current_action
        if self.live_action_count == 0:
            controller.tap("cross", 0.05)
            self.next_action_at = now + 0.24
            self.live_action_count += 1
            self.current_action = "defense: switch nearest ball"
            return self.current_action

        roll = self.rng.random()
        if roll < 0.28 + self.aggression * 0.23:
            controller.tap("square", 0.06)
            self.tackle_attempts += 1
            action = "defense: dive tackle"
        elif roll < 0.62:
            controller.tap("circle", 0.055)
            action = "defense: sprint / shed"
        elif roll < 0.74:
            controller.tap("triangle", 0.055)
            action = "defense: play ball"
        elif roll < 0.84:
            controller.tap("r2", 0.055)
            action = "defense: strip attempt"
        elif roll < 0.93:
            move = self.rng.choice(["l1", "r1"])
            controller.tap(move, 0.055)
            action = f"defense: rush move {move}"
        else:
            controller.set_right_stick(self.rng.uniform(-0.35, 0.35), -1.0)
            action = "defense: HIT STICK"
        self.live_action_count += 1
        self.next_action_at = now + self.rng.uniform(0.30, 0.52)
        self.current_action = action
        return action

    def _kicking(self, controller: Controller, now: float) -> str:
        return super()._kicking(controller, now)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "menu_screen": self.menu_assessment.screen.value,
                "menu_confidence": round(self.menu_assessment.confidence, 2),
                "menu_reason": self.menu_assessment.reason,
                "ocr_available": self.last_ocr.available,
                "ocr_confidence": round(self.last_ocr.mean_confidence, 2),
                "ocr_text": self.last_ocr.text[:220],
                "situation": self.situation.label,
                "down": self.situation.down,
                "distance": self.situation.distance,
                "quarter": self.situation.quarter,
                "clock_seconds": self.situation.clock_seconds,
                "motion_target_y": round(self.motion_target_y, 3),
                "menu_escapes": self.menu.escape_count,
            }
        )
        return state

    def recover(self, controller: Controller) -> str:
        if self.phase == MaddenPhase.MENU:
            controller.release_all()
            controller.tap("triangle", 0.08)
            self.menu.force_title = True
            self.menu.escape_count += 1
            self.current_action = "recovery: back toward title"
            return self.current_action
        return super().recover(controller)


def self_clamp(value: float) -> float:
    return max(-1.0, min(1.0, float(value)))
