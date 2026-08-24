from __future__ import annotations

import json
from pathlib import Path

import pytest

from ps2_autopilot.madden_policy_compare import (
    PolicyReportError,
    compare_reports,
    main,
)


def _report(*, candidate: bool = False) -> dict:
    return {
        "schema": "madden-policy-quality-v1",
        "policy_versions": ["v24"],
        "live_samples": 2000 if candidate else 1000,
        "football": {
            "play_completion_pct": 92.0 if candidate else 80.0,
            "scoring_events": 4 if candidate else 2,
            "turnover_events": 2 if candidate else 1,
        },
        "live_quality": {
            "unknown_possession_pct": 4.0 if candidate else 10.0,
            "spatial_available_pct": 90.0 if candidate else 70.0,
            "controlled_confidence_ge_050_pct": 80.0 if candidate else 60.0,
            "target_confidence_ge_050_pct": 82.0 if candidate else 62.0,
            "ball_confidence_ge_050_pct": 55.0 if candidate else 40.0,
            "open_space_confidence_ge_050_pct": 75.0 if candidate else 65.0,
        },
        "defense": {
            "contact_authorized_pct_of_classified_ticks": 44.0 if candidate else 40.0,
        },
        "special_teams": {
            "unknown_kicking_ticks": 4 if candidate else 5,
            "scoring_ambiguities": 2 if candidate else 2,
        },
        "runtime": {
            "duration_seconds": 7200.0 if candidate else 3600.0,
            "games_completed": 2 if candidate else 1,
            "hard_recoveries": 2 if candidate else 2,
            "semantic_recoveries": 2 if candidate else 4,
            "unknown_captures": 0 if candidate else 2,
            "failure_bundles": 2 if candidate else 2,
            "unresolved_navigation_pct": 0.5 if candidate else 2.0,
        },
    }


def _metric(report: dict, name: str) -> dict:
    return next(item for item in report["metrics"] if item["metric"] == name)


def test_directional_comparison_normalizes_rates_and_does_not_score_overall():
    result = compare_reports(_report(), _report(candidate=True))

    assert result["summary"]["overall_verdict"] == "not-scored"
    assert _metric(result, "play_completion_pct")["movement"] == "improved"
    assert _metric(result, "unknown_possession_pct")["movement"] == "improved"
    assert _metric(result, "spatial_available_pct")["movement"] == "improved"
    assert _metric(result, "unresolved_navigation_pct")["movement"] == "improved"

    # 2 hard recoveries in one hour -> 1 hard recovery/hour over two hours.
    hard = _metric(result, "hard_recoveries_per_hour")
    assert hard["baseline"] == 2.0
    assert hard["candidate"] == 1.0
    assert hard["movement"] == "improved"

    semantic = _metric(result, "semantic_recoveries_per_hour")
    assert semantic["baseline"] == 4.0
    assert semantic["candidate"] == 1.0
    assert semantic["movement"] == "improved"

    # Scoring remains diagnostic even though it increased.
    scoring = _metric(result, "scoring_events_per_completed_game")
    assert scoring["preference"] == "diagnostic"
    assert scoring["movement"] == "unchanged"  # 2/game in both runs.


def test_special_team_unknown_ticks_are_normalized_by_live_samples():
    result = compare_reports(_report(), _report(candidate=True))
    metric = _metric(result, "unknown_kicking_ticks_per_1000_live_samples")
    assert metric["baseline"] == 5.0
    assert metric["candidate"] == 2.0
    assert metric["movement"] == "improved"


def test_tolerance_can_treat_small_directional_change_as_unchanged():
    before = _report()
    after = _report()
    after["live_quality"]["unknown_possession_pct"] = 9.8
    result = compare_reports(before, after, tolerance=0.25)
    assert _metric(result, "unknown_possession_pct")["movement"] == "unchanged"


def test_missing_denominator_is_reported_not_comparable_instead_of_dividing_by_zero():
    before = _report()
    after = _report(candidate=True)
    before["runtime"]["duration_seconds"] = 0
    before["runtime"]["games_completed"] = 0
    before["live_samples"] = 0
    result = compare_reports(before, after)
    assert _metric(result, "hard_recoveries_per_hour")["movement"] == "not-comparable"
    assert _metric(result, "scoring_events_per_completed_game")["movement"] == "not-comparable"
    assert _metric(result, "unknown_kicking_ticks_per_1000_live_samples")["movement"] == "not-comparable"


def test_wrong_schema_and_missing_metrics_fail_loudly(tmp_path: Path, capsys):
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    baseline.write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
    candidate.write_text(json.dumps(_report()), encoding="utf-8")
    with pytest.raises(SystemExit):
        main([str(baseline), str(candidate)])
    assert "unsupported schema" in capsys.readouterr().err

    broken = _report()
    broken["runtime"].pop("unresolved_navigation_pct")
    with pytest.raises(PolicyReportError, match="unresolved_navigation_pct"):
        compare_reports(_report(), broken)
