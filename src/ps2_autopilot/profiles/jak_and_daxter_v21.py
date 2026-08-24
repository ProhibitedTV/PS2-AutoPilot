from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import math

import cv2
import numpy as np

from ps2_autopilot.jak_experience import JakExperienceMemory

from .base import ProfileContext
from .jak_and_daxter import JakPhase
from .jak_and_daxter_v20 import JakAndDaxterV20Profile


@dataclass(frozen=True)
class LearningSample:
    at: float
    keys: tuple[str, ...]
    position: tuple[float, float, float] | None


class JakAndDaxterV21Profile(JakAndDaxterV20Profile):
    """Learn route consequences online instead of repeating the same bad excursion.

    V20 finally has a usable read-only semantic side channel, but the controller still
    behaves like an amnesiac: falling/swimming into a bad region, getting stuck, or
    dying only changes the next few seconds of behavior. A restart erases even that.

    V21 adds a deliberately small online experience model:

    * semantic XYZ buckets are learned once live position telemetry proves it actually
      moves; a brightness-invariant visual scene hash remains a fallback;
    * entering confirmed water penalizes the route immediately before entry and creates
      a short episode-level no-return zone;
    * an explicit death or a respawn-like semantic teleport after a recent hazard gives
      that recent route a much stronger persistent penalty;
    * repeated local-stuck failures add a small penalty instead of being forgotten;
    * verified collectible/progression gains reward the recent route and can rehabilitate
      an area that was only ambiguously dangerous;
    * when Jak revisits a learned-danger bucket, the existing V18 atomic backoff/pivot/
      relocation controller preempts generic wandering;
    * successful left/right escapes are remembered and preferred next time;
    * the table is atomically persisted outside ``runtime/`` so run24x7 cleanup and
      process restarts do not wipe what was learned.

    This is intentionally tabular/online rather than a neural RL learner. It produces
    useful behavior after one or two failures, remains inspectable on stream, and can
    later supply training data to a larger policy without making live gameplay the
    training furnace.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        self.learning_enabled = bool(cfg.get("learning_enabled", True))
        self.learning_sample_seconds = max(
            0.20, float(cfg.get("learning_sample_seconds", 0.60))
        )
        self.learning_trail_seconds = max(
            3.0, float(cfg.get("learning_trail_seconds", 10.0))
        )
        self.learning_danger_threshold = max(
            0.50, float(cfg.get("learning_danger_threshold", 1.25))
        )
        self.learning_water_penalty = max(
            0.10, float(cfg.get("learning_water_penalty", 0.72))
        )
        self.learning_death_penalty = max(
            self.learning_water_penalty,
            float(cfg.get("learning_death_penalty", 2.75)),
        )
        self.learning_respawn_penalty = max(
            self.learning_death_penalty,
            float(cfg.get("learning_respawn_penalty", 3.25)),
        )
        self.learning_stall_penalty = max(
            0.05, float(cfg.get("learning_stall_penalty", 0.30))
        )
        self.learning_progress_reward = max(
            0.10, float(cfg.get("learning_progress_reward", 1.00))
        )
        self.learning_episode_avoid_seconds = max(
            4.0, float(cfg.get("learning_episode_avoid_seconds", 30.0))
        )
        self.learning_recent_hazard_seconds = max(
            3.0, float(cfg.get("learning_recent_hazard_seconds", 14.0))
        )
        self.learning_avoid_cooldown_seconds = max(
            2.0, float(cfg.get("learning_avoid_cooldown_seconds", 8.0))
        )
        self.learning_respawn_distance = max(
            4.0, float(cfg.get("learning_respawn_distance", 10.0))
        )
        self.learning_position_validation_min = max(
            0.02, float(cfg.get("learning_position_validation_min", 0.15))
        )
        self.learning_position_validation_max = max(
            self.learning_position_validation_min,
            float(cfg.get("learning_position_validation_max", 4.0)),
        )

        memory_path = str(cfg.get("learning_memory_path", "state/jak_experience.json"))
        self.experience = JakExperienceMemory(
            memory_path,
            bucket_size=float(cfg.get("learning_bucket_size", cfg.get("position_bucket_size", 3.0))),
            save_interval_seconds=float(cfg.get("learning_save_interval_seconds", 2.0)),
            max_cells=int(cfg.get("learning_max_cells", 5000)),
        )

        max_samples = max(
            16,
            int(math.ceil(self.learning_trail_seconds / self.learning_sample_seconds)) + 8,
        )
        self.learning_trail: deque[LearningSample] = deque(maxlen=max_samples)
        self.learning_current_keys: tuple[str, ...] = ()
        self.learning_current_position: tuple[float, float, float] | None = None
        self.learning_current_danger = 0.0
        self.learning_next_sample_at = 0.0

        self.learning_position_probe: tuple[float, float, float] | None = None
        self.learning_position_validated = False
        self.learning_last_position: tuple[float, float, float] | None = None
        self.learning_position_validation_events = 0
        self.learning_position_rejections = 0

        self.learning_episode_avoid_keys: set[str] = set()
        self.learning_episode_avoid_until = 0.0
        self.learning_recent_hazard_until = 0.0
        self.learning_avoid_cooldown_until = 0.0
        self.learning_escape_flip = 1.0
        self.learning_avoid_pending = False
        self.learning_avoid_origin_keys: tuple[str, ...] = ()
        self.learning_avoid_origin_score = 0.0
        self.learning_avoid_direction = 0.0

        self.learning_water_entries = 0
        self.learning_deaths_seen = 0
        self.learning_respawns_seen = 0
        self.learning_stalls_seen = 0
        self.learning_progress_rewards = 0
        self.learning_avoidances = 0
        self.learning_avoid_successes = 0
        self.learning_avoid_failures = 0
        self.learning_episode_blocks = 0
        self.learning_last_event = "memory-loaded" if self.experience.cells else "warming-up"

        self.learning_last_semantic_counts: tuple[int | None, int | None, int | None] | None = None
        self.learning_last_phase = self.phase
        self.learning_last_stuck_failures = int(getattr(self, "local_stuck_failures", 0))
        self.learning_last_progress_events = int(
            getattr(getattr(self, "objective_planner", None), "progress_events", 0)
        )

    @staticmethod
    def _distance(
        a: tuple[float, float, float] | None,
        b: tuple[float, float, float] | None,
    ) -> float:
        if a is None or b is None:
            return 0.0
        return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))

    @staticmethod
    def _scene_key(frame: np.ndarray) -> str | None:
        """Return a cheap brightness-invariant perceptual hash for fallback learning."""
        if frame is None or frame.size == 0:
            return None
        h, w = frame.shape[:2]
        roi = frame[
            max(0, int(round(h * 0.18))):min(h, int(round(h * 0.92))),
            max(0, int(round(w * 0.08))):min(w, int(round(w * 0.92))),
        ]
        if roi.size == 0:
            return None
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        tiny = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
        bits = tiny[:, 1:] >= tiny[:, :-1]
        packed = np.packbits(bits.reshape(-1).astype(np.uint8)).tobytes()
        # Prefixing a short digest keeps JSON keys compact while retaining enough of
        # the dHash to recognize the same local view after lighting changes.
        return "scene:" + hashlib.blake2s(packed, digest_size=8).hexdigest()

    @staticmethod
    def _trusted_semantic(ctx: ProfileContext) -> bool:
        return bool(
            ctx.semantic.get("pine_available")
            and ctx.semantic.get("pine_verified")
            and not ctx.semantic.get("pine_stale")
            and ctx.semantic.get("pine_schema_verified", True)
        )

    def _raw_position(self, ctx: ProfileContext) -> tuple[float, float, float] | None:
        if not self._trusted_semantic(ctx):
            return None
        try:
            position = (
                float(ctx.semantic["jak_x"]),
                float(ctx.semantic["jak_y"]),
                float(ctx.semantic["jak_z"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
        return position if all(math.isfinite(v) for v in position) else None

    def _validated_position(self, ctx: ProfileContext) -> tuple[float, float, float] | None:
        position = self._raw_position(ctx)
        if position is None:
            return None
        if self.learning_position_validated:
            return position

        if self.learning_position_probe is not None:
            moved = self._distance(self.learning_position_probe, position)
            if self.learning_position_validation_min <= moved <= self.learning_position_validation_max:
                self.learning_position_validated = True
                self.learning_position_validation_events += 1
                self.learning_last_event = "semantic-position-validated"
                return position
            if moved > self.learning_position_validation_max:
                self.learning_position_rejections += 1
        self.learning_position_probe = position
        return None

    @staticmethod
    def _read_count(ctx: ProfileContext, *names: str) -> int | None:
        if not JakAndDaxterV21Profile._trusted_semantic(ctx):
            return None
        for name in names:
            value = ctx.semantic.get(name)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return None

    def _semantic_counts(self, ctx: ProfileContext) -> tuple[int | None, int | None, int | None]:
        return (
            self._read_count(ctx, "power_cells", "jak_power_cells"),
            self._read_count(ctx, "precursor_orbs", "jak_precursor_orbs"),
            self._read_count(ctx, "scout_flies", "jak_scout_flies"),
        )

    def _keys_for(self, ctx: ProfileContext, position: tuple[float, float, float] | None) -> tuple[str, ...]:
        keys: list[str] = []
        if self.learning_position_validated:
            key = self.experience.position_key(position)
            if key:
                keys.append(key)
        scene = self._scene_key(ctx.frame)
        if scene:
            keys.append(scene)
        return tuple(dict.fromkeys(keys))

    def _recent_keys(self, now: float, seconds: float) -> tuple[str, ...]:
        cutoff = float(now) - max(0.0, float(seconds))
        keys: list[str] = []
        for sample in self.learning_trail:
            if sample.at < cutoff:
                continue
            keys.extend(sample.keys)
        return tuple(dict.fromkeys(keys))

    def _record_trail(
        self,
        ctx: ProfileContext,
        *,
        event: str,
        seconds: float,
        danger: float = 0.0,
        reward: float = 0.0,
        water_entry: bool = False,
        death: bool = False,
        respawn: bool = False,
        stall: bool = False,
    ) -> tuple[str, ...]:
        keys = self._recent_keys(ctx.now, seconds)
        if not keys:
            keys = self.learning_current_keys
        self.experience.mark(
            keys,
            now=ctx.now,
            event=event,
            danger=danger,
            reward=reward,
            water_entry=water_entry,
            death=death,
            respawn=respawn,
            stall=stall,
        )
        self.learning_last_event = event
        return keys

    def _record_water_entry(self, ctx: ProfileContext) -> None:
        self.learning_water_entries += 1
        keys = self._record_trail(
            ctx,
            event="water-entry",
            seconds=3.0,
            danger=self.learning_water_penalty,
            water_entry=True,
        )
        self.learning_episode_avoid_keys = set(keys)
        self.learning_episode_avoid_until = ctx.now + self.learning_episode_avoid_seconds
        self.learning_recent_hazard_until = ctx.now + self.learning_recent_hazard_seconds

    def _record_death(self, ctx: ProfileContext, *, event: str = "death") -> None:
        self.learning_deaths_seen += 1
        keys = self._record_trail(
            ctx,
            event=event,
            seconds=6.0,
            danger=self.learning_death_penalty,
            death=True,
        )
        self.learning_episode_avoid_keys.update(keys)
        self.learning_episode_avoid_until = ctx.now + self.learning_episode_avoid_seconds * 2.0
        self.learning_recent_hazard_until = ctx.now + self.learning_recent_hazard_seconds

    def _record_respawn(self, ctx: ProfileContext) -> None:
        self.learning_respawns_seen += 1
        keys = self._record_trail(
            ctx,
            event="respawn-after-hazard",
            seconds=7.0,
            danger=self.learning_respawn_penalty,
            respawn=True,
        )
        self.learning_episode_avoid_keys.update(keys)
        self.learning_episode_avoid_until = ctx.now + self.learning_episode_avoid_seconds * 2.0
        self.learning_recent_hazard_until = ctx.now + self.learning_recent_hazard_seconds

    def _record_progress_reward(self, ctx: ProfileContext, *, event: str) -> None:
        self.learning_progress_rewards += 1
        self._record_trail(
            ctx,
            event=event,
            seconds=6.0,
            reward=self.learning_progress_reward,
        )

    def _observe_learning(self, ctx: ProfileContext) -> None:
        if not self.learning_enabled:
            return

        position = self._validated_position(ctx)

        # A large semantic discontinuity immediately after water/death-like trouble is
        # much more likely to be a checkpoint respawn than ordinary locomotion. Record
        # the trail *before* appending the new checkpoint position.
        if (
            self.learning_position_validated
            and position is not None
            and self.learning_last_position is not None
        ):
            jump = self._distance(self.learning_last_position, position)
            if (
                jump >= self.learning_respawn_distance
                and ctx.now <= self.learning_recent_hazard_until
            ):
                self._record_respawn(ctx)
                self.learning_trail.clear()

        self.learning_current_position = position
        self.learning_current_keys = self._keys_for(ctx, position)
        self.learning_current_danger = self.experience.score(self.learning_current_keys)

        if ctx.now >= self.learning_next_sample_at:
            self.learning_next_sample_at = ctx.now + self.learning_sample_seconds
            sample = LearningSample(ctx.now, self.learning_current_keys, position)
            self.learning_trail.append(sample)
            self.experience.touch(self.learning_current_keys, ctx.now)
            cutoff = ctx.now - self.learning_trail_seconds
            while self.learning_trail and self.learning_trail[0].at < cutoff:
                self.learning_trail.popleft()

        if position is not None:
            self.learning_last_position = position

        counts = self._semantic_counts(ctx)
        previous = self.learning_last_semantic_counts
        if previous is not None:
            gained = any(
                old is not None and new is not None and new > old
                for old, new in zip(previous, counts)
            )
            if gained:
                self._record_progress_reward(ctx, event="semantic-progress")
        self.learning_last_semantic_counts = counts

    def _refresh_water_state(self, ctx: ProfileContext) -> None:
        was_active = self.water_escape_active
        super()._refresh_water_state(ctx)
        if self.learning_enabled and not was_active and self.water_escape_active:
            self._record_water_entry(ctx)

    def _episode_danger_active(self, ctx: ProfileContext) -> bool:
        if ctx.now >= self.learning_episode_avoid_until:
            self.learning_episode_avoid_keys.clear()
            return False
        if not self.learning_episode_avoid_keys:
            return False
        return bool(set(self.learning_current_keys) & self.learning_episode_avoid_keys)

    def _danger_requires_avoidance(self, ctx: ProfileContext) -> bool:
        if not self.learning_enabled or ctx.now < self.learning_avoid_cooldown_until:
            return False
        episode = self._episode_danger_active(ctx)
        if episode:
            self.learning_episode_blocks += 1
        return bool(episode or self.learning_current_danger >= self.learning_danger_threshold)

    def _choose_learned_escape_direction(self) -> float:
        preferred = self.experience.preferred_escape_direction(self.learning_current_keys)
        if preferred is not None:
            return preferred
        if abs(float(getattr(self, "route_bias", 0.0))) >= 0.08:
            return -1.0 if self.route_bias > 0.0 else 1.0
        direction = self.learning_escape_flip
        self.learning_escape_flip *= -1.0
        return direction

    def _start_learned_avoidance(self, ctx: ProfileContext) -> None:
        direction = self._choose_learned_escape_direction()
        self.learning_avoid_pending = True
        self.learning_avoid_origin_keys = self.learning_current_keys
        self.learning_avoid_origin_score = max(
            self.learning_current_danger,
            self.learning_danger_threshold if self._episode_danger_active(ctx) else 0.0,
        )
        self.learning_avoid_direction = direction
        self.learning_avoidances += 1
        self.learning_last_event = "avoid-known-danger"
        self.learning_avoid_cooldown_until = ctx.now + self.learning_avoid_cooldown_seconds
        self._begin_navigation_commit(
            ctx,
            reason="learned-danger",
            direction=direction,
        )

    def _finish_learned_avoidance(self, ctx: ProfileContext) -> None:
        if not self.learning_avoid_pending or self.navigation_commit_active:
            return
        current = self.experience.score(self.learning_current_keys)
        success = bool(
            not self.water_escape_active
            and current < max(
                self.learning_danger_threshold * 0.70,
                self.learning_avoid_origin_score * 0.75,
            )
        )
        self.experience.mark_escape_result(
            self.learning_avoid_origin_keys,
            direction=self.learning_avoid_direction,
            success=success,
            now=ctx.now,
        )
        if success:
            self.learning_avoid_successes += 1
            self.learning_last_event = "learned-escape-success"
        else:
            self.learning_avoid_failures += 1
            self.learning_last_event = "learned-escape-failure"
            self.experience.mark(
                self.learning_avoid_origin_keys,
                now=ctx.now,
                event="learned-escape-failure",
                danger=0.25,
            )
        self.learning_avoid_pending = False
        self.learning_avoid_origin_keys = ()
        self.learning_avoid_origin_score = 0.0
        self.learning_avoid_direction = 0.0
        self.learning_avoid_cooldown_until = max(
            self.learning_avoid_cooldown_until,
            ctx.now + self.learning_avoid_cooldown_seconds * 0.5,
        )

    def _on_foot(self, controller, ctx: ProfileContext) -> str:
        # Existing atomic navigation owns control until it finishes or safety aborts.
        if self.navigation_commit_active:
            action = super()._on_foot(controller, ctx)
            self._finish_learned_avoidance(ctx)
            return action

        # Memory lookup is cheap. Only pay the extra water refresh when the current
        # learned state is actually dangerous enough to preempt ordinary navigation.
        if (
            self._danger_requires_avoidance(ctx)
            and not self.skill_active
            and not self.land_scan_active
            and not self.target_resolution_active
            and not self.mobility_active
            and not getattr(self, "ledge_jump_active", False)
        ):
            self._refresh_water_state(ctx)
            if not self.water_escape_active and not self.shoreline_risk.active:
                self._start_learned_avoidance(ctx)
                return self._service_navigation_commit(controller, ctx)

        action = super()._on_foot(controller, ctx)
        self._finish_learned_avoidance(ctx)
        return action

    def tick(self, controller, ctx: ProfileContext) -> str:
        self._observe_learning(ctx)
        phase_before = self.phase
        stuck_before = int(getattr(self, "local_stuck_failures", 0))
        progress_before = int(
            getattr(getattr(self, "objective_planner", None), "progress_events", 0)
        )

        action = super().tick(controller, ctx)

        if self.phase == JakPhase.DEATH and phase_before != JakPhase.DEATH:
            self._record_death(ctx)

        stuck_after = int(getattr(self, "local_stuck_failures", 0))
        if stuck_after > stuck_before:
            self.learning_stalls_seen += stuck_after - stuck_before
            self._record_trail(
                ctx,
                event="local-stuck-failure",
                seconds=2.5,
                danger=self.learning_stall_penalty,
                stall=True,
            )

        progress_after = int(
            getattr(getattr(self, "objective_planner", None), "progress_events", 0)
        )
        if progress_after > progress_before:
            # Semantic counts are normally the first signal. This second path allows
            # OCR-only progress to reinforce a successful route without double-counting
            # more than one bounded reward per planner transition.
            current_counts = self._semantic_counts(ctx)
            if current_counts == self.learning_last_semantic_counts:
                self._record_progress_reward(ctx, event="planner-progress")

        self.learning_last_phase = self.phase
        self.learning_last_stuck_failures = stuck_after
        self.learning_last_progress_events = progress_after
        self.learning_current_danger = self.experience.score(self.learning_current_keys)
        self.experience.maybe_save(ctx.now)
        return action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        episode_active = self._episode_danger_active(ctx)
        state.update(self.experience.telemetry())
        state.update(
            {
                "jak_policy_version": "v21",
                "jak_learning_enabled": self.learning_enabled,
                "jak_learning_position_validated": self.learning_position_validated,
                "jak_learning_position_validation_events": self.learning_position_validation_events,
                "jak_learning_position_rejections": self.learning_position_rejections,
                "jak_learning_current_keys": list(self.learning_current_keys),
                "jak_learning_current_danger": round(self.learning_current_danger, 3),
                "jak_learning_episode_avoid_active": episode_active,
                "jak_learning_episode_avoid_for": round(
                    max(0.0, self.learning_episode_avoid_until - ctx.now), 2
                ),
                "jak_learning_recent_hazard_for": round(
                    max(0.0, self.learning_recent_hazard_until - ctx.now), 2
                ),
                "jak_learning_water_entries_seen": self.learning_water_entries,
                "jak_learning_deaths_seen": self.learning_deaths_seen,
                "jak_learning_respawns_seen": self.learning_respawns_seen,
                "jak_learning_stalls_seen": self.learning_stalls_seen,
                "jak_learning_progress_rewards": self.learning_progress_rewards,
                "jak_learning_avoidances": self.learning_avoidances,
                "jak_learning_avoid_successes": self.learning_avoid_successes,
                "jak_learning_avoid_failures": self.learning_avoid_failures,
                "jak_learning_episode_blocks": self.learning_episode_blocks,
                "jak_learning_avoid_pending": self.learning_avoid_pending,
                "jak_learning_last_event_v21": self.learning_last_event,
            }
        )
        return state

    def _v16_stream_intent(self) -> str:
        if self.learning_avoid_pending or (
            self.navigation_commit_active and self.navigation_commit_reason == "learned-danger"
        ):
            return f"LEARN · AVOID KNOWN DANGER ({self.navigation_commit_stage.upper()})"
        if self.learning_current_danger >= self.learning_danger_threshold:
            return f"LEARN · KNOWN DANGER {self.learning_current_danger:.1f}"
        return super()._v16_stream_intent()
