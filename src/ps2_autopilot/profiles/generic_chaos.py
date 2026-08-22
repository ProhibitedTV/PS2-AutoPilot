from __future__ import annotations

import random

from .base import GameProfile, ProfileContext
from ps2_autopilot.controllers.base import Controller


class GenericChaosProfile(GameProfile):
    name = "generic_chaos"

    def __init__(self, action_seconds: float = 1.25) -> None:
        self.action_seconds = action_seconds
        self.next_action_at = 0.0
        self.current = "boot"

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        if ctx.now < self.next_action_at:
            return self.current
        controller.release_all()
        action = random.choice(["confirm", "start", "up", "down", "left", "right"])
        controller.tap(action)
        self.current = f"smoke {action}"
        self.next_action_at = ctx.now + self.action_seconds
        return self.current
