from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import math
from typing import Iterable


@dataclass
class ExperienceCell:
    visits: int = 0
    danger: float = 0.0
    reward: float = 0.0
    water_entries: int = 0
    deaths: int = 0
    respawns: int = 0
    stalls: int = 0
    avoid_left_success: int = 0
    avoid_right_success: int = 0
    avoid_left_failure: int = 0
    avoid_right_failure: int = 0
    last_event: str = ""
    last_at: float = 0.0

    @property
    def score(self) -> float:
        # Positive outcomes can rehabilitate an area that was previously ambiguous,
        # but a single reward should not erase a repeated/death-confirmed hazard.
        return max(0.0, float(self.danger) - float(self.reward) * 0.55)


class JakExperienceMemory:
    """Tiny persistent online-learning table for Jak navigation.

    This is intentionally inspectable instead of pretending to be a large RL system.
    Keys may be semantic XYZ buckets (preferred) or visual scene hashes (fallback).
    Bad outcomes add danger, verified game progress adds reward, and successful learned
    escapes bias future escape direction. The JSON file survives AutoPilot restarts.
    """

    VERSION = 1

    def __init__(
        self,
        path: str | Path,
        *,
        bucket_size: float = 3.0,
        save_interval_seconds: float = 2.0,
        max_cells: int = 5000,
    ) -> None:
        self.path = Path(path)
        self.bucket_size = max(0.25, float(bucket_size))
        self.save_interval_seconds = max(0.25, float(save_interval_seconds))
        self.max_cells = max(100, int(max_cells))
        self.cells: dict[str, ExperienceCell] = {}
        self.loaded = False
        self.dirty = False
        self.last_save_at = 0.0
        self.total_events = 0
        self.total_water_events = 0
        self.total_deaths = 0
        self.total_respawns = 0
        self.total_rewards = 0
        self.total_stalls = 0
        self.last_event = ""
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
        cells = raw.get("cells", {})
        if not isinstance(cells, dict):
            return
        parsed: dict[str, ExperienceCell] = {}
        fields = ExperienceCell.__dataclass_fields__
        for key, value in cells.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            clean = {name: value[name] for name in fields if name in value}
            try:
                parsed[key] = ExperienceCell(**clean)
            except (TypeError, ValueError):
                continue
        self.cells = parsed
        stats = raw.get("stats", {}) if isinstance(raw.get("stats"), dict) else {}
        self.total_events = int(stats.get("events", 0) or 0)
        self.total_water_events = int(stats.get("water_events", 0) or 0)
        self.total_deaths = int(stats.get("deaths", 0) or 0)
        self.total_respawns = int(stats.get("respawns", 0) or 0)
        self.total_rewards = int(stats.get("rewards", 0) or 0)
        self.total_stalls = int(stats.get("stalls", 0) or 0)
        self.last_event = str(stats.get("last_event", "") or "")

    def _cell(self, key: str) -> ExperienceCell:
        cell = self.cells.get(key)
        if cell is None:
            cell = ExperienceCell()
            self.cells[key] = cell
        return cell

    def position_key(self, position: tuple[float, float, float] | None) -> str | None:
        if position is None:
            return None
        if not all(math.isfinite(float(v)) for v in position):
            return None
        size = self.bucket_size
        coords = tuple(int(math.floor(float(v) / size)) for v in position)
        return f"xyz:{coords[0]}:{coords[1]}:{coords[2]}"

    @staticmethod
    def _unique(keys: Iterable[str | None]) -> tuple[str, ...]:
        result: list[str] = []
        seen: set[str] = set()
        for key in keys:
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(str(key))
        return tuple(result)

    def touch(self, keys: Iterable[str | None], now: float) -> None:
        changed = False
        for key in self._unique(keys):
            cell = self._cell(key)
            cell.visits += 1
            cell.last_at = max(cell.last_at, float(now))
            changed = True
        if changed:
            self.dirty = True

    def score(self, keys: Iterable[str | None]) -> float:
        result = 0.0
        for key in self._unique(keys):
            cell = self.cells.get(key)
            if cell is not None:
                result = max(result, cell.score)
        return result

    def max_score(self) -> float:
        return max((cell.score for cell in self.cells.values()), default=0.0)

    def mark(
        self,
        keys: Iterable[str | None],
        *,
        now: float,
        event: str,
        danger: float = 0.0,
        reward: float = 0.0,
        water_entry: bool = False,
        death: bool = False,
        respawn: bool = False,
        stall: bool = False,
    ) -> None:
        unique = self._unique(keys)
        if not unique:
            return
        for key in unique:
            cell = self._cell(key)
            cell.danger = max(0.0, min(100.0, cell.danger + max(0.0, float(danger))))
            cell.reward = max(0.0, min(100.0, cell.reward + max(0.0, float(reward))))
            cell.water_entries += int(bool(water_entry))
            cell.deaths += int(bool(death))
            cell.respawns += int(bool(respawn))
            cell.stalls += int(bool(stall))
            cell.last_event = str(event)
            cell.last_at = float(now)
        self.total_events += 1
        self.total_water_events += int(bool(water_entry))
        self.total_deaths += int(bool(death))
        self.total_respawns += int(bool(respawn))
        self.total_rewards += int(reward > 0.0)
        self.total_stalls += int(bool(stall))
        self.last_event = str(event)
        self.dirty = True
        self._trim()

    def mark_escape_result(
        self,
        keys: Iterable[str | None],
        *,
        direction: float,
        success: bool,
        now: float,
    ) -> None:
        unique = self._unique(keys)
        if not unique:
            return
        right = direction >= 0.0
        for key in unique:
            cell = self._cell(key)
            if right and success:
                cell.avoid_right_success += 1
            elif right:
                cell.avoid_right_failure += 1
            elif success:
                cell.avoid_left_success += 1
            else:
                cell.avoid_left_failure += 1
            cell.last_event = "learned_escape_success" if success else "learned_escape_failure"
            cell.last_at = float(now)
        self.total_events += 1
        self.last_event = "learned_escape_success" if success else "learned_escape_failure"
        self.dirty = True

    def preferred_escape_direction(self, keys: Iterable[str | None]) -> float | None:
        left_score = 0.0
        right_score = 0.0
        evidence = 0
        for key in self._unique(keys):
            cell = self.cells.get(key)
            if cell is None:
                continue
            left_score += cell.avoid_left_success * 2.0 - cell.avoid_left_failure
            right_score += cell.avoid_right_success * 2.0 - cell.avoid_right_failure
            evidence += (
                cell.avoid_left_success
                + cell.avoid_right_success
                + cell.avoid_left_failure
                + cell.avoid_right_failure
            )
        if evidence <= 0 or abs(left_score - right_score) < 0.5:
            return None
        return -1.0 if left_score > right_score else 1.0

    def _trim(self) -> None:
        overflow = len(self.cells) - self.max_cells
        if overflow <= 0:
            return
        # Keep high-consequence/recent cells. Throw away stale, low-value visit noise.
        ranked = sorted(
            self.cells.items(),
            key=lambda item: (item[1].score + item[1].reward * 0.25, item[1].last_at),
        )
        for key, _cell in ranked[:overflow]:
            self.cells.pop(key, None)

    def maybe_save(self, now: float, *, force: bool = False) -> bool:
        if not self.dirty:
            return False
        if not force and float(now) - self.last_save_at < self.save_interval_seconds:
            return False
        payload = {
            "version": self.VERSION,
            "bucket_size": self.bucket_size,
            "cells": {key: asdict(value) for key, value in self.cells.items()},
            "stats": {
                "events": self.total_events,
                "water_events": self.total_water_events,
                "deaths": self.total_deaths,
                "respawns": self.total_respawns,
                "rewards": self.total_rewards,
                "stalls": self.total_stalls,
                "last_event": self.last_event,
            },
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            return False
        self.dirty = False
        self.last_save_at = float(now)
        return True

    def telemetry(self) -> dict[str, object]:
        dangerous = sum(1 for cell in self.cells.values() if cell.score >= 1.25)
        return {
            "jak_learning_memory_path": str(self.path),
            "jak_learning_memory_loaded": self.loaded,
            "jak_learning_cells": len(self.cells),
            "jak_learning_dangerous_cells": dangerous,
            "jak_learning_max_danger": round(self.max_score(), 3),
            "jak_learning_events": self.total_events,
            "jak_learning_water_events": self.total_water_events,
            "jak_learning_deaths": self.total_deaths,
            "jak_learning_respawns": self.total_respawns,
            "jak_learning_rewards": self.total_rewards,
            "jak_learning_stalls": self.total_stalls,
            "jak_learning_last_event": self.last_event,
            "jak_learning_dirty": self.dirty,
        }
