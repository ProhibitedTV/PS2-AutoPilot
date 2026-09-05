from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GuitarHeroScreen(str, Enum):
    UNKNOWN = "unknown"
    TITLE = "title"
    SAVE_PROMPT = "save_prompt"
    MAIN_MENU = "main_menu"
    SETLIST = "setlist"
    DIFFICULTY = "difficulty"
    PRESENTATION = "presentation"
    GAMEPLAY = "gameplay"
    RESULTS = "results"
    FAILED = "failed"
    HIGH_SCORE = "high_score"


class GuitarHeroPhase(str, Enum):
    BOOT = "boot"
    MENU = "menu"
    AWAIT_GAMEPLAY = "await_gameplay"
    PLAYING = "playing"
    POST_SONG = "post_song"
    PRESENTATION = "presentation"


@dataclass
class QueuedInput:
    action: str
    duration: float = 0.06


TEMPLATE_SCREEN_HINTS: tuple[tuple[tuple[str, ...], GuitarHeroScreen], ...] = (
    (("gameplay", "note_highway", "song_play"), GuitarHeroScreen.GAMEPLAY),
    (("save", "memory_card"), GuitarHeroScreen.SAVE_PROMPT),
    (("main_menu",), GuitarHeroScreen.MAIN_MENU),
    (("setlist", "song_select"), GuitarHeroScreen.SETLIST),
    (("difficulty",), GuitarHeroScreen.DIFFICULTY),
    (("loading", "cutscene", "intro", "venue", "presentation"), GuitarHeroScreen.PRESENTATION),
    (("song_failed", "failed"), GuitarHeroScreen.FAILED),
    (("high_score", "initials", "name_entry"), GuitarHeroScreen.HIGH_SCORE),
    (("results", "song_complete", "score_screen"), GuitarHeroScreen.RESULTS),
    (("title", "press_start"), GuitarHeroScreen.TITLE),
)
