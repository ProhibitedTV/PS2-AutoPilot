from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Iterable

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_ocr import OCRLine, OCRSnapshot


class MaddenScreen(str, Enum):
    UNKNOWN = "unknown"
    TITLE = "title"
    MAIN_MENU = "main_menu"
    WRONG_MODE = "wrong_mode"
    DRILL_DIALOG = "drill_dialog"
    TEAM_SELECT = "team_select"
    CONTROLLER_SELECT = "controller_select"
    MATCHUP = "matchup"
    GAME_SETTINGS = "game_settings"
    COIN_TOSS = "coin_toss"
    PLAYCALL = "playcall"
    KICKING = "kicking"
    PAUSED = "paused"
    FINAL = "final"
    DIALOG = "dialog"


@dataclass(frozen=True)
class MenuAssessment:
    screen: MaddenScreen
    confidence: float
    reason: str


@dataclass(frozen=True)
class MenuHighlight:
    text: str
    y: float
    score: float
    confidence: float


@dataclass
class PendingTransition:
    source: MaddenScreen
    action: str
    expected: tuple[MaddenScreen, ...]
    started_at: float
    retries: int = 0


@dataclass(frozen=True)
class GameSituation:
    down: int | None = None
    distance: int | None = None
    goal_to_go: bool = False
    quarter: int | None = None
    clock_seconds: int | None = None
    play_clock_seconds: int | None = None

    @property
    def label(self) -> str:
        pieces: list[str] = []
        if self.down is not None:
            dist = "GOAL" if self.goal_to_go else ("?" if self.distance is None else str(self.distance))
            pieces.append(f"{self.down}&{dist}")
        if self.quarter is not None:
            pieces.append(f"Q{self.quarter}")
        if self.clock_seconds is not None:
            pieces.append(f"{self.clock_seconds // 60}:{self.clock_seconds % 60:02d}")
        if self.play_clock_seconds is not None:
            pieces.append(f"PC{self.play_clock_seconds}")
        return " ".join(pieces) or "unknown"


def _text_views(snapshot: OCRSnapshot) -> tuple[str, str, str]:
    text = snapshot.text.upper()
    alpha = text.replace("0", "O").replace("1", "I").replace("5", "S")
    header = " | ".join(line.text for line in snapshot.lines if line.y <= 0.28)
    return text, alpha, header


def _has(text: str, *phrases: str) -> bool:
    return any(phrase in text for phrase in phrases)


def _line_has(line: OCRLine, phrases: Iterable[str]) -> bool:
    text = line.text.upper().replace("0", "O").replace("1", "I").replace("5", "S")
    return any(phrase in text for phrase in phrases)


def find_ocr_line(snapshot: OCRSnapshot, *phrases: str) -> OCRLine | None:
    candidates = [line for line in snapshot.lines if _line_has(line, phrases)]
    if not candidates:
        return None
    return max(candidates, key=lambda line: line.confidence)


def detect_menu_highlight(frame: np.ndarray, snapshot: OCRSnapshot) -> MenuHighlight | None:
    """Best-effort highlighted-row detection using OCR geometry + Madden UI color."""

    if not snapshot.available or not snapshot.lines or frame.size == 0:
        return None
    h, w = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    global_mean = float(gray.mean())
    scored: list[tuple[float, OCRLine]] = []

    for line in snapshot.lines:
        if line.y < 0.18 or line.y > 0.90 or line.height <= 0:
            continue
        cy = int(line.y * h)
        band_h = max(8, int(max(line.height * h * 1.65, h * 0.027)))
        y0 = max(0, cy - band_h // 2)
        y1 = min(h, cy + band_h // 2)
        if y1 <= y0:
            continue

        x0 = max(0, int((line.x - max(0.18, line.width * 0.9)) * w))
        x1 = min(w, int((line.x + max(0.18, line.width * 0.9)) * w))
        if x1 - x0 < max(12, int(w * 0.08)):
            x0 = int(w * 0.06)
            x1 = int(w * 0.94)

        band = hsv[y0:y1, x0:x1]
        lum = gray[y0:y1, x0:x1]
        if band.size == 0:
            continue
        hue, sat, val = cv2.split(band)
        sat_mean = float(sat.mean()) / 255.0
        lum_contrast = min(1.0, abs(float(lum.mean()) - global_mean) / 80.0)
        colorful = ((sat > 72) & (val > 55))
        red_or_orange = colorful & ((hue < 32) | (hue > 166))
        blue = colorful & (hue >= 88) & (hue <= 142)
        color_ratio = float(np.maximum(red_or_orange, blue).mean())
        edge = cv2.Laplacian(lum, cv2.CV_32F)
        edge_energy = min(1.0, float(np.mean(np.abs(edge))) / 32.0)
        score = sat_mean * 0.36 + color_ratio * 0.34 + lum_contrast * 0.18 + edge_energy * 0.12
        scored.append((score, line))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_line = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    separation = max(0.0, best_score - second)
    confidence = max(0.0, min(1.0, best_score * 0.72 + separation * 2.4))
    if best_score < 0.18 or confidence < 0.24:
        return None
    return MenuHighlight(best_line.text, best_line.y, best_score, confidence)


def classify_madden_screen(snapshot: OCRSnapshot) -> MenuAssessment:
    if not snapshot.available or not snapshot.lines:
        return MenuAssessment(MaddenScreen.UNKNOWN, 0.0, snapshot.error or "no OCR text")

    text, alpha, header = _text_views(snapshot)

    wrong_header = (
        "FRANCHISE SETUP",
        "SELECT DRILL",
        "TRAINING CAMP",
        "MINI CAMP",
        "MINICAMP",
        "SUPERSTAR",
        "MADDEN 101",
        "CREATE A PLAYER",
        "CREATE-A-PLAYER",
        "ROSTER MANAGEMENT",
    )
    if _has(text, "POCKET PRESENCE", "START DRILL") and "CANCEL" in text:
        return MenuAssessment(MaddenScreen.DRILL_DIALOG, 0.99, "drill confirmation / cancel selected")

    if _has(header, *wrong_header):
        return MenuAssessment(MaddenScreen.WRONG_MODE, 0.99, "non-Play-Now branch")

    if _has(alpha, "PRESS START", "PRESS THE START", "PRESS ANY BUTTON"):
        return MenuAssessment(MaddenScreen.TITLE, 0.99, "title prompt")

    if "DEMO" in alpha and _has(alpha, "MADDEN", "MADDENNFL"):
        return MenuAssessment(MaddenScreen.TITLE, 0.96, "Madden attract/demo screen")

    if _has(header, "CONTROLLER SELECT", "SELECT CONTROLLER", "CHOOSE SIDE", "SELECT SIDE"):
        return MenuAssessment(MaddenScreen.CONTROLLER_SELECT, 0.97, "controller/side assignment")

    if _has(header, "TEAM SELECT", "SELECT TEAM", "TEAM SELECTION", "FAVORITE TEAM", "SELECT FAVORITE"):
        return MenuAssessment(MaddenScreen.TEAM_SELECT, 0.96, "team selection")

    if _has(header, "TEAM MATCHUP", "MATCHUP"):
        return MenuAssessment(MaddenScreen.MATCHUP, 0.92, "matchup")

    if _has(header, "GAME SETTINGS", "GAME OPTIONS", "STADIUM SELECT", "SELECT STADIUM"):
        return MenuAssessment(MaddenScreen.GAME_SETTINGS, 0.92, "pregame settings")

    if _has(text, "COIN TOSS") or (
        _has(text, "KICK", "RECEIVE") and _has(text, "CHOOSE", "SELECT")
    ):
        return MenuAssessment(MaddenScreen.COIN_TOSS, 0.90, "coin toss choice")

    if _has(text, "FINAL SCORE", "GAME OVER") or _has(header, "FINAL"):
        return MenuAssessment(MaddenScreen.FINAL, 0.96, "final/game over")

    if _has(header, "PAUSE", "PAUSED") or (
        "RESUME" in text and _has(text, "INSTANT REPLAY", "QUIT GAME", "SETTINGS")
    ):
        return MenuAssessment(MaddenScreen.PAUSED, 0.95, "pause menu")

    if ("POWER" in text and "ACCURACY" in text) or _has(text, "KICK METER"):
        return MenuAssessment(MaddenScreen.KICKING, 0.90, "kick meter")

    playcall_markers = (
        "ASK MADDEN",
        "SELECT A PLAY",
        "CHOOSE A PLAY",
        "PLAY CALL",
        "PLAYCALL",
    )
    if _has(text, *playcall_markers):
        return MenuAssessment(MaddenScreen.PLAYCALL, 0.94, "play-call text")
    if "FORMATION" in text and _has(text, "I FORM", "SHOTGUN", "SINGLEBACK", "GOAL LINE", "DEFENSE"):
        return MenuAssessment(MaddenScreen.PLAYCALL, 0.84, "formation text")

    if _has(alpha, "PLAY NOW"):
        return MenuAssessment(MaddenScreen.MAIN_MENU, 0.96, "Play Now visible")

    if _has(text, "ARE YOU SURE", "YES", "NO") and len(snapshot.lines) <= 14:
        return MenuAssessment(MaddenScreen.DIALOG, 0.72, "confirmation dialog")

    return MenuAssessment(
        MaddenScreen.UNKNOWN,
        max(0.15, min(0.65, snapshot.mean_confidence * 0.65)),
        "unclassified OCR",
    )


_DOWN_RE = re.compile(
    r"\b([1-4])(?:ST|ND|RD|TH)?\s*(?:&|AND)\s*(GOAL|\d{1,2})\b",
    re.IGNORECASE,
)
_QTR_RE = re.compile(r"\bQ(?:TR|UARTER)?\s*([1-4])\b", re.IGNORECASE)
_ORDINAL_QTR_RE = re.compile(r"\b([1-4])(?:ST|ND|RD|TH)\s+(?:QTR|QUARTER)\b", re.IGNORECASE)
_BARE_ORDINAL_QTR_RE = re.compile(r"^([1-4])(?:ST|ND|RD|TH)$", re.IGNORECASE)
_CLOCK_RE = re.compile(r"\b([0-9]{1,2}):([0-5][0-9])\b")
_PLAY_CLOCK_RE = re.compile(r"(?<!\d):([0-3]?\d|40)\b")


def parse_game_situation(snapshot: OCRSnapshot) -> GameSituation:
    if not snapshot.available:
        return GameSituation()

    text = snapshot.text.upper().replace("|", " ")
    down = distance = quarter = clock_seconds = play_clock_seconds = None
    goal_to_go = False

    match = _DOWN_RE.search(text)
    if match:
        down = int(match.group(1))
        if match.group(2).upper() == "GOAL":
            goal_to_go = True
        else:
            distance = int(match.group(2))

    qmatch = _QTR_RE.search(text) or _ORDINAL_QTR_RE.search(text)
    if qmatch:
        quarter = int(qmatch.group(1))
    else:
        # Madden often shows a standalone `2ND` score-bug line. Require the
        # entire OCR line to be the ordinal so `1ST AND 10` cannot become Q1.
        for line in snapshot.lines:
            if line.y > 0.25:
                continue
            bare = _BARE_ORDINAL_QTR_RE.fullmatch(line.text.strip())
            if bare:
                quarter = int(bare.group(1))
                break

    for cmatch in _CLOCK_RE.finditer(text):
        minutes = int(cmatch.group(1))
        seconds = int(cmatch.group(2))
        if minutes <= 15:
            clock_seconds = minutes * 60 + seconds
            break

    play_matches = [int(match.group(1)) for match in _PLAY_CLOCK_RE.finditer(text)]
    if play_matches:
        play_clock_seconds = min(play_matches)

    return GameSituation(
        down,
        distance,
        goal_to_go,
        quarter,
        clock_seconds,
        play_clock_seconds,
    )


class MaddenMenuNavigator:
    """Verified, conservative route from boot into Play Now.

    Confirm-like actions create a transaction with an expected destination. A
    button is not considered successful until vision observes that destination.
    Timeouts retry the action, then fall back toward a known-safe parent screen.
    """

    def __init__(self, action_seconds: float = 1.05) -> None:
        self.action_seconds = max(0.55, float(action_seconds))
        self.next_action_at = 0.0
        self.last_screen = MaddenScreen.UNKNOWN
        self.screen_since = 0.0
        self.force_title = False
        self.after_title_start = False
        self.controller_side_moved = False
        self.escape_count = 0
        self.current_action = "menu: observing"
        self.pending: PendingTransition | None = None
        self.verified_transitions = 0
        self.transaction_retries = 0
        self.transaction_failures = 0
        self.highlight: MenuHighlight | None = None

    def _seen(self, screen: MaddenScreen, now: float) -> None:
        if screen != self.last_screen:
            self.last_screen = screen
            self.screen_since = now
            if screen != MaddenScreen.CONTROLLER_SELECT:
                self.controller_side_moved = False

    def _begin(
        self,
        screen: MaddenScreen,
        action: str,
        expected: tuple[MaddenScreen, ...],
        now: float,
    ) -> None:
        self.pending = PendingTransition(screen, action, expected, now)

    def _clear_pending(self) -> None:
        self.pending = None

    def _pending_step(self, controller: Controller, screen: MaddenScreen, now: float) -> str | None:
        pending = self.pending
        if pending is None:
            return None
        if screen in pending.expected:
            self.verified_transitions += 1
            self._clear_pending()
            return None

        if screen not in {pending.source, MaddenScreen.UNKNOWN}:
            self.transaction_failures += 1
            self._clear_pending()
            if screen in {MaddenScreen.WRONG_MODE, MaddenScreen.DRILL_DIALOG}:
                self.force_title = True
            return None

        timeout = 3.2 + pending.retries * 1.2
        if now - pending.started_at < timeout or now < self.next_action_at:
            return self.current_action

        if pending.retries < 2:
            controller.tap(pending.action, 0.08)
            pending.retries += 1
            pending.started_at = now
            self.transaction_retries += 1
            self.next_action_at = now + 1.0
            self.current_action = f"menu: retry {pending.action} ({pending.retries}/2)"
            return self.current_action

        self.transaction_failures += 1
        self._clear_pending()
        self.force_title = True
        controller.tap("triangle", 0.07)
        self.next_action_at = now + self.action_seconds
        self.current_action = "menu: transition failed -> safe backout"
        return self.current_action

    def _tap(
        self,
        controller: Controller,
        action: str,
        now: float,
        screen: MaddenScreen,
        expected: tuple[MaddenScreen, ...] = (),
        delay: float | None = None,
    ) -> str:
        controller.tap(action, 0.08)
        self.next_action_at = now + (self.action_seconds if delay is None else delay)
        self.current_action = f"menu: {action}"
        if expected:
            self._begin(screen, action, expected, now)
        return self.current_action

    def _main_menu(
        self,
        controller: Controller,
        assessment: MenuAssessment,
        now: float,
        snapshot: OCRSnapshot | None,
        highlight: MenuHighlight | None,
    ) -> str:
        if snapshot is not None and highlight is not None:
            play_now = find_ocr_line(snapshot, "PLAY NOW")
            if play_now is not None and "PLAY NOW" not in highlight.text.upper():
                direction = "down" if play_now.y > highlight.y else "up"
                self._tap(controller, direction, now, assessment.screen, delay=0.45)
                self.current_action = f"menu: move {direction} toward PLAY NOW"
                return self.current_action
        return self._tap(
            controller,
            "cross",
            now,
            assessment.screen,
            (MaddenScreen.TEAM_SELECT, MaddenScreen.CONTROLLER_SELECT, MaddenScreen.MATCHUP),
        )

    def act(
        self,
        controller: Controller,
        assessment: MenuAssessment,
        now: float,
        transition: bool = False,
        snapshot: OCRSnapshot | None = None,
        highlight: MenuHighlight | None = None,
    ) -> str:
        screen = assessment.screen
        self._seen(screen, now)
        self.highlight = highlight
        controller.neutral_sticks()

        pending = self._pending_step(controller, screen, now)
        if pending is not None:
            return pending
        if now < self.next_action_at:
            return self.current_action

        if screen == MaddenScreen.TITLE:
            self.force_title = False
            self.after_title_start = True
            return self._tap(
                controller,
                "start",
                now,
                screen,
                (MaddenScreen.MAIN_MENU,),
                delay=1.0,
            )

        if screen == MaddenScreen.MAIN_MENU:
            return self._main_menu(controller, assessment, now, snapshot, highlight)

        if screen == MaddenScreen.WRONG_MODE:
            self.force_title = True
            return self._tap(controller, "triangle", now, screen, delay=0.75)

        if screen == MaddenScreen.DRILL_DIALOG:
            self.force_title = True
            self._tap(controller, "cross", now, screen, delay=0.85)
            self.current_action = "menu: cancel drill dialog -> cross"
            return self.current_action

        if screen == MaddenScreen.TEAM_SELECT:
            return self._tap(
                controller,
                "cross",
                now,
                screen,
                (MaddenScreen.CONTROLLER_SELECT, MaddenScreen.MATCHUP, MaddenScreen.GAME_SETTINGS),
                delay=1.2,
            )

        if screen == MaddenScreen.CONTROLLER_SELECT:
            if not self.controller_side_moved:
                self.controller_side_moved = True
                return self._tap(controller, "right", now, screen, delay=0.50)
            return self._tap(
                controller,
                "cross",
                now,
                screen,
                (MaddenScreen.MATCHUP, MaddenScreen.GAME_SETTINGS, MaddenScreen.COIN_TOSS, MaddenScreen.PLAYCALL),
                delay=1.4,
            )

        if screen in {MaddenScreen.MATCHUP, MaddenScreen.GAME_SETTINGS}:
            return self._tap(
                controller,
                "cross",
                now,
                screen,
                (MaddenScreen.GAME_SETTINGS, MaddenScreen.COIN_TOSS, MaddenScreen.PLAYCALL),
                delay=1.5,
            )

        if screen == MaddenScreen.COIN_TOSS:
            return self._tap(
                controller,
                "cross",
                now,
                screen,
                (MaddenScreen.PLAYCALL,),
                delay=1.2,
            )

        if screen == MaddenScreen.PAUSED:
            return self._tap(controller, "start", now, screen, delay=1.0)

        if screen == MaddenScreen.FINAL:
            return self._tap(controller, "cross", now, screen, delay=1.4)

        if screen == MaddenScreen.DIALOG:
            return self._tap(controller, "cross", now, screen, delay=1.0)

        if transition:
            self.current_action = "menu: transition/loading; hold inputs"
            self.next_action_at = now + 1.0
            return self.current_action

        if now - self.screen_since < 2.2:
            self.current_action = "menu: observing"
            self.next_action_at = now + 0.65
            return self.current_action

        self.escape_count += 1
        return self._tap(controller, "triangle", now, screen, delay=1.0)

    def request_recovery(self, controller: Controller, level: int, now: float) -> str:
        controller.neutral_sticks()
        if level <= 1:
            controller.tap("triangle", 0.07)
            self.current_action = "menu: recovery L1 -> back"
        elif level == 2:
            controller.tap("start", 0.08)
            self.current_action = "menu: recovery L2 -> start probe"
        else:
            controller.tap("triangle", 0.07)
            self.force_title = True
            self.current_action = "menu: recovery L3 -> force title route"
        self.next_action_at = now + 1.25
        return self.current_action

    def telemetry(self) -> dict:
        pending = self.pending
        return {
            "menu_highlight": None if self.highlight is None else self.highlight.text,
            "menu_highlight_confidence": None if self.highlight is None else round(self.highlight.confidence, 2),
            "menu_pending": None if pending is None else pending.action,
            "menu_expected": [] if pending is None else [screen.value for screen in pending.expected],
            "menu_transaction_retries": self.transaction_retries,
            "menu_transaction_failures": self.transaction_failures,
            "menu_verified_transitions": self.verified_transitions,
            "menu_escape_count": self.escape_count,
        }
