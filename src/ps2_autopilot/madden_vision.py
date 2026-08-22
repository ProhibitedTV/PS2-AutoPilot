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
    field_center_x: float
    motion_center_x: float
    brightness: float
    motion: float
    template_name: str | None
    template_score: float | None


class MaddenVision:
    TEMPLATE_STATES = {
        "playcall": MaddenVisualState.PLAYCALL,
        "play_call": MaddenVisualState.PLAYCALL,
        "playcall_offense": MaddenVisualState.PLAYCALL,
        "playcall_defense": MaddenVisualState.PLAYCALL,
        "pre_snap": MaddenVisualState.FIELD_IDLE,
        "pre_snap_offense": MaddenVisualState.FIELD_IDLE,
        "pre_snap_defense": MaddenVisualState.FIELD_IDLE,
        "post_play": MaddenVisualState.FIELD_IDLE,
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
    def _field_crop(frame: np.ndarray) -> tuple[np.ndarray, int, int]:
        h, w = frame.shape[:2]
        x0, x1 = int(w * 0.06), int(w * 0.94)
        y0, y1 = int(h * 0.18), int(h * 0.92)
        return frame[y0:y1, x0:x1], x0, y0

    @classmethod
    def field_features(cls, frame: np.ndarray) -> tuple[float, float]:
        """Return grass coverage and normalized horizontal grass centroid (-1..1)."""
        crop, _, _ = cls._field_crop(frame)
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        lower = np.array([28, 35, 25], dtype=np.uint8)
        upper = np.array([100, 255, 255], dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)
        count = int(np.count_nonzero(mask))
        ratio = float(count / mask.size)
        if count == 0:
            return ratio, 0.0
        xs = np.nonzero(mask)[1]
        center = float(xs.mean() / max(mask.shape[1] - 1, 1))
        return ratio, max(-1.0, min(1.0, center * 2.0 - 1.0))

    @classmethod
    def green_ratio(cls, frame: np.ndarray) -> float:
        return cls.field_features(frame)[0]

    @classmethod
    def motion_centroid_x(
        cls, previous: np.ndarray | None, current: np.ndarray
    ) -> float:
        """Estimate where meaningful field motion is happening horizontally."""
        if previous is None or previous.shape != current.shape:
            return 0.0
        a, _, _ = cls._field_crop(previous)
        b, _, _ = cls._field_crop(current)
        a = cv2.resize(a, (240, 135), interpolation=cv2.INTER_AREA)
        b = cv2.resize(b, (240, 135), interpolation=cv2.INTER_AREA)
        a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
        b = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
        diff = cv2.absdiff(a, b)
        _, mask = cv2.threshold(diff, 18, 255, cv2.THRESH_BINARY)
        mask = cv2.medianBlur(mask, 5)
        ys, xs = np.nonzero(mask)
        if len(xs) < 24:
            return 0.0
        center = float(xs.mean() / max(mask.shape[1] - 1, 1))
        return max(-1.0, min(1.0, center * 2.0 - 1.0))

    @staticmethod
    def brightness(frame: np.ndarray) -> float:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return float(np.mean(gray) / 255.0)

    def observe(
        self,
        frame: np.ndarray,
        motion: float,
        template: TemplateMatch | None,
        previous_frame: np.ndarray | None = None,
    ) -> MaddenObservation:
        green, field_center = self.field_features(frame)
        motion_center = self.motion_centroid_x(previous_frame, frame)
        bright = self.brightness(frame)
        t_name = template.name.lower() if template else None
        t_score = template.score if template else None

        if template and template.score >= self.template_threshold:
            mapped = self.TEMPLATE_STATES.get(t_name)
            if mapped is None and t_name is not None:
                if t_name.startswith(("playcall", "play_call")):
                    mapped = MaddenVisualState.PLAYCALL
                elif t_name.startswith(("pre_snap", "post_play")):
                    mapped = MaddenVisualState.FIELD_IDLE
                elif t_name.startswith(("kick_meter", "kicking")):
                    mapped = MaddenVisualState.KICKING
                elif t_name.startswith(("pause", "paused")):
                    mapped = MaddenVisualState.PAUSED
                elif t_name.startswith(("game_over", "final")):
                    mapped = MaddenVisualState.GAME_OVER
            state = mapped if mapped is not None else MaddenVisualState.MENU
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
            field_center_x=field_center,
            motion_center_x=motion_center,
            brightness=bright,
            motion=motion,
            template_name=t_name,
            template_score=t_score,
        )
