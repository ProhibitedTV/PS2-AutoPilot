from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np

from .vision import TemplateMatch


class MaddenVisualState(str, Enum):
    MENU = "menu"
    TRANSITION = "transition"
    FIELD_IDLE = "field_idle"
    LIVE_PLAY = "live_play"
    PLAYCALL = "playcall"
    KICKING = "kicking"
    PAUSED = "paused"
    GAME_OVER = "game_over"


@dataclass(frozen=True)
class MaddenObservation:
    state: MaddenVisualState
    green_ratio: float
    brightness: float
    motion: float
    template_name: str | None
    template_score: float | None


class MaddenVision:
    TEMPLATE_STATES = {
        "playcall": MaddenVisualState.PLAYCALL,
        "play_call": MaddenVisualState.PLAYCALL,
        "kick_meter": MaddenVisualState.KICKING,
        "kicking": MaddenVisualState.KICKING,
        "pause": MaddenVisualState.PAUSED,
        "paused": MaddenVisualState.PAUSED,
        "game_over": MaddenVisualState.GAME_OVER,
        "final": MaddenVisualState.GAME_OVER,
    }

    def __init__(
        self,
        field_green_threshold: float = 0.20,
        live_motion_threshold: float = 0.020,
        template_threshold: float = 0.84,
    ) -> None:
        self.field_green_threshold = field_green_threshold
        self.live_motion_threshold = live_motion_threshold
        self.template_threshold = template_threshold

    @staticmethod
    def green_ratio(frame: np.ndarray) -> float:
        h, w = frame.shape[:2]
        crop = frame[int(h * 0.18) : int(h * 0.92), int(w * 0.06) : int(w * 0.94)]
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        lower = np.array([28, 35, 25], dtype=np.uint8)
        upper = np.array([100, 255, 255], dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)
        return float(np.count_nonzero(mask) / mask.size)

    @staticmethod
    def brightness(frame: np.ndarray) -> float:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return float(np.mean(gray) / 255.0)

    def observe(
        self, frame: np.ndarray, motion: float, template: TemplateMatch | None
    ) -> MaddenObservation:
        green = self.green_ratio(frame)
        bright = self.brightness(frame)
        t_name = template.name.lower() if template else None
        t_score = template.score if template else None

        if template and template.score >= self.template_threshold:
            mapped = self.TEMPLATE_STATES.get(t_name)
            if mapped is not None:
                state = mapped
            elif t_name in {"pre_snap", "post_play"}:
                state = MaddenVisualState.FIELD_IDLE
            else:
                state = MaddenVisualState.MENU
        elif green >= self.field_green_threshold:
            state = (
                MaddenVisualState.LIVE_PLAY
                if motion >= self.live_motion_threshold
                else MaddenVisualState.FIELD_IDLE
            )
        elif motion >= self.live_motion_threshold * 1.8:
            state = MaddenVisualState.TRANSITION
        else:
            state = MaddenVisualState.MENU

        return MaddenObservation(
            state=state,
            green_ratio=green,
            brightness=bright,
            motion=motion,
            template_name=t_name,
            template_score=t_score,
        )
