from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SkillStats:
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    safety_aborts: int = 0
    retries: int = 0
    total_latency_seconds: float = 0.0
    last_latency_seconds: float = 0.0
    last_result: str = "never"


@dataclass
class ActiveSkill:
    name: str
    phase: str
    started_at: float
    phase_started_at: float
    phase_until: float
    timeout_at: float
    heading: float = 0.0
    forward: float = 0.0
    retries: int = 0
    start_position: tuple[float, float, float] | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class AtomicSkillEngine:
    """Small inspectable state machine for Jak locomotion transactions.

    The profile owns controller semantics; this object owns transaction lifetime,
    explicit phases, timeout/retry accounting, and per-skill outcome metrics. Keeping
    those concerns separate lets the same safety/verification rules apply to hops,
    double-jumps, roll-jumps, dives, and platform chains without turning the policy
    into another collection of unrelated timers.
    """

    def __init__(self) -> None:
        self.active: ActiveSkill | None = None
        self.stats: dict[str, SkillStats] = {}
        self.last_name = "none"
        self.last_phase = "idle"
        self.last_result = "none"
        self.last_reason = ""

    @property
    def is_active(self) -> bool:
        return self.active is not None

    def _stats(self, name: str) -> SkillStats:
        if name not in self.stats:
            self.stats[name] = SkillStats()
        return self.stats[name]

    def start(
        self,
        name: str,
        *,
        now: float,
        phase: str = "align",
        phase_seconds: float = 0.0,
        timeout_seconds: float = 2.0,
        heading: float = 0.0,
        forward: float = 0.0,
        start_position: tuple[float, float, float] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ActiveSkill:
        if self.active is not None:
            raise RuntimeError(f"skill already active: {self.active.name}/{self.active.phase}")
        item = ActiveSkill(
            name=str(name),
            phase=str(phase),
            started_at=float(now),
            phase_started_at=float(now),
            phase_until=float(now) + max(0.0, float(phase_seconds)),
            timeout_at=float(now) + max(0.10, float(timeout_seconds)),
            heading=float(heading),
            forward=float(forward),
            start_position=start_position,
            payload=dict(payload or {}),
        )
        self.active = item
        self._stats(item.name).attempts += 1
        self.last_name = item.name
        self.last_phase = item.phase
        self.last_result = "running"
        self.last_reason = str(item.payload.get("reason", ""))
        return item

    def transition(self, phase: str, *, now: float, seconds: float = 0.0) -> ActiveSkill:
        if self.active is None:
            raise RuntimeError("cannot transition idle skill engine")
        self.active.phase = str(phase)
        self.active.phase_started_at = float(now)
        self.active.phase_until = float(now) + max(0.0, float(seconds))
        self.last_phase = self.active.phase
        return self.active

    def retry(self, *, now: float, phase: str, seconds: float = 0.0) -> ActiveSkill:
        if self.active is None:
            raise RuntimeError("cannot retry idle skill engine")
        self.active.retries += 1
        self._stats(self.active.name).retries += 1
        return self.transition(phase, now=now, seconds=seconds)

    def timed_out(self, now: float) -> bool:
        return bool(self.active is not None and float(now) >= self.active.timeout_at)

    def phase_done(self, now: float) -> bool:
        return bool(self.active is not None and float(now) >= self.active.phase_until)

    def finish(self, *, now: float, success: bool, result: str) -> ActiveSkill | None:
        item = self.active
        if item is None:
            return None
        latency = max(0.0, float(now) - item.started_at)
        stats = self._stats(item.name)
        if success:
            stats.successes += 1
        else:
            stats.failures += 1
        stats.total_latency_seconds += latency
        stats.last_latency_seconds = latency
        stats.last_result = str(result)
        self.last_name = item.name
        self.last_phase = item.phase
        self.last_result = str(result)
        self.last_reason = str(item.payload.get("reason", ""))
        self.active = None
        return item

    def abort(self, *, now: float, reason: str) -> ActiveSkill | None:
        item = self.active
        if item is None:
            return None
        latency = max(0.0, float(now) - item.started_at)
        stats = self._stats(item.name)
        stats.safety_aborts += 1
        stats.total_latency_seconds += latency
        stats.last_latency_seconds = latency
        stats.last_result = f"abort:{reason}"
        self.last_name = item.name
        self.last_phase = item.phase
        self.last_result = f"abort:{reason}"
        self.last_reason = str(reason)
        self.active = None
        return item

    def telemetry(self, now: float) -> dict[str, Any]:
        active = self.active
        rows: dict[str, Any] = {}
        for name, stats in sorted(self.stats.items()):
            prefix = f"jak_skill_{name}"
            average = (
                stats.total_latency_seconds / max(1, stats.successes + stats.failures + stats.safety_aborts)
            )
            rows.update(
                {
                    f"{prefix}_attempts": stats.attempts,
                    f"{prefix}_successes": stats.successes,
                    f"{prefix}_failures": stats.failures,
                    f"{prefix}_safety_aborts": stats.safety_aborts,
                    f"{prefix}_retries": stats.retries,
                    f"{prefix}_avg_latency_ms": round(average * 1000.0, 1),
                    f"{prefix}_last_latency_ms": round(stats.last_latency_seconds * 1000.0, 1),
                    f"{prefix}_last_result": stats.last_result,
                }
            )
        rows.update(
            {
                "jak_atomic_skill_active": active is not None,
                "jak_atomic_skill": active.name if active else "none",
                "jak_atomic_skill_phase": active.phase if active else "idle",
                "jak_atomic_skill_age": round(max(0.0, float(now) - active.started_at), 3)
                if active
                else 0.0,
                "jak_atomic_skill_retries": active.retries if active else 0,
                "jak_atomic_skill_last": self.last_name,
                "jak_atomic_skill_last_phase": self.last_phase,
                "jak_atomic_skill_last_result": self.last_result,
                "jak_atomic_skill_last_reason": self.last_reason,
            }
        )
        return rows
