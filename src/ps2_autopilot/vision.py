from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class TemplateMatch:
    name: str
    score: float


def motion_score(previous: np.ndarray | None, current: np.ndarray) -> float:
    if previous is None:
        return 1.0
    a = cv2.resize(previous, (160, 90), interpolation=cv2.INTER_AREA)
    b = cv2.resize(current, (160, 90), interpolation=cv2.INTER_AREA)
    a = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    b = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    diff = cv2.absdiff(a, b)
    return float(np.mean(diff) / 255.0)


class TemplateDetector:
    def __init__(self, template_dir: str | Path) -> None:
        self.template_dir = Path(template_dir)
        self.templates: dict[str, np.ndarray] = {}
        if self.template_dir.exists():
            for path in sorted(self.template_dir.glob("*.png")):
                image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                if image is not None:
                    self.templates[path.stem] = image

    def best_match(self, frame: np.ndarray) -> TemplateMatch | None:
        if not self.templates:
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        best: TemplateMatch | None = None
        for name, template in self.templates.items():
            if template.shape[0] > gray.shape[0] or template.shape[1] > gray.shape[1]:
                continue
            result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
            score = float(result.max())
            if best is None or score > best.score:
                best = TemplateMatch(name=name, score=score)
        return best
