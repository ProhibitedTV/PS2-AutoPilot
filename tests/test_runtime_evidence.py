from __future__ import annotations

import pytest

from ps2_autopilot.runtime_evidence import (
    EvidenceSchemaError,
    SOAK_REPORT_SCHEMA,
    overall_metrics,
    validate_soak_report,
)


def valid_report() -> dict:
    return {
        "schema": SOAK_REPORT_SCHEMA,
        "session_count": 1,
        "game_count": 1,
        "overall": {
            "duration_seconds": 3600.0,
            "games_started": 2,
            "games_completed": 2,
            "unresolved_navigation_pct": 0.25,
        },
        "games": {},
        "sessions": [],
    }


def test_validate_soak_report_accepts_public_contract():
    report = valid_report()
    validate_soak_report(report)

    metrics = overall_metrics(report)
    assert metrics["duration_seconds"] == 3600.0
    assert metrics["games_completed"] == 2


def test_overall_metrics_returns_defensive_copy():
    report = valid_report()
    metrics = overall_metrics(report)
    metrics["games_completed"] = 99

    assert report["overall"]["games_completed"] == 2


def test_wrong_schema_fails_loudly_instead_of_becoming_zero_metrics():
    report = valid_report()
    report["schema"] = "future-soak-schema-v2"

    with pytest.raises(EvidenceSchemaError, match="unsupported soak report schema"):
        overall_metrics(report)


def test_renamed_aggregate_block_fails_loudly():
    report = valid_report()
    report["aggregate"] = report.pop("overall")

    with pytest.raises(EvidenceSchemaError, match="missing the public 'overall'"):
        overall_metrics(report)


def test_missing_required_overall_metric_fails_loudly():
    report = valid_report()
    del report["overall"]["duration_seconds"]

    with pytest.raises(EvidenceSchemaError, match="duration_seconds"):
        validate_soak_report(report)


def test_non_mapping_report_fails_loudly():
    with pytest.raises(EvidenceSchemaError, match="must be a mapping"):
        validate_soak_report([])  # type: ignore[arg-type]
