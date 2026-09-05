from __future__ import annotations

from collections import deque

from ps2_autopilot.guitar_hero_vision import GuitarHeroObservation, GuitarHeroVision

from .base import GameProfile, ProfileContext
from .guitar_hero_runtime import GuitarHeroRuntimeMixin
from .guitar_hero_types import GuitarHeroPhase, GuitarHeroScreen, QueuedInput, TEMPLATE_SCREEN_HINTS


class GuitarHeroV2Profile(GuitarHeroRuntimeMixin, GameProfile):
    """Streaming-oriented autonomous profile for Guitar Hero (PS2, 2005).

    The original game is unusually automation-friendly with a DualShock 2: L2/L1/
    R1/R2/Cross directly play green/red/yellow/blue/orange and no separate strum is
    required. The policy therefore drives the five note buttons from visual timing,
    while a separate conservative menu transaction layer owns boot/save/Quick Play/
    setlist/difficulty/results flow.

    Unknown moving screens are presentation, never an invitation to mash buttons.
    That preserves intro movies, venue fly-ins, loading presentation and post-song
    celebration for an unattended stream.
    """

    name = "guitar_hero"
    policy_version = 2

    def __init__(self, cfg: dict) -> None:
        self.cfg = dict(cfg)
        self.vision = GuitarHeroVision(self.cfg)
        self.template_threshold = float(self.cfg.get("template_threshold", 0.82))
        self.gameplay_threshold = float(self.cfg.get("gameplay_threshold", 0.64))
        self.save_prompt_threshold = float(self.cfg.get("save_prompt_threshold", 0.44))
        self.main_menu_threshold = float(self.cfg.get("main_menu_threshold", 0.48))
        self.setlist_threshold = float(self.cfg.get("setlist_threshold", 0.64))
        self.difficulty_threshold = float(self.cfg.get("difficulty_threshold", 0.60))
        self.title_threshold = float(self.cfg.get("title_threshold", 0.58))
        self.presentation_motion_threshold = float(
            self.cfg.get("presentation_motion_threshold", 0.018)
        )
        self.menu_stable_seconds = float(self.cfg.get("menu_stable_seconds", 0.45))
        self.menu_input_settle_seconds = float(
            self.cfg.get("menu_input_settle_seconds", 0.22)
        )
        self.menu_progress_timeout_seconds = float(
            self.cfg.get("menu_progress_timeout_seconds", 1.25)
        )
        self.menu_progress_delta = float(self.cfg.get("menu_progress_delta", 0.018))
        self.boot_prompt_seconds = float(self.cfg.get("boot_prompt_seconds", 2.0))
        self.boot_retry_seconds = float(self.cfg.get("boot_retry_seconds", 5.0))
        self.boot_max_attempts = max(1, int(self.cfg.get("boot_max_attempts", 4)))
        self.launch_wait_seconds = float(self.cfg.get("launch_wait_seconds", 35.0))
        self.post_song_settle_seconds = float(self.cfg.get("post_song_settle_seconds", 1.25))
        self.post_song_confirm_limit = max(1, int(self.cfg.get("post_song_confirm_limit", 4)))
        self.hit_threshold = float(self.cfg.get("hit_threshold", 0.075))
        self.hit_reset_threshold = float(self.cfg.get("hit_reset_threshold", 0.028))
        self.tap_hold_seconds = float(self.cfg.get("tap_hold_seconds", 0.024))
        self.sustain_grace_seconds = float(self.cfg.get("sustain_grace_seconds", 0.055))
        self.rearm_seconds = float(self.cfg.get("rearm_seconds", 0.014))
        self.whammy_enabled = bool(self.cfg.get("whammy_enabled", True))
        self.whammy_hz = max(1.0, float(self.cfg.get("whammy_hz", 7.0)))
        self.star_power_enabled = bool(self.cfg.get("star_power_enabled", False))
        self.star_power_note_interval = max(20, int(self.cfg.get("star_power_note_interval", 90)))
        self.star_power_min_seconds = float(self.cfg.get("star_power_min_seconds", 28.0))
        self.difficulty = str(self.cfg.get("difficulty", "easy")).strip().lower()
        if self.difficulty not in {"easy", "medium", "hard", "expert"}:
            raise ValueError("Guitar Hero difficulty must be easy, medium, hard, or expert")
        self.difficulty_index = {"easy": 0, "medium": 1, "hard": 2, "expert": 3}[
            self.difficulty
        ]
        self.song_advance_after_completion = max(
            0, int(self.cfg.get("song_advance_after_completion", 1))
        )

        self.phase = GuitarHeroPhase.BOOT
        self.screen = GuitarHeroScreen.UNKNOWN
        self._screen_since = 0.0
        self._last_tick_at = 0.0
        self._last_obs: GuitarHeroObservation | None = None
        self._menu_queue: deque[QueuedInput] = deque()
        self._menu_plan_tag: str | None = None
        self._last_menu_input_at = -1e9
        self._pending_signature = None
        self._pending_input_at = -1e9
        self._boot_attempts = 0
        self._last_boot_attempt_at = -1e9
        self._launch_started_at = -1e9
        self._post_song_started_at = -1e9
        self._post_song_confirms = 0
        self._first_difficulty_selection = True
        self._setlist_visits = 0
        self._song_advance_pending = 0
        self._song_active = False
        self._last_gameplay_at = -1e9
        self._presentation_started_at = -1e9

        self._armed = [True] * 5
        self._held_until = [-1e9] * 5
        self._held = [False] * 5
        self._last_hit_at = [-1e9] * 5
        self._whammy_active = False
        self._star_power_held_until = -1e9
        self._last_star_power_at = -1e9
        self._last_star_power_note_count = 0

        self.notes_attempted = 0
        self.chords_attempted = 0
        self.sustain_ticks = 0
        self.menu_inputs = 0
        self.presentation_wait_ticks = 0
        self.songs_started = 0
        self.songs_completed = 0
        self.songs_failed = 0
        self.unknown_static_ticks = 0

    @staticmethod
    def _template_screen(name: str | None) -> GuitarHeroScreen | None:
        normalized = str(name or "").strip().lower()
        if not normalized:
            return None
        for needles, screen in TEMPLATE_SCREEN_HINTS:
            if any(needle in normalized for needle in needles):
                return screen
        return None

    @staticmethod
    def _template_main_index(name: str | None) -> int | None:
        normalized = str(name or "").strip().lower()
        rows = ("career", "quick_play", "multiplayer", "tutorial", "options")
        if "main_menu" not in normalized:
            return None
        for index, row in enumerate(rows):
            if row in normalized:
                return index
        return None

    def _classify(self, ctx: ProfileContext, obs: GuitarHeroObservation) -> GuitarHeroScreen:
        template_name = ctx.template.name if ctx.template is not None else None
        if ctx.template is not None and ctx.template.score >= self.template_threshold:
            hinted = self._template_screen(template_name)
            if hinted is not None:
                return hinted

        if obs.gameplay_confidence >= self.gameplay_threshold:
            return GuitarHeroScreen.GAMEPLAY
        if obs.save_prompt_score >= self.save_prompt_threshold and self.phase == GuitarHeroPhase.BOOT:
            return GuitarHeroScreen.SAVE_PROMPT
        if obs.main_menu_score >= self.main_menu_threshold and self.phase != GuitarHeroPhase.PLAYING:
            return GuitarHeroScreen.MAIN_MENU
        if obs.setlist_score >= self.setlist_threshold and self.phase != GuitarHeroPhase.PLAYING:
            return GuitarHeroScreen.SETLIST
        if (
            obs.difficulty_score >= self.difficulty_threshold
            and self.phase in {GuitarHeroPhase.MENU, GuitarHeroPhase.BOOT}
        ):
            return GuitarHeroScreen.DIFFICULTY
        if obs.title_score >= self.title_threshold and self.phase in {
            GuitarHeroPhase.BOOT,
            GuitarHeroPhase.PRESENTATION,
        }:
            return GuitarHeroScreen.TITLE

        # After leaving gameplay, movement is the concert/cutscene/presentation and
        # is intentionally stream content. Do not let generic bootstrap skip it.
        if ctx.motion >= self.presentation_motion_threshold:
            return GuitarHeroScreen.PRESENTATION

        # During a known song-launch transaction, static loading/venue cards also
        # belong to presentation. The policy waits rather than probing Start/Confirm.
        if self.phase == GuitarHeroPhase.AWAIT_GAMEPLAY:
            return GuitarHeroScreen.PRESENTATION

        if self.phase in {GuitarHeroPhase.PLAYING, GuitarHeroPhase.POST_SONG}:
            if self._last_gameplay_at > -1e8:
                return GuitarHeroScreen.RESULTS

        return GuitarHeroScreen.UNKNOWN
