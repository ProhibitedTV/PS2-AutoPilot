from __future__ import annotations

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .guitar_hero_types import GuitarHeroScreen
from .guitar_hero_v4 import GuitarHeroV4Profile


class GuitarHeroV5Profile(GuitarHeroV4Profile):
    """V5: use the live-proven menu-confirm path on GH1's controller tutorial.

    V3/V4 correctly recognized the first-run ``USING THE GUITAR CONTROLLER`` card,
    but treated its printed ``Continue (Green button)`` literally and sent PS2 L2.
    On the current PCSX2 virtual-controller path that trigger was accepted by the
    backend yet the card did not advance, exhausting the retry budget.

    Normal Guitar Hero menus in this profile already use ``confirm`` (virtual Xbox A
    -> PS2 Cross). V5 therefore tries that menu-confirm path first and keeps L2 only
    as a bounded fallback. Gameplay note mapping remains unchanged: L2/L1/R1/R2/X
    still represent green/red/yellow/blue/orange while a song is active.
    """

    policy_version = 5

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        configured = self.cfg.get("controller_tutorial_continue_actions")
        if configured is None:
            actions = ("confirm", "l2", "confirm")
        elif isinstance(configured, str):
            actions = tuple(part.strip().lower() for part in configured.split(",") if part.strip())
        else:
            actions = tuple(str(part).strip().lower() for part in configured if str(part).strip())
        if not actions:
            actions = ("confirm",)
        allowed = {"confirm", "cross", "l2"}
        invalid = [action for action in actions if action not in allowed]
        if invalid:
            raise ValueError(
                "Guitar Hero controller tutorial actions must be confirm, cross, or l2; "
                f"got {invalid!r}"
            )
        self.controller_tutorial_continue_actions = actions
        # The action sequence is the retry budget. Respect an explicitly smaller
        # configured max-attempt count, otherwise allow the complete sequence.
        explicit_max = self.cfg.get("controller_tutorial_max_attempts")
        if explicit_max is None:
            self.controller_tutorial_max_attempts = len(actions)
        else:
            self.controller_tutorial_max_attempts = max(
                1, min(int(explicit_max), len(actions))
            )
        self._controller_tutorial_last_action = ""

    def _tutorial_action_for_attempt(self) -> str:
        index = min(
            self._controller_tutorial_attempts,
            len(self.controller_tutorial_continue_actions) - 1,
        )
        return self.controller_tutorial_continue_actions[index]

    def _tutorial_tick(self, controller: Controller, ctx: ProfileContext, score: float) -> str:
        now = ctx.now
        if not self._controller_tutorial_active:
            self._controller_tutorial_active = True
            self._controller_tutorial_since = now
            self._controller_tutorial_attempts = 0
            self.controller_tutorial_episodes += 1

        self._controller_tutorial_last_seen = now
        self._controller_tutorial_score = score

        self._update_screen(GuitarHeroScreen.PRESENTATION, now)
        self._release_gameplay_inputs(controller)

        stable_for = now - self._controller_tutorial_since
        since_input = now - self._controller_tutorial_last_input
        ready = stable_for >= self.controller_tutorial_settle_seconds
        retry_ready = (
            self._controller_tutorial_attempts == 0
            or since_input >= self.controller_tutorial_retry_seconds
        )
        if (
            ready
            and retry_ready
            and self._controller_tutorial_attempts < self.controller_tutorial_max_attempts
        ):
            action = self._tutorial_action_for_attempt()
            controller.release_all()
            controller.neutral_sticks()
            controller.tap(action, 0.08 if action in {"confirm", "cross"} else 0.06)
            self.menu_inputs += 1
            self.controller_tutorial_inputs += 1
            self._controller_tutorial_attempts += 1
            self._controller_tutorial_last_input = now
            self._controller_tutorial_last_action = action
            label = "confirm/Cross" if action in {"confirm", "cross"} else "green/L2 fallback"
            return (
                f"controller tutorial: Continue with {label} "
                f"({self._controller_tutorial_attempts}/{self.controller_tutorial_max_attempts})"
            )

        if self._controller_tutorial_attempts >= self.controller_tutorial_max_attempts:
            return "controller tutorial: confirm fallback budget exhausted; wait for visual progress"
        return "controller tutorial settling; wait before Continue input"

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "gh_policy_version": self.policy_version,
                "gh_controller_tutorial_last_action": self._controller_tutorial_last_action,
                "gh_controller_tutorial_continue_actions": list(
                    self.controller_tutorial_continue_actions
                ),
            }
        )
        return state
