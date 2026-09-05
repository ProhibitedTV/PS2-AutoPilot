from __future__ import annotations

from typing import Any

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.guitar_hero_vision import LANE_ACTIONS, LANE_NAMES, GuitarHeroObservation, signature_delta

from .base import ProfileContext
from .guitar_hero_types import GuitarHeroPhase, GuitarHeroScreen, QueuedInput


class GuitarHeroRuntimeMixin:
    def _update_screen(self, screen: GuitarHeroScreen, now: float) -> None:
        if screen != self.screen:
            self.screen = screen
            self._screen_since = now
            self._menu_plan_tag = None
            self._menu_queue.clear()
            self._pending_signature = None
        self._last_tick_at = now

    def _screen_stable(self, now: float) -> bool:
        return now - self._screen_since >= self.menu_stable_seconds

    def _input_progress_ready(self, obs: GuitarHeroObservation, now: float) -> bool:
        if self._pending_signature is None:
            return now - self._last_menu_input_at >= self.menu_input_settle_seconds
        delta = signature_delta(self._pending_signature, obs.frame_signature)
        if delta >= self.menu_progress_delta:
            self._pending_signature = None
            return now - self._last_menu_input_at >= self.menu_input_settle_seconds
        if now - self._pending_input_at >= self.menu_progress_timeout_seconds:
            self._pending_signature = None
            return True
        return False

    def _queue_plan(self, tag: str, actions: list[str]) -> None:
        if self._menu_plan_tag == tag:
            return
        self._menu_plan_tag = tag
        self._menu_queue.clear()
        self._menu_queue.extend(QueuedInput(action) for action in actions)

    def _drive_menu_queue(
        self,
        controller: Controller,
        obs: GuitarHeroObservation,
        now: float,
    ) -> str | None:
        if not self._menu_queue:
            return None
        if not self._input_progress_ready(obs, now):
            return f"menu transaction settling ({self._menu_plan_tag})"
        queued = self._menu_queue.popleft()
        controller.release_all()
        controller.neutral_sticks()
        controller.tap(queued.action, queued.duration)
        self.menu_inputs += 1
        self._last_menu_input_at = now
        self._pending_input_at = now
        self._pending_signature = obs.frame_signature.copy()
        return f"menu {self._menu_plan_tag}: {queued.action}"

    def _release_gameplay_inputs(self, controller: Controller) -> None:
        if any(self._held):
            controller.release_all()
        else:
            # Explicitly release Star Power if it was held for one non-blocking tick.
            if self._star_power_held_until > -1e8:
                try:
                    controller.release("select")
                except KeyError:
                    pass
        self._held = [False] * 5
        self._held_until = [-1e9] * 5
        self._star_power_held_until = -1e9
        if self._whammy_active:
            controller.neutral_sticks()
            self._whammy_active = False

    def _play_tick(
        self,
        controller: Controller,
        obs: GuitarHeroObservation,
        now: float,
    ) -> str:
        if not self._song_active:
            self._song_active = True
            self.songs_started += 1
            self._post_song_confirms = 0
            self._song_advance_pending = 0
        self.phase = GuitarHeroPhase.PLAYING
        self.route_stage = "gameplay"
        self._last_gameplay_at = now
        self._launch_started_at = -1e9
        self._menu_queue.clear()
        self._menu_plan_tag = None

        if self._star_power_held_until > -1e8 and now >= self._star_power_held_until:
            controller.release("select")
            self._star_power_held_until = -1e9

        for index in range(5):
            strength = obs.hit_strengths[index]
            if strength <= self.hit_reset_threshold and now - self._last_hit_at[index] >= self.rearm_seconds:
                self._armed[index] = True

            if self._held[index] and obs.sustains[index]:
                self._held_until[index] = max(
                    self._held_until[index], now + self.sustain_grace_seconds
                )
                self.sustain_ticks += 1
            elif self._held[index] and now >= self._held_until[index]:
                controller.release(LANE_ACTIONS[index])
                self._held[index] = False

        candidates = [
            index
            for index, strength in enumerate(obs.hit_strengths)
            if self._armed[index] and strength >= self.hit_threshold
        ]
        if candidates:
            if len(candidates) > 1:
                self.chords_attempted += 1
            for index in candidates:
                if self._held[index]:
                    controller.release(LANE_ACTIONS[index])
                    self._held[index] = False
                controller.hold(LANE_ACTIONS[index])
                self._held[index] = True
                self._held_until[index] = now + self.tap_hold_seconds
                if obs.sustains[index]:
                    self._held_until[index] = now + self.sustain_grace_seconds
                self._armed[index] = False
                self._last_hit_at[index] = now
                self.notes_attempted += 1

        sustaining = any(self._held[index] and obs.sustains[index] for index in range(5))
        if self.whammy_enabled and sustaining:
            phase = int(now * self.whammy_hz * 2.0) % 2
            controller.set_left_stick(0.0, 0.85 if phase else -0.85)
            self._whammy_active = True
        elif self._whammy_active:
            controller.neutral_sticks()
            self._whammy_active = False

        if (
            self.star_power_enabled
            and self.notes_attempted - self._last_star_power_note_count >= self.star_power_note_interval
            and now - self._last_star_power_at >= self.star_power_min_seconds
            and self._star_power_held_until < -1e8
        ):
            controller.hold("select")
            self._star_power_held_until = now + 0.035
            self._last_star_power_at = now
            self._last_star_power_note_count = self.notes_attempted

        if candidates:
            names = "+".join(LANE_NAMES[index] for index in candidates)
            return f"play {names}"
        if sustaining:
            return "hold sustain + whammy" if self.whammy_enabled else "hold sustain"
        return "track note highway"

    def _main_menu_plan(self, selected_index: int | None) -> list[str]:
        # Quick Play is row 1. If selected-row evidence is unavailable, fresh GH1
        # boots select Career (row 0), so a single Down is the least invasive fallback.
        if selected_index is None:
            return ["down", "confirm"]
        delta = 1 - selected_index
        if delta > 0:
            return ["down"] * delta + ["confirm"]
        if delta < 0:
            return ["up"] * abs(delta) + ["confirm"]
        return ["confirm"]

    def _tick_non_gameplay(
        self,
        controller: Controller,
        ctx: ProfileContext,
        obs: GuitarHeroObservation,
    ) -> str:
        now = ctx.now

        if self.screen == GuitarHeroScreen.PRESENTATION:
            self._release_gameplay_inputs(controller)
            self.presentation_wait_ticks += 1
            if self._presentation_started_at < -1e8:
                self._presentation_started_at = now
            if self.phase == GuitarHeroPhase.PLAYING:
                self.phase = GuitarHeroPhase.POST_SONG
                self._post_song_started_at = now
            elif self.phase not in {GuitarHeroPhase.AWAIT_GAMEPLAY, GuitarHeroPhase.POST_SONG}:
                self.phase = GuitarHeroPhase.PRESENTATION
            return "wait for cutscene/loading/presentation"

        self._presentation_started_at = -1e9

        if self.screen == GuitarHeroScreen.SAVE_PROMPT:
            self.phase = GuitarHeroPhase.BOOT
            if not self._screen_stable(now):
                return "save prompt settling"
            self._queue_plan("save_yes", ["confirm"])
            action = self._drive_menu_queue(controller, obs, now)
            return action or "save prompt acknowledged"

        if self.screen == GuitarHeroScreen.TITLE:
            self.phase = GuitarHeroPhase.BOOT
            if not self._screen_stable(now):
                return "title settling"
            self._queue_plan("title_start", ["start"])
            action = self._drive_menu_queue(controller, obs, now)
            return action or "title start sent"

        if self.screen == GuitarHeroScreen.MAIN_MENU:
            self.phase = GuitarHeroPhase.MENU
            self.route_stage = "main"
            if not self._screen_stable(now):
                return "main menu settling"
            template_index = self._template_main_index(
                ctx.template.name if ctx.template is not None else None
            )
            selected = template_index if template_index is not None else obs.selected_main_index
            self._queue_plan("main_to_quick_play", self._main_menu_plan(selected))
            action = self._drive_menu_queue(controller, obs, now)
            if action is not None and not self._menu_queue and action.endswith("confirm"):
                self.route_stage = "setlist"
            return action or "Quick Play selected"

        if self.screen == GuitarHeroScreen.SETLIST:
            self.phase = GuitarHeroPhase.MENU
            self.route_stage = "setlist"
            if not self._screen_stable(now):
                return "setlist settling"
            if self._menu_plan_tag != "setlist_launch":
                advances = self._song_advance_pending
                self._queue_plan("setlist_launch", ["down"] * advances + ["confirm"])
                self._song_advance_pending = 0
                self._setlist_visits += 1
            action = self._drive_menu_queue(controller, obs, now)
            if action is not None:
                if not self._menu_queue and action.endswith("confirm"):
                    self.route_stage = "difficulty"
                return action
            return "setlist song selected"

        if self.screen == GuitarHeroScreen.DIFFICULTY:
            self.phase = GuitarHeroPhase.MENU
            self.route_stage = "difficulty"
            if not self._screen_stable(now):
                return "difficulty menu settling"
            selected = obs.selected_difficulty_index
            if selected is not None:
                delta = self.difficulty_index - selected
                moves = (["down"] * delta) if delta > 0 else (["up"] * abs(delta))
            elif self._first_difficulty_selection:
                # Fresh GH1 Quick Play opens at Easy; this is only a fallback when
                # row geometry is unavailable. Later screens reuse the chosen value.
                moves = ["down"] * self.difficulty_index
            else:
                moves = []
            self._queue_plan("difficulty_launch", moves + ["confirm"])
            action = self._drive_menu_queue(controller, obs, now)
            if action is not None:
                if not self._menu_queue and action.endswith("confirm"):
                    self._first_difficulty_selection = False
                    self.phase = GuitarHeroPhase.AWAIT_GAMEPLAY
                    self.route_stage = "song"
                    self._launch_started_at = now
                return action
            return f"difficulty {self.difficulty} selected"

        if self.screen == GuitarHeroScreen.FAILED:
            self.phase = GuitarHeroPhase.POST_SONG
            self.route_stage = "post_song"
            if self._song_active:
                self.songs_failed += 1
                self._song_active = False
            if not self._screen_stable(now):
                return "song failed screen settling"
            # Retry is the safe streaming default; explicit templates can later
            # distinguish alternate rows without changing this conservative path.
            self._queue_plan("retry_failed_song", ["confirm"])
            action = self._drive_menu_queue(controller, obs, now)
            return action or "retry selected"

        if self.screen in {GuitarHeroScreen.RESULTS, GuitarHeroScreen.HIGH_SCORE}:
            self.route_stage = "post_song"
            if self.phase == GuitarHeroPhase.PLAYING:
                self.phase = GuitarHeroPhase.POST_SONG
                self._post_song_started_at = now
            if self.phase != GuitarHeroPhase.POST_SONG:
                self.phase = GuitarHeroPhase.POST_SONG
            if now - max(self._post_song_started_at, self._screen_since) < self.post_song_settle_seconds:
                self._release_gameplay_inputs(controller)
                return "post-song results settling"
            if self._song_active:
                self.songs_completed += 1
                self._song_active = False
                self._song_advance_pending = self.song_advance_after_completion
            if self._post_song_confirms >= self.post_song_confirm_limit:
                self._release_gameplay_inputs(controller)
                return "post-song confirm budget exhausted; wait for evidence"
            tag = f"post_song_{self._post_song_confirms}"
            self._queue_plan(tag, ["confirm"])
            action = self._drive_menu_queue(controller, obs, now)
            if action is not None and action.endswith("confirm"):
                self._post_song_confirms += 1
            return action or "post-song continue"

        # Unknown static screen. During boot a bounded Start probe is appropriate;
        # after menu/song ownership exists we fail closed instead of corrupting state.
        self._release_gameplay_inputs(controller)
        if self.phase in {GuitarHeroPhase.BOOT, GuitarHeroPhase.PRESENTATION}:
            stable_for = now - self._screen_since
            since_attempt = now - self._last_boot_attempt_at
            if (
                stable_for >= self.boot_prompt_seconds
                and since_attempt >= self.boot_retry_seconds
                and self._boot_attempts < self.boot_max_attempts
            ):
                controller.tap("start", 0.06)
                self.menu_inputs += 1
                self._boot_attempts += 1
                self._last_boot_attempt_at = now
                self._pending_signature = obs.frame_signature.copy()
                self._pending_input_at = now
                self._last_menu_input_at = now
                return f"bounded boot Start probe {self._boot_attempts}/{self.boot_max_attempts}"
            return "wait for title/menu evidence"

        if self.phase == GuitarHeroPhase.AWAIT_GAMEPLAY:
            if now - self._launch_started_at <= self.launch_wait_seconds:
                return "wait for song load/venue presentation"
            return "song launch timed out; fail closed for evidence"

        self.unknown_static_ticks += 1
        return "unknown static screen; fail closed"

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        obs = self.vision.analyze(ctx.frame)
        self._last_obs = obs
        screen = self._classify(ctx, obs)

        if self.screen == GuitarHeroScreen.GAMEPLAY and screen != GuitarHeroScreen.GAMEPLAY:
            self.phase = GuitarHeroPhase.POST_SONG
            self._post_song_started_at = ctx.now
        self._update_screen(screen, ctx.now)

        if screen == GuitarHeroScreen.GAMEPLAY:
            self._boot_attempts = 0
            return self._play_tick(controller, obs, ctx.now)
        return self._tick_non_gameplay(controller, ctx, obs)

    def recover(self, controller: Controller) -> str:
        # Recovery must respect presentation ownership. The global motion watchdog can
        # see a static loading card or held cinematic frame; never skip it or reload a
        # savestate just because pixels stopped changing.
        if self.screen in {
            GuitarHeroScreen.PRESENTATION,
            GuitarHeroScreen.GAMEPLAY,
            GuitarHeroScreen.RESULTS,
            GuitarHeroScreen.HIGH_SCORE,
        } or self.phase in {
            GuitarHeroPhase.AWAIT_GAMEPLAY,
            GuitarHeroPhase.PLAYING,
            GuitarHeroPhase.POST_SONG,
        }:
            self._release_gameplay_inputs(controller)
            return "Guitar Hero recovery suppressed while presentation/game owns screen"
        controller.release_all()
        controller.neutral_sticks()
        if self.screen == GuitarHeroScreen.MAIN_MENU:
            controller.tap("cancel", 0.05)
            return "Guitar Hero bounded menu cancel recovery"
        controller.tap("start", 0.05)
        return "Guitar Hero bounded boot/menu Start recovery"

    def telemetry(self, ctx: ProfileContext) -> dict[str, Any]:
        obs = self._last_obs
        state: dict[str, Any] = {
            "gh_policy_version": self.policy_version,
            "gh_phase": self.phase.value,
            "gh_screen": self.screen.value,
            "gh_difficulty": self.difficulty,
            "gh_route_stage": self.route_stage,
            "gh_notes_attempted": self.notes_attempted,
            "gh_chords_attempted": self.chords_attempted,
            "gh_sustain_ticks": self.sustain_ticks,
            "gh_active_lanes": [LANE_NAMES[i] for i, held in enumerate(self._held) if held],
            "gh_menu_inputs": self.menu_inputs,
            "gh_menu_plan": self._menu_plan_tag,
            "gh_menu_queue_depth": len(self._menu_queue),
            "gh_presentation_wait_ticks": self.presentation_wait_ticks,
            "gh_songs_started": self.songs_started,
            "gh_songs_completed": self.songs_completed,
            "gh_songs_failed": self.songs_failed,
            "gh_post_song_confirms": self._post_song_confirms,
            "gh_unknown_static_ticks": self.unknown_static_ticks,
            "gh_boot_attempts": self._boot_attempts,
        }
        if obs is not None:
            state.update(obs.telemetry())
        return state
