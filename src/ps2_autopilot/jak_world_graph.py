from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from pathlib import Path
from typing import Iterable


@dataclass
class WorldNode:
    visits: int = 0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    danger: float = 0.0
    reward: float = 0.0
    first_at: float = 0.0
    last_at: float = 0.0
    labels: list[str] = field(default_factory=list)

    @property
    def value(self) -> float:
        # Novel nodes are useful during mapping; persistent danger should still beat
        # curiosity quickly after one or two bad outcomes.
        novelty = 1.0 / max(1.0, math.sqrt(float(self.visits)))
        return float(self.reward) * 0.65 + novelty - float(self.danger) * 1.20


@dataclass
class WorldEdge:
    src: str = ""
    dst: str = ""
    edge_type: str = "WALK"
    traversals: int = 0
    successes: int = 0
    failures: int = 0
    danger: float = 0.0
    reward: float = 0.0
    first_at: float = 0.0
    last_at: float = 0.0

    @property
    def value(self) -> float:
        reliability = (self.successes + 1.0) / (self.successes + self.failures + 2.0)
        novelty = 1.0 / max(1.0, math.sqrt(float(self.traversals)))
        return reliability + novelty + self.reward * 0.45 - self.danger * 1.10


@dataclass(frozen=True)
class GraphTransition:
    src: str
    dst: str
    edge_key: str
    edge_type: str


class JakWorldGraph:
    """Persistent sparse graph learned only from trusted Jak world coordinates.

    There are deliberately no Geyser coordinates in this module. Nodes are quantized
    from positions the live semantic bridge has already earned permission to expose,
    and edges exist only after the agent is actually observed crossing between two
    buckets. This makes the saved graph inspectable evidence rather than a disguised
    hand-authored walkthrough.
    """

    VERSION = 1

    def __init__(
        self,
        path: str | Path,
        *,
        bucket_size: float = 3.0,
        save_interval_seconds: float = 2.0,
        max_nodes: int = 2500,
        max_edges: int = 8000,
    ) -> None:
        self.path = Path(path)
        self.bucket_size = max(0.5, float(bucket_size))
        self.save_interval_seconds = max(0.25, float(save_interval_seconds))
        self.max_nodes = max(100, int(max_nodes))
        self.max_edges = max(200, int(max_edges))
        self.nodes: dict[str, WorldNode] = {}
        self.edges: dict[str, WorldEdge] = {}
        self.current_node: str | None = None
        self.previous_node: str | None = None
        self.last_edge: str | None = None
        self.last_transition_at = 0.0
        self.last_save_at = 0.0
        self.dirty = False
        self.loaded = False
        self.transitions = 0
        self.load()

    def load(self) -> None:
        self.loaded = True
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(raw, dict) or int(raw.get("version", 0)) != self.VERSION:
            return
        node_fields = WorldNode.__dataclass_fields__
        edge_fields = WorldEdge.__dataclass_fields__
        for key, value in (raw.get("nodes") or {}).items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            try:
                clean = {name: value[name] for name in node_fields if name in value}
                self.nodes[key] = WorldNode(**clean)
            except (TypeError, ValueError):
                continue
        for key, value in (raw.get("edges") or {}).items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            try:
                clean = {name: value[name] for name in edge_fields if name in value}
                self.edges[key] = WorldEdge(**clean)
            except (TypeError, ValueError):
                continue
        stats = raw.get("stats") if isinstance(raw.get("stats"), dict) else {}
        self.transitions = int(stats.get("transitions", 0) or 0)

    def node_id(self, position: tuple[float, float, float] | None) -> str | None:
        if position is None or len(position) != 3:
            return None
        try:
            coords = tuple(float(v) for v in position)
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(v) for v in coords):
            return None
        size = self.bucket_size
        bucket = tuple(int(math.floor(v / size)) for v in coords)
        return f"n:{bucket[0]}:{bucket[1]}:{bucket[2]}"

    @staticmethod
    def edge_key(src: str, dst: str, edge_type: str) -> str:
        return f"{src}>{dst}:{str(edge_type).upper()}"

    @staticmethod
    def _labels(values: Iterable[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if not text or text in result:
                continue
            result.append(text[:80])
            if len(result) >= 8:
                break
        return result

    def _upsert_node(
        self,
        node_id: str,
        position: tuple[float, float, float],
        *,
        now: float,
        danger: float,
        reward: float,
        labels: Iterable[str],
    ) -> WorldNode:
        node = self.nodes.get(node_id)
        if node is None:
            node = WorldNode(
                visits=0,
                x=float(position[0]),
                y=float(position[1]),
                z=float(position[2]),
                first_at=float(now),
                last_at=float(now),
            )
            self.nodes[node_id] = node
        old_visits = max(0, int(node.visits))
        node.visits = old_visits + 1
        weight = 1.0 / float(node.visits)
        node.x += (float(position[0]) - node.x) * weight
        node.y += (float(position[1]) - node.y) * weight
        node.z += (float(position[2]) - node.z) * weight
        node.danger = max(0.0, float(danger))
        node.reward = max(0.0, float(reward))
        node.last_at = float(now)
        if node.first_at <= 0.0:
            node.first_at = float(now)
        merged = self._labels([*node.labels, *labels])
        node.labels = merged
        return node

    def observe(
        self,
        position: tuple[float, float, float] | None,
        *,
        now: float,
        edge_type: str = "WALK",
        danger: float = 0.0,
        reward: float = 0.0,
        labels: Iterable[str] = (),
    ) -> GraphTransition | None:
        node_id = self.node_id(position)
        if node_id is None or position is None:
            return None
        self._upsert_node(
            node_id,
            position,
            now=now,
            danger=danger,
            reward=reward,
            labels=labels,
        )
        transition: GraphTransition | None = None
        if self.current_node is not None and node_id != self.current_node:
            src = self.current_node
            kind = str(edge_type or "WALK").upper()
            key = self.edge_key(src, node_id, kind)
            edge = self.edges.get(key)
            if edge is None:
                edge = WorldEdge(
                    src=src,
                    dst=node_id,
                    edge_type=kind,
                    first_at=float(now),
                )
                self.edges[key] = edge
            edge.traversals += 1
            # Crossing the bucket boundary is evidence that this edge displaced Jak.
            edge.successes += 1
            edge.danger = max(0.0, float(danger))
            edge.reward = max(0.0, float(reward))
            edge.last_at = float(now)
            self.previous_node = src
            self.last_edge = key
            self.last_transition_at = float(now)
            self.transitions += 1
            transition = GraphTransition(src, node_id, key, kind)
        self.current_node = node_id
        self.dirty = True
        self._trim()
        return transition

    def mark_current(
        self,
        *,
        now: float,
        danger: float | None = None,
        reward: float | None = None,
        label: str | None = None,
    ) -> None:
        if self.current_node is None:
            return
        node = self.nodes.get(self.current_node)
        if node is None:
            return
        if danger is not None:
            node.danger = max(0.0, float(danger))
        if reward is not None:
            node.reward = max(0.0, float(reward))
        if label:
            node.labels = self._labels([*node.labels, label])
        node.last_at = max(node.last_at, float(now))
        self.dirty = True

    def mark_last_edge(
        self,
        *,
        now: float,
        success: bool | None = None,
        danger: float = 0.0,
        reward: float = 0.0,
    ) -> None:
        if self.last_edge is None:
            return
        edge = self.edges.get(self.last_edge)
        if edge is None:
            return
        if success is False:
            edge.failures += 1
        elif success is True and edge.successes <= 0:
            edge.successes += 1
        edge.danger = max(edge.danger, max(0.0, float(danger)))
        edge.reward = max(edge.reward, max(0.0, float(reward)))
        edge.last_at = max(edge.last_at, float(now))
        self.dirty = True

    def outgoing(self, node_id: str | None = None) -> tuple[tuple[str, WorldEdge], ...]:
        source = node_id or self.current_node
        if source is None:
            return ()
        rows = [(key, edge) for key, edge in self.edges.items() if edge.src == source]
        rows.sort(key=lambda item: item[1].value, reverse=True)
        return tuple(rows)

    def best_neighbor(self, node_id: str | None = None) -> tuple[str, WorldEdge] | None:
        rows = self.outgoing(node_id)
        return rows[0] if rows else None

    def _trim(self) -> None:
        if len(self.edges) > self.max_edges:
            overflow = len(self.edges) - self.max_edges
            ranked = sorted(
                self.edges.items(),
                key=lambda item: (item[1].value, item[1].last_at),
            )
            for key, _ in ranked[:overflow]:
                self.edges.pop(key, None)
        if len(self.nodes) > self.max_nodes:
            protected = {self.current_node, self.previous_node}
            overflow = len(self.nodes) - self.max_nodes
            ranked = sorted(
                (
                    (key, node)
                    for key, node in self.nodes.items()
                    if key not in protected
                ),
                key=lambda item: (item[1].value, item[1].last_at),
            )
            for key, _ in ranked[:overflow]:
                self.nodes.pop(key, None)
            valid = set(self.nodes)
            for key in list(self.edges):
                edge = self.edges[key]
                if edge.src not in valid or edge.dst not in valid:
                    self.edges.pop(key, None)

    def maybe_save(self, now: float, *, force: bool = False) -> bool:
        if not self.dirty:
            return False
        if not force and float(now) - self.last_save_at < self.save_interval_seconds:
            return False
        payload = {
            "version": self.VERSION,
            "bucket_size": self.bucket_size,
            "nodes": {key: asdict(node) for key, node in self.nodes.items()},
            "edges": {key: asdict(edge) for key, edge in self.edges.items()},
            "stats": {"transitions": self.transitions},
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            return False
        self.last_save_at = float(now)
        self.dirty = False
        return True

    def telemetry(self) -> dict[str, object]:
        best = self.best_neighbor()
        edge_types: dict[str, int] = {}
        for edge in self.edges.values():
            edge_types[edge.edge_type] = edge_types.get(edge.edge_type, 0) + 1
        return {
            "jak_world_graph_path": str(self.path),
            "jak_world_graph_loaded": self.loaded,
            "jak_world_graph_nodes": len(self.nodes),
            "jak_world_graph_edges": len(self.edges),
            "jak_world_graph_transitions": self.transitions,
            "jak_world_graph_current_node": self.current_node,
            "jak_world_graph_previous_node": self.previous_node,
            "jak_world_graph_last_edge": self.last_edge,
            "jak_world_graph_edge_types": edge_types,
            "jak_world_graph_best_edge": None if best is None else best[0],
            "jak_world_graph_best_edge_value": None
            if best is None
            else round(best[1].value, 3),
            "jak_world_graph_dirty": self.dirty,
        }
