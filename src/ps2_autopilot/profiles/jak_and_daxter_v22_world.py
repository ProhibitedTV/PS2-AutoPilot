from __future__ import annotations

from ps2_autopilot.jak_world_graph import JakWorldGraph

from .base import ProfileContext
from .jak_and_daxter_v22_hardened import JakAndDaxterV22Profile as _JakAndDaxterV22SkillProfile


class JakAndDaxterV22Profile(_JakAndDaxterV22SkillProfile):
    """Add a persistent, evidence-built topology above the V22 skill controller.

    The graph deliberately does not know any authored Geyser coordinates. It wakes up
    only after V21 validates semantic position, learns nodes from visited XYZ buckets,
    and learns typed directed edges from actual bucket crossings. Consequences from the
    V21 experience table are copied onto nodes/edges so the topology can later support
    route planning without throwing away the low-sample learning already earned live.
    """

    SKILL_EDGE_TYPES = {
        "hop_step": "GAP_JUMP",
        "jump": "GAP_JUMP",
        "double_jump": "GAP_JUMP",
        "roll_jump": "ROLL_JUMP",
        "dive": "DIVE",
        "platform_chain": "PLATFORM_CHAIN",
    }

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        graph_path = str(cfg.get("world_graph_path", "state/jak_world_graph.json"))
        self.world_graph_enabled = bool(cfg.get("world_graph_enabled", True))
        self.world_graph = JakWorldGraph(
            graph_path,
            bucket_size=float(
                cfg.get(
                    "world_graph_bucket_size",
                    cfg.get("learning_bucket_size", cfg.get("position_bucket_size", 3.0)),
                )
            ),
            save_interval_seconds=float(cfg.get("world_graph_save_interval_seconds", 2.0)),
            max_nodes=int(cfg.get("world_graph_max_nodes", 2500)),
            max_edges=int(cfg.get("world_graph_max_edges", 8000)),
        )
        self.world_graph_edge_mode = "WALK"
        self.world_graph_edge_mode_until = 0.0
        self.world_graph_edge_hold_seconds = max(
            0.5, float(cfg.get("world_graph_edge_hold_seconds", 3.0))
        )
        self.world_graph_transitions_seen = 0
        self.world_graph_semantic_samples = 0
        self.world_graph_waiting_samples = 0
        self.world_graph_consequence_updates = 0
        self.world_graph_last_transition = "none"
        self.world_graph_last_status = "loaded" if self.world_graph.nodes else "waiting-semantic-position"

    def _set_graph_edge_mode(self, edge_type: str, now: float, *, seconds: float | None = None) -> None:
        self.world_graph_edge_mode = str(edge_type or "WALK").upper()
        self.world_graph_edge_mode_until = max(
            self.world_graph_edge_mode_until,
            float(now) + (self.world_graph_edge_hold_seconds if seconds is None else max(0.1, seconds)),
        )

    def _begin_atomic(self, name: str, ctx: ProfileContext, **kwargs) -> None:
        super()._begin_atomic(name, ctx, **kwargs)
        edge_type = self.SKILL_EDGE_TYPES.get(str(name), "WALK")
        self._set_graph_edge_mode(edge_type, ctx.now)

    def _graph_position(self):
        if not bool(getattr(self, "learning_position_validated", False)):
            return None
        return getattr(self, "learning_current_position", None)

    def _graph_experience(self, position) -> tuple[float, float]:
        if position is None:
            return 0.0, 0.0
        key = self.experience.position_key(position)
        cell = self.experience.cells.get(key) if key else None
        if cell is None:
            return 0.0, 0.0
        return float(cell.score), float(cell.reward)

    def _graph_labels(self) -> tuple[str, ...]:
        labels: list[str] = []
        objective = getattr(self, "objective", None)
        stage = getattr(objective, "stage", None)
        stage_value = getattr(stage, "value", None)
        if stage_value:
            labels.append(f"objective:{stage_value}")
        cue = getattr(getattr(self, "gameplay_cue", None), "kind", "none")
        if cue and cue != "none":
            labels.append(f"cue:{cue}")
        visual = getattr(getattr(self, "visual_goal", None), "kind", "none")
        if visual and visual != "none":
            labels.append(f"visual:{visual}")
        if bool(getattr(self, "water_escape_active", False)):
            labels.append("hazard:water")
        if self.atomic_skills.is_active and self.atomic_skills.active is not None:
            labels.append(f"skill:{self.atomic_skills.active.name}")
        return tuple(labels)

    def _current_graph_edge_type(self, ctx: ProfileContext) -> str:
        if bool(getattr(self, "water_escape_active", False)):
            self._set_graph_edge_mode("SWIM_ESCAPE", ctx.now)
            return "SWIM_ESCAPE"
        if self.atomic_skills.is_active and self.atomic_skills.active is not None:
            kind = self.SKILL_EDGE_TYPES.get(self.atomic_skills.active.name, "WALK")
            self._set_graph_edge_mode(kind, ctx.now)
            return kind
        objective = getattr(self, "objective", None)
        stage = getattr(getattr(objective, "stage", None), "value", "")
        cue = getattr(getattr(self, "gameplay_cue", None), "kind", "none")
        if stage == "blue_eco_door" and cue == "blue_eco":
            self._set_graph_edge_mode("ECO_RUN", ctx.now)
            return "ECO_RUN"
        if ctx.now <= self.world_graph_edge_mode_until:
            return self.world_graph_edge_mode
        self.world_graph_edge_mode = "WALK"
        return "WALK"

    def _observe_world_graph(self, ctx: ProfileContext) -> None:
        if not self.world_graph_enabled:
            self.world_graph_last_status = "disabled"
            return
        position = self._graph_position()
        if position is None:
            self.world_graph_waiting_samples += 1
            self.world_graph_last_status = "waiting-semantic-position"
            return
        self.world_graph_semantic_samples += 1
        danger, reward = self._graph_experience(position)
        edge_type = self._current_graph_edge_type(ctx)
        transition = self.world_graph.observe(
            position,
            now=ctx.now,
            edge_type=edge_type,
            danger=danger,
            reward=reward,
            labels=self._graph_labels(),
        )
        if transition is not None:
            self.world_graph_transitions_seen += 1
            self.world_graph_last_transition = (
                f"{transition.src}->{transition.dst}:{transition.edge_type}"
            )
            # Once a typed edge has actually crossed a bucket, ordinary walking owns
            # future transitions unless another skill/hazard establishes a new mode.
            if not self.atomic_skills.is_active and not self.water_escape_active:
                self.world_graph_edge_mode = "WALK"
                self.world_graph_edge_mode_until = ctx.now
        self.world_graph_last_status = "mapping"
        self.world_graph.maybe_save(ctx.now)

    def _mark_graph_consequence(
        self,
        ctx: ProfileContext,
        *,
        danger: float = 0.0,
        reward: float = 0.0,
        label: str,
        edge_failure: bool = False,
    ) -> None:
        if not self.world_graph_enabled:
            return
        self.world_graph.mark_current(
            now=ctx.now,
            danger=danger if danger > 0.0 else None,
            reward=reward if reward > 0.0 else None,
            label=label,
        )
        self.world_graph.mark_last_edge(
            now=ctx.now,
            success=False if edge_failure else None,
            danger=danger,
            reward=reward,
        )
        self.world_graph_consequence_updates += 1
        self.world_graph.maybe_save(ctx.now)

    def _record_water_entry(self, ctx: ProfileContext) -> None:
        super()._record_water_entry(ctx)
        self._set_graph_edge_mode("SWIM_ESCAPE", ctx.now)
        self._mark_graph_consequence(
            ctx,
            danger=self.learning_water_penalty,
            label="outcome:water-entry",
            edge_failure=True,
        )

    def _record_death(self, ctx: ProfileContext, *, event: str = "death") -> None:
        super()._record_death(ctx, event=event)
        self._mark_graph_consequence(
            ctx,
            danger=self.learning_death_penalty,
            label=f"outcome:{event}",
            edge_failure=True,
        )

    def _record_respawn(self, ctx: ProfileContext) -> None:
        super()._record_respawn(ctx)
        self._mark_graph_consequence(
            ctx,
            danger=self.learning_respawn_penalty,
            label="outcome:respawn",
            edge_failure=True,
        )

    def _record_progress_reward(self, ctx: ProfileContext, *, event: str) -> None:
        super()._record_progress_reward(ctx, event=event)
        self._mark_graph_consequence(
            ctx,
            reward=self.learning_progress_reward,
            label=f"outcome:{event}",
            edge_failure=False,
        )

    def tick(self, controller, ctx: ProfileContext) -> str:
        stalls_before = int(getattr(self, "learning_stalls_seen", 0))
        action = super().tick(controller, ctx)
        if int(getattr(self, "learning_stalls_seen", 0)) > stalls_before:
            self._mark_graph_consequence(
                ctx,
                danger=self.learning_stall_penalty,
                label="outcome:stuck",
                edge_failure=True,
            )
        self._observe_world_graph(ctx)
        return action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(self.world_graph.telemetry())
        state.update(
            {
                "jak_policy_version": "v22",
                "jak_world_graph_enabled": self.world_graph_enabled,
                "jak_world_graph_status": self.world_graph_last_status,
                "jak_world_graph_edge_mode": self._current_graph_edge_type(ctx),
                "jak_world_graph_semantic_samples": self.world_graph_semantic_samples,
                "jak_world_graph_waiting_samples": self.world_graph_waiting_samples,
                "jak_world_graph_transitions_seen": self.world_graph_transitions_seen,
                "jak_world_graph_consequence_updates": self.world_graph_consequence_updates,
                "jak_world_graph_last_transition_v22": self.world_graph_last_transition,
            }
        )
        return state
