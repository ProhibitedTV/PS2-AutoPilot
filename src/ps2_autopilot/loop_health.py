from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class LoopHealthSnapshot:
    samples: int = 0
    loop_p50_ms: float = 0.0
    loop_p95_ms: float = 0.0
    capture_p50_ms: float = 0.0
    capture_p95_ms: float = 0.0
    policy_p50_ms: float = 0.0
    policy_p95_ms: float = 0.0
    overrun_ratio: float = 0.0
    budget_ms: float = 0.0
    pressure: str = "warming"

    def as_dict(self) -> dict[str, float | int | str]:
        return {
            "loop_health_samples": self.samples,
            "loop_p50_ms": round(self.loop_p50_ms, 2),
            "loop_p95_ms": round(self.loop_p95_ms, 2),
            "capture_p50_ms": round(self.capture_p50_ms, 2),
            "capture_p95_ms": round(self.capture_p95_ms, 2),
            "policy_p50_ms": round(self.policy_p50_ms, 2),
            "policy_p95_ms": round(self.policy_p95_ms, 2),
            "loop_overrun_ratio": round(self.overrun_ratio, 3),
            "loop_budget_ms_rolling": round(self.budget_ms, 2),
            "loop_pressure": self.pressure,
        }


class RollingLoopHealth:
    """Small rolling timing window shared by every game profile.

    Long-running streams need recent control-loop health, not only a lifetime overrun
    counter. The window intentionally stores only primitive floats and computes
    percentiles at telemetry cadence, keeping the collector itself negligible.
    """

    def __init__(self, window: int = 120) -> None:
        size = max(12, min(1200, int(window)))
        self._capture: deque[float] = deque(maxlen=size)
        self._policy: deque[float] = deque(maxlen=size)
        self._loop: deque[float] = deque(maxlen=size)
        self._overrun: deque[int] = deque(maxlen=size)

    def record(self, *, capture_ms: float, policy_ms: float, loop_ms: float, budget_ms: float) -> None:
        self._capture.append(max(0.0, float(capture_ms)))
        self._policy.append(max(0.0, float(policy_ms)))
        self._loop.append(max(0.0, float(loop_ms)))
        self._overrun.append(1 if float(loop_ms) > float(budget_ms) else 0)

    @staticmethod
    def _percentile(values: deque[float], q: float) -> float:
        if not values:
            return 0.0
        return float(np.percentile(np.asarray(values, dtype=np.float32), q))

    def snapshot(self, budget_ms: float) -> LoopHealthSnapshot:
        samples = len(self._loop)
        ratio = float(sum(self._overrun)) / float(max(len(self._overrun), 1))
        loop_p95 = self._percentile(self._loop, 95.0)
        budget = max(0.01, float(budget_ms))
        if samples < 8:
            pressure = "warming"
        elif ratio >= 0.70 or loop_p95 >= budget * 2.0:
            pressure = "critical"
        elif ratio >= 0.35 or loop_p95 >= budget * 1.35:
            pressure = "high"
        elif ratio >= 0.12 or loop_p95 >= budget * 1.05:
            pressure = "elevated"
        else:
            pressure = "healthy"
        return LoopHealthSnapshot(
            samples=samples,
            loop_p50_ms=self._percentile(self._loop, 50.0),
            loop_p95_ms=loop_p95,
            capture_p50_ms=self._percentile(self._capture, 50.0),
            capture_p95_ms=self._percentile(self._capture, 95.0),
            policy_p50_ms=self._percentile(self._policy, 50.0),
            policy_p95_ms=self._percentile(self._policy, 95.0),
            overrun_ratio=ratio,
            budget_ms=budget,
            pressure=pressure,
        )
