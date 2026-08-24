from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any

from .jak_knowledge import JakProgression


class GeyserObjective(str, Enum):
    FIRST_CELL = "first_cell"
    SCOUT_FLIES = "scout_flies"
    BLUE_ECO_DOOR = "blue_eco_door"
    CLIFF_CELL = "cliff_cell"
    RETURN_WARP = "return_warp"
    COMPLETE = "complete"


@dataclass(frozen=True)
class ObjectiveSpec:
    stage: GeyserObjective
    goal: str
    subgoal: str
    preferred_cue: str
    skill: str


GEYSER_ROCK_CURRICULUM: tuple[ObjectiveSpec, ...] = (
    ObjectiveSpec(
        GeyserObjective.FIRST_CELL,
        "GEYSER ROCK · FIRST POWER CELL",
        "Follow the tutorial path and reach the first cell",
        "corridor",
        "safe_traversal",
    ),
    ObjectiveSpec(
        GeyserObjective.SCOUT_FLIES,
        "GEYSER ROCK · FREE 7 SCOUT FLIES",
        "Search stable red/gray boxes; jump then dive attack",
        "scout_box",
        "scout_dive",
    ),
    ObjectiveSpec(
        GeyserObjective.BLUE_ECO_DOOR,
        "GEYSER ROCK · OPEN PRECURSOR DOOR",
        "Acquire Blue Eco, then carry it toward the sealed door",
        "blue_eco",
        "eco_run",
    ),
    ObjectiveSpec(
        GeyserObjective.CLIFF_CELL,
        "GEYSER ROCK · CLIFF POWER CELL",
        "Seek upward/open routes and commit to platforming",
        "platform_route",
        "platforming",
    ),
    ObjectiveSpec(
        GeyserObjective.RETURN_WARP,
        "GEYSER ROCK · RETURN TO WARP GATE",
        "Backtrack toward the starting warp after all four cells",
        "warp_route",
        "route_memory",
    ),
    ObjectiveSpec(
        GeyserObjective.COMPLETE,
        "GEYSER ROCK · GRADUATED",
        "Tutorial completion verified",
        "none",
        "none",
    ),
)

_SPEC_BY_STAGE = {spec.stage: spec for spec in GEYSER_ROCK_CURRICULUM}


@dataclass
class SparseWorldNode:
    name: str
    x: float
    y: float
    z: float
    tags: tuple[str, ...] = ()
    neighbors: tuple[str, ...] = ()


@dataclass
class SparseWorldGraph:
    nodes: dict[str, SparseWorldNode] = field(default_factory=dict)

    @classmethod
    def from_config(cls, values: Any) -> "SparseWorldGraph":
        result: dict[str, SparseWorldNode] = {}
        if not isinstance(values, list):
            return cls(result)
        for raw in values:
            if not isinstance(raw, dict) or not raw.get("name"):
                continue
            try:
                node = SparseWorldNode(
                    name=str(raw["name"]),
                    x=float(raw["x"]),
                    y=float(raw["y"]),
                    z=float(raw["z"]),
                    tags=tuple(str(v) for v in raw.get("tags", [])),
                    neighbors=tuple(str(v) for v in raw.get("neighbors", [])),
                )
            except (KeyError, TypeError, ValueError):
                continue
            result[node.name] = node
        return cls(result)

    def nearest(self, position: tuple[float, float, float] | None) -> tuple[str | None, float | None]:
        if position is None or not self.nodes:
            return None, None
        x, y, z = position
        best_name: str | None = None
        best_distance = math.inf
        for node in self.nodes.values():
            d = math.sqrt((x - node.x) ** 2 + (y - node.y) ** 2 + (z - node.z) ** 2)
            if d < best_distance:
                best_name, best_distance = node.name, d
        return best_name, None if best_name is None else best_distance


@dataclass
class PlannerSnapshot:
    stage: GeyserObjective = GeyserObjective.FIRST_CELL
    goal: str = "GEYSER ROCK · FIRST POWER CELL"
    subgoal: str = "Follow the tutorial path and reach the first cell"
    preferred_cue: str = "corridor"
    skill: str = "safe_traversal"
    cells_delta: int | None = None
    orbs_delta: int | None = None
    flies_delta: int | None = None
    progress_source: str = "unknown"
    objective_age: float = 0.0
    no_progress_age: float = 0.0
    replan_due: bool = False
    nearest_node: str | None = None
    nearest_node_distance: float | None = None
    distinct_position_buckets: int = 0
    current_position_bucket: str | None = None


class GeyserRockPlanner:
    """Small deterministic tutorial curriculum and sparse route-memory layer.

    This does not pretend we already know the PS2 build's coordinates. It can work
    from OCR progress today, consume read-only PINE counts/position when configured,
    and automatically gains meaningful world-node routing once calibrated waypoints
    are added to YAML. High-level planning is therefore useful before memory hunting
    is complete rather than blocked on reverse engineering.
    """

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        cfg = dict(cfg or {})
        self.replan_seconds = max(12.0, float(cfg.get("objective_replan_seconds", 45.0)))
        self.position_bucket_size = max(0.5, float(cfg.get("position_bucket_size", 3.0)))
        self.graph = SparseWorldGraph.from_config(cfg.get("geyser_waypoints", []))

        self.baseline = JakProgression()
        self.last_counts = JakProgression()
        self.last_progress_at: float | None = None
        self.stage_started_at: float | None = None
        self.last_stage = GeyserObjective.FIRST_CELL
        self.stage_changes = 0
        self.replans = 0
        self.progress_events = 0
        self.position_buckets: set[str] = set()
        self.current_position_bucket: str | None = None
        self.current_node: str | None = None
        self.current_node_distance: float | None = None
        self.snapshot = PlannerSnapshot()

    @staticmethod
    def _nonnegative_delta(current: int | None, baseline: int | None) -> int | None:
        if current is None or baseline is None:
            return None
        return max(0, int(current) - int(baseline))

    @staticmethod
    def _semantic_int(semantic: dict[str, Any], *names: str) -> int | None:
        for name in names:
            value = semantic.get(name)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _position(semantic: dict[str, Any]) -> tuple[float, float, float] | None:
        candidates = (
            ("jak_x", "jak_y", "jak_z"),
            ("player_x", "player_y", "player_z"),
        )
        for names in candidates:
            values: list[float] = []
            for name in names:
                try:
                    values.append(float(semantic[name]))
                except (KeyError, TypeError, ValueError):
                    values = []
                    break
            if len(values) == 3 and all(math.isfinite(v) for v in values):
                return values[0], values[1], values[2]
        return None

    def _merge_counts(self, visual: JakProgression, semantic: dict[str, Any]) -> tuple[JakProgression, str]:
        semantic_trusted = bool(
            semantic.get("pine_verified")
            and semantic.get("pine_available")
            and not semantic.get("pine_stale")
        )
        if semantic_trusted:
            cells = self._semantic_int(semantic, "power_cells", "jak_power_cells")
            orbs = self._semantic_int(semantic, "precursor_orbs", "jak_precursor_orbs")
            flies = self._semantic_int(semantic, "scout_flies", "jak_scout_flies")
        else:
            cells = orbs = flies = None

        merged = JakProgression(
            power_cells=cells if cells is not None else visual.power_cells,
            precursor_orbs=orbs if orbs is not None else visual.precursor_orbs,
            scout_flies=flies if flies is not None else visual.scout_flies,
        )
        source = "pine" if semantic_trusted and any(v is not None for v in (cells, orbs, flies)) else "ocr"
        if all(v is None for v in (merged.power_cells, merged.precursor_orbs, merged.scout_flies)):
            source = "unknown"
        return merged, source

    def _initialize_baseline(self, counts: JakProgression) -> None:
        self.baseline = JakProgression(
            power_cells=counts.power_cells if self.baseline.power_cells is None else self.baseline.power_cells,
            precursor_orbs=counts.precursor_orbs if self.baseline.precursor_orbs is None else self.baseline.precursor_orbs,
            scout_flies=counts.scout_flies if self.baseline.scout_flies is None else self.baseline.scout_flies,
        )

    def _stage_for(self, cells: int | None, flies: int | None, semantic: dict[str, Any]) -> GeyserObjective:
        # An explicit calibrated semantic task flag wins when available.
        if bool(semantic.get("geyser_complete")):
            return GeyserObjective.COMPLETE
        if cells is not None and cells >= 4:
            return GeyserObjective.RETURN_WARP
        if cells is not None and cells >= 3:
            return GeyserObjective.CLIFF_CELL
        if (flies is not None and flies >= 7) or (cells is not None and cells >= 2):
            return GeyserObjective.BLUE_ECO_DOOR
        if cells is not None and cells >= 1:
            return GeyserObjective.SCOUT_FLIES
        return GeyserObjective.FIRST_CELL

    def observe(self, visual: JakProgression, semantic: dict[str, Any], now: float) -> PlannerSnapshot:
        counts, source = self._merge_counts(visual, semantic)
        self._initialize_baseline(counts)
        cells = self._nonnegative_delta(counts.power_cells, self.baseline.power_cells)
        orbs = self._nonnegative_delta(counts.precursor_orbs, self.baseline.precursor_orbs)
        flies = self._nonnegative_delta(counts.scout_flies, self.baseline.scout_flies)

        if self.stage_started_at is None:
            self.stage_started_at = now
        if self.last_progress_at is None:
            self.last_progress_at = now

        old_tuple = (
            self.last_counts.power_cells,
            self.last_counts.precursor_orbs,
            self.last_counts.scout_flies,
        )
        new_tuple = (counts.power_cells, counts.precursor_orbs, counts.scout_flies)
        if old_tuple != (None, None, None) and new_tuple != old_tuple:
            monotonic_gain = any(
                n is not None and o is not None and n > o
                for o, n in zip(old_tuple, new_tuple)
            )
            if monotonic_gain:
                self.last_progress_at = now
                self.progress_events += 1
        self.last_counts = counts

        stage = self._stage_for(cells, flies, semantic)
        if stage != self.last_stage:
            self.last_stage = stage
            self.stage_started_at = now
            self.last_progress_at = now
            self.stage_changes += 1

        position = self._position(semantic)
        if position is not None:
            size = self.position_bucket_size
            bucket = ":".join(str(int(math.floor(v / size))) for v in position)
            self.current_position_bucket = bucket
            self.position_buckets.add(bucket)
        self.current_node, self.current_node_distance = self.graph.nearest(position)

        objective_age = max(0.0, now - (self.stage_started_at or now))
        no_progress_age = max(0.0, now - (self.last_progress_at or now))
        replan_due = no_progress_age >= self.replan_seconds
        if replan_due and not self.snapshot.replan_due:
            self.replans += 1

        spec = _SPEC_BY_STAGE[stage]
        self.snapshot = PlannerSnapshot(
            stage=stage,
            goal=spec.goal,
            subgoal=spec.subgoal,
            preferred_cue=spec.preferred_cue,
            skill=spec.skill,
            cells_delta=cells,
            orbs_delta=orbs,
            flies_delta=flies,
            progress_source=source,
            objective_age=objective_age,
            no_progress_age=no_progress_age,
            replan_due=replan_due,
            nearest_node=self.current_node,
            nearest_node_distance=self.current_node_distance,
            distinct_position_buckets=len(self.position_buckets),
            current_position_bucket=self.current_position_bucket,
        )
        return self.snapshot
