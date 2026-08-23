from __future__ import annotations

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_runtime import RuntimeDirective

from .base import ProfileContext
from .madden2005 import MaddenPhase
from .madden2005_v6_runtime import Madden2005V6RuntimeProfile


class Madden2005V7Profile(Madden2005V6RuntimeProfile):
    """Broadcast-paced Madden policy that preserves presentation sequences.

    Post-play replays, celebrations, crowd shots, and other presentation beats are
    part of the stream rather than dead time. The agent therefore stays hands-off
    while Madden is naturally presenting the previous play. It only advances when
    a real continue prompt is visible or the presentation has been stalled for a
    deliberately generous amount of time.
    """

    name = "madden2005"

    MAJOR_PRESENTATION_EVENTS = {
        "touchdown",
        "interception",
        "fumble",
        "field_goal",
        "penalty",
        "sack",
        "punt",
        "kickoff",
    }

    CONTINUE_PROMPTS = (
        "PRESS X",
        "PRESS CROSS",
        "PRESS ANY BUTTON",
        "PRESS A BUTTON",
        "TO CONTINUE",
        "CONTINUE",
    )

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.presentation_hold_seconds = float(cfg.get("presentation_hold_seconds", 8.0))
        self.event_presentation_hold_seconds = float(
            cfg.get("event_presentation_hold_seconds", 12.0)
        )
        self.presentation_stall_nudge_seconds = float(
            cfg.get("presentation_stall_nudge_seconds", 30.0)
        )
        self.presentation_hard_recovery_seconds = float(
            cfg.get("presentation_hard_recovery_seconds", 55.0)
        )
        self.presentation_nudges = 0
        self.presentation_prompt_seen = False

    def _transition_phase(self, new_phase: MaddenPhase, now: float) -> None:
        old = self.phase
        super()._transition_phase(new_phase, now)
        if self.phase == old:
            return
        if new_phase == MaddenPhase.POST_PLAY:
            self.presentation_nudges = 0
            self.presentation_prompt_seen = False

    def _presentation_prompt_visible(self) -> bool:
        text = self._clean_text(self.last_ocr.text)
        return any(prompt in text for prompt in self.CONTINUE_PROMPTS)

    def _major_event_is_recent(self, now: float) -> bool:
        return (
            self.last_game_event in self.MAJOR_PRESENTATION_EVENTS
            and now - self.last_game_event_at <= 14.0
        )

    def _presentation_hold_target(self, now: float) -> float:
        if self._major_event_is_recent(now):
            return self.event_presentation_hold_seconds
        return self.presentation_hold_seconds

    def _post_play(self, controller: Controller, now: float) -> str:
        controller.neutral_sticks()
        age = max(0.0, now - self.phase_since)
        hold = self._presentation_hold_target(now)
        prompt = self._presentation_prompt_visible()
        self.presentation_prompt_seen = self.presentation_prompt_seen or prompt

        # Never fast-forward the natural replay/celebration window. Big football
        # moments intentionally get a longer hold for better broadcast pacing.
        if age < hold:
            remaining = max(0.0, hold - age)
            kind = "event" if self._major_event_is_recent(now) else "standard"
            self.current_action = (
                f"presentation: watch {kind} sequence ({remaining:.1f}s minimum hold)"
            )
            return self.current_action

        if now < self.next_action_at:
            return self.current_action

        # If Madden explicitly asks for input after the presentation, one Cross is
        # appropriate. Never repeatedly confirm through whatever screen comes next.
        if prompt and self.presentation_nudges < 1:
            controller.tap("cross", 0.055)
            self.presentation_nudges += 1
            self.next_action_at = now + 3.0
            self.current_action = "presentation: explicit continue prompt -> CROSS once"
            return self.current_action

        # Otherwise let Madden's broadcast package flow on its own. Only after a
        # long apparent stall do we send one conservative nudge.
        if age < self.presentation_stall_nudge_seconds:
            self.next_action_at = now + 1.5
            self.current_action = f"presentation: auto-flow / hands off ({age:.1f}s)"
            return self.current_action

        if self.presentation_nudges < 1:
            controller.tap("cross", 0.055)
            self.presentation_nudges += 1
            self.next_action_at = now + 5.0
            self.current_action = (
                f"presentation: stalled {age:.1f}s -> single advance nudge"
            )
            return self.current_action

        self.next_action_at = now + 2.0
        self.current_action = "presentation: waiting after single nudge"
        return self.current_action

    def _soft_stall_recovery(self, controller: Controller, now: float) -> str | None:
        # The legacy soft watchdog advances POST_PLAY after ~14 seconds, which is
        # exactly the behavior we do not want for a broadcast. Let the dedicated
        # presentation policy and semantic watchdog own this phase instead.
        if self.phase == MaddenPhase.POST_PLAY:
            return None
        return super()._soft_stall_recovery(controller, now)

    def _progress_recover(
        self,
        controller: Controller,
        directive: RuntimeDirective,
        now: float,
    ) -> str:
        if self.phase != MaddenPhase.POST_PLAY:
            return super()._progress_recover(controller, directive, now)

        controller.neutral_sticks()
        age = max(0.0, now - self.phase_since)
        if age < self.presentation_hard_recovery_seconds:
            self.next_action_at = now + 2.0
            self.current_action = (
                f"presentation watchdog: preserve sequence ({age:.1f}s; "
                f"hard recovery at {self.presentation_hard_recovery_seconds:.0f}s)"
            )
            return self.current_action

        # At this point it is almost certainly a stuck prompt rather than a normal
        # Madden presentation. Permit one additional recovery nudge, then fall back
        # to the normal recovery ladder if the state remains unchanged.
        if self.presentation_nudges < 2:
            controller.tap("cross", 0.055)
            self.presentation_nudges += 1
            self.next_action_at = now + 5.0
            self.current_action = (
                f"presentation watchdog: hard-stall CROSS {self.presentation_nudges}/2"
            )
            return self.current_action
        return super()._progress_recover(controller, directive, now)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        age = max(0.0, ctx.now - self.phase_since) if self.phase == MaddenPhase.POST_PLAY else 0.0
        state.update(
            {
                "presentation_mode": self.phase == MaddenPhase.POST_PLAY,
                "presentation_age": round(age, 1),
                "presentation_hold_seconds": round(
                    self._presentation_hold_target(ctx.now), 1
                )
                if self.phase == MaddenPhase.POST_PLAY
                else 0.0,
                "presentation_nudges": self.presentation_nudges,
                "presentation_prompt_seen": self.presentation_prompt_seen,
            }
        )
        return state
