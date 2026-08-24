from __future__ import annotations

from pathlib import Path

from ps2_autopilot import jak_acceptance


def test_missing_live_evidence_is_reported_as_blocked_not_passed() -> None:
    report = jak_acceptance.evaluate_acceptance()
    assert report["passed"] is False
    assert report["passed_sections"] == []
    assert set(report["remaining_sections"]) == {
        "v21_semantics",
        "v21_route",
        "v22_curriculum",
        "v23_graduation",
    }
    assert all(item["status"] == "live-evidence-missing" for item in report["blockers"])


def test_all_strict_subgates_must_pass(monkeypatch, tmp_path: Path) -> None:
    semantic = tmp_path / "semantic.jsonl"
    route = tmp_path / "route.json"
    curriculum = tmp_path / "curriculum.json"
    logs = [tmp_path / f"run-{index}.jsonl" for index in range(5)]
    for path in (semantic, route, curriculum, *logs):
        path.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(jak_acceptance, "validate_semantics", lambda *args, **kwargs: {"passed": True})
    monkeypatch.setattr(jak_acceptance, "load_route", lambda path: {"schema": "ok"})
    monkeypatch.setattr(jak_acceptance, "check_route", lambda manifest: {"ready": True})
    monkeypatch.setattr(jak_acceptance, "check_curriculum", lambda path: {"ready": True})
    monkeypatch.setattr(
        jak_acceptance,
        "evaluate_suite",
        lambda *args, **kwargs: {"graduated": True, "required_runs": 5, "passed_runs": 5},
    )

    report = jak_acceptance.evaluate_acceptance(
        semantic_trace=semantic,
        route_manifest=route,
        curriculum_manifest=curriculum,
        graduation_logs=logs,
        autonomous_asserted=True,
        fresh_boots_asserted=True,
    )
    assert report["passed"] is True
    assert len(report["passed_sections"]) == 4
    assert report["blockers"] == []


def test_one_incomplete_gate_keeps_overall_acceptance_red(monkeypatch, tmp_path: Path) -> None:
    semantic = tmp_path / "semantic.jsonl"
    route = tmp_path / "route.json"
    curriculum = tmp_path / "curriculum.json"
    graduation = tmp_path / "run.jsonl"
    for path in (semantic, route, curriculum, graduation):
        path.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(jak_acceptance, "validate_semantics", lambda *args, **kwargs: {"passed": True})
    monkeypatch.setattr(jak_acceptance, "load_route", lambda path: {"schema": "ok"})
    monkeypatch.setattr(jak_acceptance, "check_route", lambda manifest: {"ready": False})
    monkeypatch.setattr(jak_acceptance, "check_curriculum", lambda path: {"ready": True})
    monkeypatch.setattr(
        jak_acceptance,
        "evaluate_suite",
        lambda *args, **kwargs: {"graduated": True},
    )

    report = jak_acceptance.evaluate_acceptance(
        semantic_trace=semantic,
        route_manifest=route,
        curriculum_manifest=curriculum,
        graduation_logs=[graduation],
        required_runs=1,
        autonomous_asserted=True,
        fresh_boots_asserted=True,
    )
    assert report["passed"] is False
    assert report["sections"]["v21_route"]["status"] == "evidence-incomplete"
    assert "v21_route" in report["remaining_sections"]
