from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any

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
    """Full-frame template recognition without stalling the control loop.

    Matching a growing template library against a 1080p PCSX2 frame can take much
    longer than a gameplay cycle.  In asynchronous mode there is at most one scan in
    flight and new callers receive the freshest bounded-age result.  The worker is
    deliberately latest-frame-only: it never builds a queue of already-stale game
    frames while recognition is busy.
    """

    def __init__(
        self,
        template_dir: str | Path,
        *,
        asynchronous: bool = False,
        scan_interval_seconds: float = 0.10,
        result_max_age_seconds: float = 1.25,
    ) -> None:
        self.template_dir = Path(template_dir)
        self.templates: dict[str, np.ndarray] = {}
        if self.template_dir.exists():
            for path in sorted(self.template_dir.glob("*.png")):
                image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                if image is not None:
                    self.templates[path.stem] = image

        self.asynchronous = bool(asynchronous and self.templates)
        self.scan_interval_seconds = max(0.0, float(scan_interval_seconds))
        self.result_max_age_seconds = max(0.10, float(result_max_age_seconds))
        self._executor = (
            ThreadPoolExecutor(max_workers=1, thread_name_prefix="template-vision")
            if self.asynchronous
            else None
        )
        self._future: Future[tuple[TemplateMatch | None, float]] | None = None
        self._last_submit_at = -1e9
        self._last_result: TemplateMatch | None = None
        self._last_result_at = -1e9
        self._last_scan_ms = 0.0
        self._scans_started = 0
        self._scans_completed = 0
        self._busy_skips = 0
        self._scan_errors = 0

    def _scan_frame(self, frame: np.ndarray) -> tuple[TemplateMatch | None, float]:
        started = time.perf_counter()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        best: TemplateMatch | None = None
        for name, template in self.templates.items():
            if template.shape[0] > gray.shape[0] or template.shape[1] > gray.shape[1]:
                continue
            result = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
            score = float(result.max())
            if best is None or score > best.score:
                best = TemplateMatch(name=name, score=score)
        return best, (time.perf_counter() - started) * 1000.0

    def _collect_finished_scan(self, now: float) -> None:
        if self._future is None or not self._future.done():
            return
        try:
            self._last_result, self._last_scan_ms = self._future.result()
            # Age the result from frame submission, not worker completion. A scan
            # that itself took 600 ms is already 600 ms old when it becomes visible.
            self._last_result_at = self._last_submit_at
            self._scans_completed += 1
        except Exception:
            # Template recognition is advisory evidence. A bad local capture or an
            # OpenCV worker failure must not terminate autonomous gameplay.
            self._scan_errors += 1
            self._last_result = None
            self._last_result_at = now
        finally:
            self._future = None

    def best_match(self, frame: np.ndarray) -> TemplateMatch | None:
        if not self.templates:
            return None
        if not self.asynchronous:
            self._scans_started += 1
            result, self._last_scan_ms = self._scan_frame(frame)
            self._scans_completed += 1
            self._last_result = result
            self._last_result_at = time.monotonic()
            return result

        now = time.monotonic()
        self._collect_finished_scan(now)
        if self._future is None and now - self._last_submit_at >= self.scan_interval_seconds:
            # Copy only when a worker is ready. This bounds memory and ensures the
            # next scan observes the newest frame rather than a queued old frame.
            assert self._executor is not None
            self._future = self._executor.submit(self._scan_frame, frame.copy())
            self._last_submit_at = now
            self._scans_started += 1
        elif self._future is not None:
            self._busy_skips += 1

        age = now - self._last_result_at
        if 0.0 <= age <= self.result_max_age_seconds:
            return self._last_result
        return None

    def telemetry(self) -> dict[str, Any]:
        now = time.monotonic()
        age_ms = None
        if self._last_result_at > -1e8:
            age_ms = round(max(0.0, now - self._last_result_at) * 1000.0, 2)
        return {
            "template_async": self.asynchronous,
            "template_count": len(self.templates),
            "template_scan_busy": self._future is not None,
            "template_scan_ms": round(self._last_scan_ms, 2),
            "template_result_age_ms": age_ms,
            "template_scans_started": self._scans_started,
            "template_scans_completed": self._scans_completed,
            "template_scan_busy_skips": self._busy_skips,
            "template_scan_errors": self._scan_errors,
        }

    def close(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
            self._executor = None
