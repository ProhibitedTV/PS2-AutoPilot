from __future__ import annotations

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.jak_knowledge import JakProgression
from ps2_autopilot.jak_objectives import GeyserObjective, GeyserRockPlanner, PlannerSnapshot
from ps2_autopilot.jak_perception import merge_progress

from .base import ProfileContext
from .jak_and_daxter_v10 import GameplayCue
from .jak_and_daxter_v13 import JakAndDaxterV13Profile


class JakAndDaxterV14Profile(JakAndDaxterV13Profile):
    """Add a real tutorial curriculum above V13's reflex/navigation layers.

    Earlier versions mostly answered "what should I do this frame?". V14 adds a
    slower objective layer that answers "what am I trying to accomplish?". Geyser
    Rock is treated as the first acceptance curriculum: first Power Cell, seven Scout
    Flies, Blue Eco door, cliff/platforming Cell, then return to the warp gate.

    Progress can come from the existing R2/OCR path or from optional, identity-gated
    read-only PINE telemetry. The planner filters visual attractions by current goal,
    forces deliberate route rescans when objective progress stalls, and publishes
    goal/subgoal/progress telemetry suitable for a livestream overlay.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.objective_planner = GeyserRockPlanner(cfg)
        self.objective = PlannerSnapshot()
        self.objective_replan_scan_seconds = max(
            3.0, float(cfg.get("objective_replan_scan_seconds", 7.0))
        )
        self.next_objective_replan_scan_at = 0.0
        self.objective_stage_changes_seen = 0
        self.objective_verified_first_cell = 0
        self.objective_verified_scout_goal = 0
        self.objective_verified_eco_door = 0
        self.objective_verified_cliff_cell = 0
        self.objective_cue_suppressions = 0
        self.semantic_progress_updates = 0
        self.last_objective_stage = GeyserObjective.FIRST_CELL

    @staticmethod
    def _semantic_progress(ctx: ProfileContext) -> JakProgression:
        semantic = ctx.semantic
        trusted = bool(
            semantic.get("pine_verified")
            and semantic.get("pine_available")
            and not semantic.get("pine_stale")
        )
        if not trusted:
            return JakProgression()

        def read_int(*names: str) -> int | None:
            for name in names:
                value = semantic.get(name)
                if value is None:
                    continue
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue
            return None

        return JakProgression(
            power_cells=read_int("power_cells", "jak_power_cells"),
            precursor_orbs=read_int("precursor_orbs", "jak_precursor_orbs"),
            scout_flies=read_int("scout_flies", "jak_scout_flies"),
        )

    def _record_objective_transition(self, old: GeyserObjective, new: GeyserObjective) -> None:
        if old == new:
            return
        self.objective_stage_changes_seen += 1
        if old == GeyserObjective.FIRST_CELL:
            self.objective_verified_first_cell += 1
        elif old == GeyserObjective.SCOUT_FLIES:
            self.objective_verified_scout_goal += 1
        elif old == GeyserObjective.BLUE_ECO_DOOR:
            self.objective_verified_eco_door += 1
        elif old == GeyserObjective.CLIFF_CELL:
            self.objective_verified_cliff_cell += 1

    def _semantic_refresh(self, ctx: ProfileContext) -> None:
        # Retain the existing screenshot/OCR semantic pass first.
        super()._semantic_refresh(ctx)

        semantic_progress = self._semantic_progress(ctx)
        merged = merge_progress(self.progress, semantic_progress)
        if merged != self.progress:
            self.progress = merged
            self.progress_updates += 1
            self.semantic_progress_updates += 1

        old_stage = self.objective.stage
        self.objective = self.objective_planner.observe(self.progress, ctx.semantic, ctx.now)
        self._record_objective_transition(old_stage, self.objective.stage)
        self.last_objective_stage = self.objective.stage

    def _refresh_gameplay_cue(self, ctx: ProfileContext) -> None:
        super()._refresh_gameplay_cue(ctx)
        cue = self.gameplay_cue
        stage = self.objective.stage

        # Goal-filter the permissive V10 detectors. A collectible-looking patch should
        # not derail the current task merely because it is visually interesting.
        allowed = {"none"}
        if stage == GeyserObjective.SCOUT_FLIES:
            allowed.add("scout_box")
        elif stage == GeyserObjective.BLUE_ECO_DOOR:
            allowed.add("blue_eco")

        if cue.kind not in allowed:
            self.objective_cue_suppressions += 1
            self.gameplay_cue = GameplayCue()

    def _objective_replan_due(self, ctx: ProfileContext) -> bool:
        return bool(
            self.objective.replan_due
            and ctx.now >= self.next_objective_replan_scan_at
            and not self.water_escape_active
            and not self.local_stuck_active
            and not self.skill_active
            and not self.land_scan_active
        )

    def _on_foot(self, controller: Controller, ctx: ProfileContext) -> str:
        # Safety remains highest priority. Refresh before considering a planner-forced
        # scan so a real ocean/wall can preempt high-level intent immediately.
        if not self.land_scan_active:
            self._refresh_water_state(ctx)
            self._refresh_local_stuck(ctx)
            if self.water_escape_active or self.local_stuck_active or self.skill_active:
                return super()._on_foot(controller, ctx)

        if self._objective_replan_due(ctx):
            self.next_objective_replan_scan_at = ctx.now + self.objective_replan_scan_seconds
            return self._start_land_scan(
                controller,
                ctx,
                reason=f"objective-stall:{self.objective.stage.value}",
            )

        action = super()._on_foot(controller, ctx)

        # Cliff curriculum: prefer real traversal skills over combat flair. We don't
        # invent a ledge from one frame; instead we make an already-safe, dry straight
        # run eligible for the existing verified roll-jump transaction sooner.
        if (
            self.objective.stage == GeyserObjective.CLIFF_CELL
            and not self.water_escape_active
            and not self.water_geometry_confirmed
            and self.gameplay_cue.kind == "none"
        ):
            self.next_roll_jump_at = min(self.next_roll_jump_at, ctx.now + 1.0)
        return action

    def _completion_percent(self) -> int:
        cells = self.objective.cells_delta
        flies = self.objective.flies_delta
        if cells is None and flies is None:
            return 0
        score = 0.0
        if cells is not None:
            # First, Scout reward, Eco door and cliff cell each represent a major
            # tutorial competency. Clamp because global telemetry can outlive Geyser.
            score += min(max(cells, 0), 4) * 20.0
        if flies is not None:
            score += min(max(flies, 0), 7) / 7.0 * 15.0
        if self.objective.stage in {GeyserObjective.RETURN_WARP, GeyserObjective.COMPLETE}:
            score = max(score, 95.0)
        if self.objective.stage == GeyserObjective.COMPLETE:
            score = 100.0
        return int(round(min(100.0, score)))

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        position = self.objective_planner.position(ctx.semantic)
        state.update(
            {
                "jak_policy_version": "v14",
                "jak_area": "geyser_rock",
                "jak_objective_stage": self.objective.stage.value,
                "jak_goal": self.objective.goal,
                "jak_subgoal": self.objective.subgoal,
                "jak_goal_skill": self.objective.skill,
                "jak_goal_preferred_cue": self.objective.preferred_cue,
                "jak_goal_progress_source": self.objective.progress_source,
                "jak_goal_cells_delta": self.objective.cells_delta,
                "jak_goal_orbs_delta": self.objective.orbs_delta,
                "jak_goal_flies_delta": self.objective.flies_delta,
                "jak_goal_completion_percent": self._completion_percent(),
                "jak_goal_age": round(self.objective.objective_age, 2),
                "jak_goal_no_progress_age": round(self.objective.no_progress_age, 2),
                "jak_goal_replan_due": self.objective.replan_due,
                "jak_goal_replans": self.objective_planner.replans,
                "jak_goal_progress_events": self.objective_planner.progress_events,
                "jak_goal_stage_changes": self.objective_planner.stage_changes,
                "jak_goal_nearest_node": self.objective.nearest_node,
                "jak_goal_nearest_node_distance": None
                if self.objective.nearest_node_distance is None
                else round(self.objective.nearest_node_distance, 2),
                "jak_position_bucket": self.objective.current_position_bucket,
                "jak_distinct_position_buckets": self.objective.distinct_position_buckets,
                "jak_position": None
                if position is None
                else [round(position[0], 3), round(position[1], 3), round(position[2], 3)],
                "jak_semantic_progress_updates": self.semantic_progress_updates,
                "jak_objective_cue_suppressions": self.objective_cue_suppressions,
                "jak_verified_first_cell": self.objective_verified_first_cell,
                "jak_verified_scout_goal": self.objective_verified_scout_goal,
                "jak_verified_eco_door": self.objective_verified_eco_door,
                "jak_verified_cliff_cell": self.objective_verified_cliff_cell,
                "jak_stream_intent": f"{self.objective.goal} · {self.objective.subgoal}",
            }
        )
        return state
