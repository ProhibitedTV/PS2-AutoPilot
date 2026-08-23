from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import re

import cv2
import numpy as np

from .jak_knowledge import JakProgression


@dataclass(frozen=True)
class JakSceneMetrics:
    novelty: float
    loop_similarity: float
    center_motion: float
    lower_motion: float
    sampled: bool


class JakSceneMemory:
    """Cheap visual memory for exploration without a game-specific detector model.

    The signature is intentionally tiny. It is not used to decide exact geometry;
    it only answers production questions such as "are we still looking at nearly
    the same place?" and "did a movement burst materially change the scene?".
    """

    def __init__(self, sample_seconds: float = 1.25, history: int = 48) -> None:
        self.sample_seconds = max(0.25, float(sample_seconds))
        self.history: deque[np.ndarray] = deque(maxlen=max(8, int(history)))
        self.last_sample_at = -1e9
        self.last_signature: np.ndarray | None = None
        self.last_metrics = JakSceneMetrics(1.0, 0.0, 0.0, 0.0, False)

    @staticmethod
    def signature(frame: np.ndarray) -> np.ndarray:
        if frame is None or frame.size == 0:
            return np.zeros((12 * 20,), dtype=np.float32)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        tiny = cv2.resize(gray, (20, 12), interpolation=cv2.INTER_AREA)
        # Equalize slowly varying renderer brightness while preserving layout.
        tiny = cv2.GaussianBlur(tiny, (3, 3), 0)
        sig = tiny.astype(np.float32) / 255.0
        sig -= float(sig.mean())
        return sig.reshape(-1)

    @staticmethod
    def _similarity(a: np.ndarray, b: np.ndarray) -> float:
        if a.size == 0 or b.size == 0:
            return 0.0
        # Mean absolute difference is stable on PS2-era graphics and cheap enough
        # to run frequently. 0 diff -> 1.0 similarity.
        diff = float(np.mean(np.abs(a - b)))
        return max(0.0, min(1.0, 1.0 - diff * 2.25))

    @staticmethod
    def _regional_motion(previous: np.ndarray | None, current: np.ndarray) -> tuple[float, float]:
        if previous is None or previous.shape != current.shape or current.size == 0:
            return 1.0, 1.0
        a = cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY)
        b = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
        h, w = b.shape[:2]
        center = cv2.absdiff(a[int(h * 0.18):int(h * 0.82), int(w * 0.20):int(w * 0.80)],
                             b[int(h * 0.18):int(h * 0.82), int(w * 0.20):int(w * 0.80)])
        lower = cv2.absdiff(a[int(h * 0.55):h, :], b[int(h * 0.55):h, :])
        return float(center.mean() / 255.0), float(lower.mean() / 255.0)

    def observe(self, frame: np.ndarray, previous: np.ndarray | None, now: float) -> JakSceneMetrics:
        center_motion, lower_motion = self._regional_motion(previous, frame)
        if now - self.last_sample_at < self.sample_seconds:
            self.last_metrics = JakSceneMetrics(
                self.last_metrics.novelty,
                self.last_metrics.loop_similarity,
                center_motion,
                lower_motion,
                False,
            )
            return self.last_metrics

        sig = self.signature(frame)
        novelty = 1.0
        if self.last_signature is not None:
            novelty = 1.0 - self._similarity(sig, self.last_signature)

        loop_similarity = 0.0
        # Ignore the newest few memories so merely standing in place for a second
        # does not immediately count as a loop. We care about revisiting an older view.
        older = list(self.history)[:-4] if len(self.history) > 4 else []
        if older:
            loop_similarity = max(self._similarity(sig, old) for old in older)

        self.history.append(sig)
        self.last_signature = sig
        self.last_sample_at = now
        self.last_metrics = JakSceneMetrics(
            novelty,
            loop_similarity,
            center_motion,
            lower_motion,
            True,
        )
        return self.last_metrics


_PROGRESS_PATTERNS = {
    "power_cells": (
        r"POWER\s*CELLS?\s*[:x]?\s*(\d{1,3})",
        r"CELLS?\s*[:x]?\s*(\d{1,3})",
    ),
    "precursor_orbs": (
        r"PRECURSOR\s*ORBS?\s*[:x]?\s*(\d{1,4})",
        r"ORBS?\s*[:x]?\s*(\d{1,4})",
    ),
    "scout_flies": (
        r"SCOUT\s*FL(?:Y|IES)\s*[:x]?\s*(\d{1,3})",
        r"FL(?:Y|IES)\s*[:x]?\s*(\d{1,3})",
    ),
}


def parse_progress_ocr(text: str) -> JakProgression:
    normalized = re.sub(r"[^A-Z0-9:/ ]+", " ", str(text).upper())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    values: dict[str, int | None] = {
        "power_cells": None,
        "precursor_orbs": None,
        "scout_flies": None,
    }
    for field, patterns in _PROGRESS_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if match:
                values[field] = int(match.group(1))
                break
    return JakProgression(**values)


def merge_progress(old: JakProgression, new: JakProgression) -> JakProgression:
    """Keep monotonic collectible evidence when OCR temporarily loses a counter."""

    def choose(a: int | None, b: int | None) -> int | None:
        if b is None:
            return a
        if a is None:
            return b
        # Collectible totals should not decrease during one campaign. Reject a
        # transient OCR regression rather than poisoning exploration telemetry.
        return max(a, b)

    return JakProgression(
        power_cells=choose(old.power_cells, new.power_cells),
        precursor_orbs=choose(old.precursor_orbs, new.precursor_orbs),
        scout_flies=choose(old.scout_flies, new.scout_flies),
    )
