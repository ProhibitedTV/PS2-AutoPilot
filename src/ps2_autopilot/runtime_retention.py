from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import shutil
import time


@dataclass(frozen=True)
class RetentionResult:
    removed_items: int
    removed_bytes: int
    total_bytes: int


class RuntimeRetention:
    """Bound runtime artifacts without deleting evidence from the active crash loop.

    A fresh operator-started run can clear the previous session completely. During
    the active 24/7 run we only prune historical artifacts (failure bundles,
    unknown-screen captures, and rotated logs) while leaving current JSONL/state
    files alone.
    """

    def __init__(
        self,
        root: Path,
        *,
        max_total_mb: float = 300.0,
        max_failure_bundles: int = 30,
        max_unknown_captures: int = 60,
        prune_interval_seconds: float = 60.0,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_total_bytes = max(32 * 1024 * 1024, int(float(max_total_mb) * 1024 * 1024))
        self.max_failure_bundles = max(5, int(max_failure_bundles))
        self.max_unknown_captures = max(10, int(max_unknown_captures))
        self.prune_interval_seconds = max(10.0, float(prune_interval_seconds))
        self.last_prune_at = -1e9

    @staticmethod
    def _path_size(path: Path) -> int:
        try:
            if path.is_file():
                return path.stat().st_size
            return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
        except OSError:
            return 0

    @classmethod
    def _remove_path(cls, path: Path) -> int:
        size = cls._path_size(path)
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        except OSError:
            return 0
        return size

    def total_bytes(self) -> int:
        return self._path_size(self.root)

    def clear_previous_run(self) -> RetentionResult:
        """Delete previous runtime artifacts while preserving an explicit stop flag."""

        before = self.total_bytes()
        removed_items = 0
        removed_bytes = 0
        for path in list(self.root.iterdir()):
            if path.name.upper() == "STOP24X7":
                continue
            removed_bytes += self._remove_path(path)
            removed_items += 1
        self.root.mkdir(parents=True, exist_ok=True)
        return RetentionResult(removed_items, removed_bytes, max(0, before - removed_bytes))

    def _prune_counted_dirs(self, parent: Path, limit: int) -> tuple[int, int]:
        if not parent.exists():
            return 0, 0
        items = sorted(
            (p for p in parent.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
        )
        removed_items = removed_bytes = 0
        while len(items) > limit:
            victim = items.pop(0)
            removed_bytes += self._remove_path(victim)
            removed_items += 1
        return removed_items, removed_bytes

    def _prune_unknown_count(self) -> tuple[int, int]:
        parent = self.root / "unknown"
        if not parent.exists():
            return 0, 0
        pngs = sorted(parent.glob("*.png"), key=lambda p: p.stat().st_mtime)
        removed_items = removed_bytes = 0
        while len(pngs) > self.max_unknown_captures:
            image = pngs.pop(0)
            meta = image.with_suffix(".json")
            removed_bytes += self._remove_path(image)
            if meta.exists():
                removed_bytes += self._remove_path(meta)
            removed_items += 1
        return removed_items, removed_bytes

    def _historical_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        failures = self.root / "failures"
        if failures.exists():
            candidates.extend(p for p in failures.iterdir() if p.is_dir())

        unknown = self.root / "unknown"
        if unknown.exists():
            candidates.extend(unknown.glob("*.png"))

        # JSONL writers keep at most one rotated generation. If total runtime size
        # still exceeds the hard budget, those backups are expendable before active
        # logs/state are touched.
        candidates.extend(self.root.glob("*.jsonl.1"))
        candidates.extend(self.root.glob("*.log.1"))
        return sorted(candidates, key=lambda p: p.stat().st_mtime)

    def prune(self) -> RetentionResult:
        removed_items = removed_bytes = 0

        count, size = self._prune_counted_dirs(self.root / "failures", self.max_failure_bundles)
        removed_items += count
        removed_bytes += size

        count, size = self._prune_unknown_count()
        removed_items += count
        removed_bytes += size

        total = self.total_bytes()
        if total > self.max_total_bytes:
            for victim in self._historical_candidates():
                if total <= self.max_total_bytes:
                    break
                # Unknown metadata is paired with its PNG.
                if victim.suffix.lower() == ".png" and victim.parent.name == "unknown":
                    meta = victim.with_suffix(".json")
                    size = self._remove_path(victim)
                    if meta.exists():
                        size += self._remove_path(meta)
                else:
                    size = self._remove_path(victim)
                if size:
                    removed_items += 1
                    removed_bytes += size
                    total = max(0, total - size)

        return RetentionResult(removed_items, removed_bytes, self.total_bytes())

    def maybe_prune(self, now: float | None = None) -> RetentionResult | None:
        now = time.monotonic() if now is None else float(now)
        if now - self.last_prune_at < self.prune_interval_seconds:
            return None
        self.last_prune_at = now
        return self.prune()


def _format_mb(value: int) -> str:
    return f"{value / (1024 * 1024):.1f} MB"


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean or prune PS2 AutoPilot runtime artifacts.")
    parser.add_argument("--root", default="runtime")
    parser.add_argument("--clear", action="store_true", help="clear the previous run before pruning")
    parser.add_argument("--max-total-mb", type=float, default=300.0)
    parser.add_argument("--max-failures", type=int, default=30)
    parser.add_argument("--max-unknown", type=int, default=60)
    args = parser.parse_args()

    retention = RuntimeRetention(
        Path(args.root),
        max_total_mb=args.max_total_mb,
        max_failure_bundles=args.max_failures,
        max_unknown_captures=args.max_unknown,
    )
    if args.clear:
        result = retention.clear_previous_run()
        print(
            f"[runtime] cleared {result.removed_items} previous items "
            f"({_format_mb(result.removed_bytes)})",
            flush=True,
        )
    result = retention.prune()
    print(
        f"[runtime] retention size={_format_mb(result.total_bytes)} "
        f"removed={result.removed_items}",
        flush=True,
    )


if __name__ == "__main__":
    main()
