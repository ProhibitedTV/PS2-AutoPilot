from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

# `soak_report` predates the acceptance tooling and still owns the canonical
# implementations. Keep its underscore helpers behind this module so new consumers
# have one public compatibility boundary instead of importing private names directly.
from .soak_report import (
    _read_log_series as _soak_read_log_series,
    _unresolved_reason as _soak_unresolved_reason,
    build_report as _build_soak_report,
    discover_sessions as _discover_soak_sessions,
)


class EvidenceSchemaError(ValueError):
    """Raised when retained evidence does not match the supported report contract."""


SOAK_REPORT_SCHEMA = "ps2-autopilot-soak-report-v1"


def read_log_series(root: str | Path, name: str) -> list[dict[str, Any]]:
    """Read a retained JSONL series, including the supported rotated generation."""

    return _soak_read_log_series(Path(root), str(name))


def unresolved_reason(state: dict[str, Any]) -> str | None:
    """Return the canonical unresolved-navigation reason for one telemetry state."""

    return _soak_unresolved_reason(state)


def discover_sessions(paths: Iterable[str | Path]) -> list[Path]:
    """Discover runtime session roots using the shared soak-report semantics."""

    return _discover_soak_sessions(paths)


def build_soak_report(
    paths: Iterable[str | Path],
    *,
    max_gap_seconds: float = 5.0,
) -> dict[str, Any]:
    """Build the canonical cross-session soak report."""

    report = _build_soak_report(paths, max_gap_seconds=max_gap_seconds)
    validate_soak_report(report)
    return report


def validate_soak_report(report: dict[str, Any]) -> None:
    """Validate the minimum public soak-report structure used by acceptance tools.

    Consumers should fail loudly on schema drift rather than silently translating a
    missing aggregate block into zero duration/games, which can make valid evidence
    look like a failed run for the wrong reason.
    """

    if not isinstance(report, dict):
        raise EvidenceSchemaError("soak report must be a mapping")

    schema = report.get("schema")
    if schema != SOAK_REPORT_SCHEMA:
        raise EvidenceSchemaError(
            f"unsupported soak report schema: {schema!r}; expected {SOAK_REPORT_SCHEMA!r}"
        )

    overall = report.get("overall")
    if not isinstance(overall, dict):
        raise EvidenceSchemaError("soak report is missing the public 'overall' metrics block")

    required = {
        "duration_seconds",
        "games_started",
        "games_completed",
        "unresolved_navigation_pct",
    }
    missing = sorted(required.difference(overall))
    if missing:
        raise EvidenceSchemaError(
            "soak report overall metrics missing required fields: " + ", ".join(missing)
        )


def overall_metrics(report: dict[str, Any]) -> dict[str, Any]:
    """Return a defensive copy of validated aggregate soak metrics."""

    validate_soak_report(report)
    return dict(report["overall"])
