from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.vision import TemplateMatch


@dataclass(frozen=True)
class ProfileContext:
    frame: np.ndarray
    motion: float
    template: TemplateMatch | None
    now: float
    previous_frame: np.ndarray | None = None


class GameProfile(ABC):
    name = "base"

    @abstractmethod
    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        """Run one decision tick and return a short action/status label."""

    def recover(self, controller: Controller) -> str:
        controller.release_all()
        controller.tap("start", 0.10)
        controller.tap("confirm", 0.10)
        controller.tap("cancel", 0.10)
        controller.hold("up")
        import time

        time.sleep(0.25)
        controller.release("up")
        return "generic unstick sequence"
