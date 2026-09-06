from __future__ import annotations

from collections import deque
from dataclasses import replace

from .guitar_hero_vision import GuitarHeroObservation
from .guitar_hero_vision_v8 import GuitarHeroVisionV8


class GuitarHeroVisionV10(GuitarHeroVisionV8):
    """Temporal validation layer for V8's geometric highway detector.

    V8 fixed the worst structural problem by solving all five fret receptors as one
    layout, but a single colored component inside a lane can still become a hit. A
    real note approaches the strike line over multiple frames; stage lighting and
    incidental color usually does not. V10 tracks the closest per-lane note gap over
    time and only emits a hit after observing a credible downward approach.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.approach_history_frames = max(2, int(cfg.get("approach_history_frames", 4)))
        self.approach_min_delta = float(cfg.get("approach_min_delta", 0.0015))
        self.approach_backtrack_tolerance = float(
            cfg.get("approach_backtrack_tolerance", 0.006)
        )
        self.approach_missing_grace_frames = max(
            0, int(cfg.get("approach_missing_grace_frames", 1))
        )
        self.timing_lead_frames = max(0.0, float(cfg.get("timing_lead_frames", 0.80)))
        self.timing_trigger_max = max(
            self.note_trigger_gap, float(cfg.get("timing_trigger_max", 0.055))
        )

        self._gap_history = [deque(maxlen=self.approach_history_frames) for _ in range(5)]
        self._missing_frames = [0] * 5
        self.approach_confirmed = [False] * 5
        self.note_velocities = [0.0] * 5
        self.dynamic_trigger_gaps = [self.note_trigger_gap] * 5
        self.temporal_hits_suppressed = 0

    def _reset_temporal(self) -> None:
        for history in self._gap_history:
            history.clear()
        self._missing_frames = [0] * 5
        self.approach_confirmed = [False] * 5
        self.note_velocities = [0.0] * 5
        self.dynamic_trigger_gaps = [self.note_trigger_gap] * 5

    def _update_lane(self, lane: int, gap: float | None) -> tuple[bool, float, float]:
        history = self._gap_history[lane]
        if gap is None:
            self._missing_frames[lane] += 1
            if self._missing_frames[lane] > self.approach_missing_grace_frames:
                history.clear()
            return False, 0.0, self.note_trigger_gap

        self._missing_frames[lane] = 0
        gap = float(gap)
        if history and gap > history[-1] + self.approach_backtrack_tolerance:
            history.clear()
        history.append(gap)

        if len(history) < 2:
            return False, 0.0, self.note_trigger_gap

        deltas = [history[index - 1] - history[index] for index in range(1, len(history))]
        positive = [delta for delta in deltas if delta > 0.0]
        velocity = sum(positive) / len(positive) if positive else 0.0
        cumulative = history[0] - history[-1]
        confirmed = (
            cumulative >= self.approach_min_delta
            and velocity >= self.approach_min_delta * 0.5
            and all(delta >= -self.approach_backtrack_tolerance for delta in deltas)
        )
        dynamic = min(
            self.timing_trigger_max,
            self.note_trigger_gap + velocity * self.timing_lead_frames,
        )
        return confirmed, velocity, dynamic

    def analyze(self, frame) -> GuitarHeroObservation:
        obs = super().analyze(frame)

        if not self.layout_locked or self.lock_support < 3:
            self._reset_temporal()
            return replace(obs, hit_strengths=(0.0, 0.0, 0.0, 0.0, 0.0))

        hits: list[float] = []
        for lane, gap in enumerate(self.note_gaps):
            confirmed, velocity, trigger_gap = self._update_lane(lane, gap)
            self.approach_confirmed[lane] = confirmed
            self.note_velocities[lane] = velocity
            self.dynamic_trigger_gaps[lane] = trigger_gap

            should_hit = confirmed and gap is not None and float(gap) <= trigger_gap
            if obs.hit_strengths[lane] > 0.0 and not should_hit:
                self.temporal_hits_suppressed += 1
            hits.append(1.0 if should_hit else 0.0)

        return replace(obs, hit_strengths=tuple(hits))
