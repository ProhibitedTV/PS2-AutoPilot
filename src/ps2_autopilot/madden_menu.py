from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_ocr import OCRSnapshot


class MaddenScreen(str, Enum):
    UNKNOWN = "unknown"
    TITLE = "title"
    MAIN_MENU = "main_menu"
    WRONG_MODE = "wrong_mode"
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
class GameSituation:
    down: int | None = None
    distance: int | None = None
    goal_to_go: bool = False
    quarter: int | None = None
    clock_seconds: int | None = None

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
        return " ".join(pieces) or "unknown"


def _text_views(snapshot: OCRSnapshot) -> tuple[str, str, str]:
    text = snapshot.text.upper()
    alpha = text.replace("0", "O").replace("1", "I").replace("5", "S")
    header = " | ".join(line.text for line in snapshot.lines if line.y <= 0.28)
    return text, alpha, header


def _has(text: str, *phrases: str) -> bool:
    return any(phrase in text for phrase in phrases)


def classify_madden_screen(snapshot: OCRSnapshot) -> MenuAssessment:
    if not snapshot.available or not snapshot.lines:
        return MenuAssessment(MaddenScreen.UNKNOWN, 0.0, snapshot.error or "no OCR text")

    text, alpha, header = _text_views(snapshot)

    # High-specificity screens first. The first live run reached both
    # FRANCHISE SETUP and SELECT DRILL/PLAYER -> POCKET PRESENCE; these are
    # intentionally treated as escape states, never as something to confirm.
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
    if _has(header, *wrong_header) or (
        _has(text, "POCKET PRESENCE", "START DRILL") and "CANCEL" in text
    ):
        return MenuAssessment(MaddenScreen.WRONG_MODE, 0.99, "non-Play-Now branch")

    if _has(alpha, "PRESS START", "PRESS THE START"):
        return MenuAssessment(MaddenScreen.TITLE, 0.99, "title prompt")

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

    # Main menu is keyed to PLAY NOW. This lets the navigator deliberately
    # choose exhibition instead of randomly wandering into Franchise.
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
_CLOCK_RE = re.compile(r"\b([0-9]{1,2}):([0-5][0-9])\b")


def parse_game_situation(snapshot: OCRSnapshot) -> GameSituation:
    if not snapshot.available:
        return GameSituation()

    text = snapshot.text.upper().replace("|", " ")
    down = distance = quarter = clock_seconds = None
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

    # Madden screens can contain unrelated timestamps; scoreboard clocks are
    # overwhelmingly in the 0:00..15:00 range, so keep the first plausible one.
    for cmatch in _CLOCK_RE.finditer(text):
        minutes = int(cmatch.group(1))
        seconds = int(cmatch.group(2))
        if minutes <= 15:
            clock_seconds = minutes * 60 + seconds
            break

    return GameSituation(down, distance, goal_to_go, quarter, clock_seconds)


class MaddenMenuNavigator:
    """Conservative deterministic route from boot to Play Now.

    Random menu confirms caused the first live prototype to create a Franchise
    and enter Pocket Presence drills. This navigator prefers backing all the way
    to the title screen whenever it detects a wrong mode, then uses the default
    main-menu selection to enter Play Now.
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

    def _seen(self, screen: MaddenScreen, now: float) -> None:
        if screen != self.last_screen:
            self.last_screen = screen
            self.screen_since = now
            if screen != MaddenScreen.CONTROLLER_SELECT:
                self.controller_side_moved = False

    def act(
        self,
        controller: Controller,
        assessment: MenuAssessment,
        now: float,
        transition: bool = False,
    ) -> str:
        controller.neutral_sticks()
        screen = assessment.screen
        self._seen(screen, now)

        if now < self.next_action_at:
            return self.current_action

        if screen == MaddenScreen.WRONG_MODE:
            controller.tap("triangle", 0.07)
            self.force_title = True
            self.after_title_start = False
            self.escape_count += 1
            self.next_action_at = now + 0.85
            self.current_action = "menu: ESCAPE wrong mode"
            return self.current_action

        if screen == MaddenScreen.TITLE:
            controller.tap("start", 0.08)
            self.force_title = False
            self.after_title_start = True
            self.next_action_at = now + 1.35
            self.current_action = "menu: START -> Play Now"
            return self.current_action

        if screen == MaddenScreen.MAIN_MENU:
            if self.force_title:
                controller.tap("triangle", 0.07)
                self.next_action_at = now + 0.90
                self.current_action = "menu: reset main menu to title"
            else:
                controller.tap("cross", 0.08)
                self.after_title_start = False
                self.next_action_at = now + 1.25
                self.current_action = "menu: select PLAY NOW"
            return self.current_action

        if screen == MaddenScreen.TEAM_SELECT:
            controller.tap("cross", 0.08)
            self.next_action_at = now + 1.30
            self.current_action = "menu: accept teams"
            return self.current_action

        if screen == MaddenScreen.CONTROLLER_SELECT:
            if not self.controller_side_moved:
                controller.tap("left", 0.08)
                self.controller_side_moved = True
                self.next_action_at = now + 0.45
                self.current_action = "menu: assign controller"
            else:
                controller.tap("cross", 0.08)
                self.next_action_at = now + 1.20
                self.current_action = "menu: confirm controller"
            return self.current_action

        if screen in {MaddenScreen.MATCHUP, MaddenScreen.GAME_SETTINGS, MaddenScreen.COIN_TOSS}:
            controller.tap("cross", 0.08)
            self.next_action_at = now + 1.15
            self.current_action = f"menu: advance {screen.value}"
            return self.current_action

        if screen == MaddenScreen.DIALOG:
            controller.tap("cross", 0.07)
            self.next_action_at = now + 1.0
            self.current_action = "menu: confirm dialog"
            return self.current_action

        if transition:
            controller.tap("cross", 0.06)
            self.next_action_at = now + 1.0
            self.current_action = "menu: skip transition"
            return self.current_action

        # OCR sometimes misses the animated main-menu text at 400x203. After a
        # recognized title START, one conservative X press targets the default
        # Play Now selection. Otherwise unknown menus back out instead of diving.
        age = now - self.screen_since
        if self.after_title_start and age >= 0.90:
            controller.tap("cross", 0.07)
            self.after_title_start = False
            self.next_action_at = now + 1.30
            self.current_action = "menu: Play Now fallback"
            return self.current_action

        if self.force_title and age >= 0.70:
            controller.tap("triangle", 0.07)
            self.escape_count += 1
            self.next_action_at = now + 0.90
            self.current_action = "menu: backing to title"
            return self.current_action

        if age >= 5.0:
            controller.tap("triangle", 0.07)
            self.force_title = True
            self.escape_count += 1
            self.next_action_at = now + 1.0
            self.current_action = "menu: unknown -> safe backout"
            return self.current_action

        self.current_action = "menu: observing"
        self.next_action_at = now + 0.45
        return self.current_action
