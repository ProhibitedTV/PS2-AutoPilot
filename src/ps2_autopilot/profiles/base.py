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


class GameProfile(ABC):
    name = "base"

    @abstractmethod
    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        """Run one decision tick and return a short action/status label."""

    def recover(self, controller: Controller) -> str:
        controller.release_all()
        controller.tap("start", 0.10)
        controller.tap("confirm", 0.10)
        return "generic recovery"
