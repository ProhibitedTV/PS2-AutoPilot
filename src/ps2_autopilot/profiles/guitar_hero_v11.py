from __future__ import annotations

from ps2_autopilot.guitar_hero_vision_v11 import GuitarHeroVisionV11

from .base import ProfileContext
from .guitar_hero_types import GuitarHeroPhase, GuitarHeroScreen
from .guitar_hero_v10 import GuitarHeroV10Profile


class GuitarHeroV11Profile(GuitarHeroV10Profile):
    """V11: protect failure->setlist routing and cut gameplay vision latency.

    The uploaded V9 soak showed the failure transaction itself firing Down+Confirm,
    immediately followed by a false TITLE classification that reset the route to BOOT.
    It then spent the rest of the run waiting/probing Start.  The same bundle also
    showed a critically over-budget loop, so V11 pairs lifecycle hardening with a
    locked-highway fast path rather than adding another note threshold.
    """

    policy_version = 11

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.vision = GuitarHeroVisionV11(self.cfg)
        self.failure_transition_seconds = float(
            self.cfg.get("failure_transition_seconds", 5.0)
        )
        self._failure_transition_until = -1e9
        self.title_guard_suppressions = 0
        self.failure_transition_wait_ticks = 0

    def _explicit_title_template(self, ctx: ProfileContext) -> bool:
        if ctx.template is None or ctx.template.score < self.template_threshold:
            return False
        return self._template_screen(ctx.template.name) is GuitarHeroScreen.TITLE

    def _classify(self, ctx: ProfileContext, obs):
        # V4's image-only title splash detector intentionally has high priority during
        # boot, but the live failure soak proved that it can collide with the animated
        # setlist/presentation reached after NEW SONG. Once we own a non-boot route,
        # only an explicit title template may reset the whole state machine to BOOT.
        protected_route = self.route_stage in {
            "setlist",
            "difficulty",
            "song",
            "gameplay",
            "post_song",
        }
        if protected_route and self.phase is not GuitarHeroPhase.BOOT and not self._explicit_title_template(ctx):
            old_threshold = self.title_splash_threshold
            self.title_splash_threshold = 1.01
            try:
                screen = super()._classify(ctx, obs)
            finally:
                self.title_splash_threshold = old_threshold
            if self._title_splash_score_value >= old_threshold:
                self.title_guard_suppressions += 1
            return screen
        return super()._classify(ctx, obs)

    def _tick_failed_card(self, controller, ctx: ProfileContext, obs) -> str:
        action = super()._tick_failed_card(controller, ctx, obs)
        if action == "song failed: confirm new_song":
            # Establish destination ownership immediately.  Do not wait for the next
            # screen classifier to infer route state from an animated transition.
            self.phase = GuitarHeroPhase.MENU
            self.route_stage = "setlist"
            self._failure_transition_until = ctx.now + self.failure_transition_seconds
        return action

    def _tick_non_gameplay(self, controller, ctx: ProfileContext, obs) -> str:
        if ctx.now < self._failure_transition_until and not self._failed_card_active:
            if self.screen is GuitarHeroScreen.SETLIST:
                self._failure_transition_until = -1e9
            elif self.screen in {
                GuitarHeroScreen.TITLE,
                GuitarHeroScreen.UNKNOWN,
                GuitarHeroScreen.PRESENTATION,
            }:
                self._release_gameplay_inputs(controller)
                self.phase = GuitarHeroPhase.MENU
                self.route_stage = "setlist"
                self.failure_transition_wait_ticks += 1
                return "wait for failed->setlist transition"
        return super()._tick_non_gameplay(controller, ctx, obs)

    def tick(self, controller, ctx: ProfileContext) -> str:
        # Menu morphology is useful while routing, but it is wasted work once a song
        # is launching/playing.  On the first frame where gameplay disappears the
        # runtime leaves PLAYING; the following frame automatically re-enables full
        # menu scoring for results/failure reacquisition.
        self.vision.skip_menu_scores = self.phase in {
            GuitarHeroPhase.AWAIT_GAMEPLAY,
            GuitarHeroPhase.PLAYING,
        }
        return super().tick(controller, ctx)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        vision = self.vision
        state.update(
            {
                "gh_policy_version": self.policy_version,
                "gh_failure_transition_active": ctx.now < self._failure_transition_until,
                "gh_failure_transition_wait_ticks": self.failure_transition_wait_ticks,
                "gh_title_guard_suppressions": self.title_guard_suppressions,
                "gh_fast_layout_frames": vision.fast_layout_frames,
                "gh_full_layout_frames": vision.full_layout_frames,
                "gh_menu_score_frames_skipped": vision.menu_score_frames_skipped,
            }
        )
        return state
