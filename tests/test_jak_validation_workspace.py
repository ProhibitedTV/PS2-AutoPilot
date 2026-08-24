from __future__ import annotations

import json
from pathlib import Path

import pytest

from ps2_autopilot.jak_validation_workspace import (
    ValidationWorkspaceError,
    init_workspace,
    status_workspace,
)


def test_init_creates_complete_but_intentionally_red_evidence_workspace(tmp_path: Path) -> None:
    root = tmp_path / "jak-validation"
    report = init_workspace(root)

    assert report["initialized"] is True
    assert report["ready"] is False
    assert (root / "validation.json").is_file()
    assert (root / "route.json").is_file()
    assert (root / "curriculum.json").is_file()
    assert (root / "captures.json").is_file()
    assert (root / "README.md").is_file()
    assert (root / "semantic").is_dir()
    assert (root / "graduation").is_dir()
    assert (root / "savestates").is_dir()
    assert (root / "capture-evidence").is_dir()

    route = json.loads((root / "route.json").read_text(encoding="utf-8"))
    assert all(node["xyz"] is None for node in route["nodes"].values())
    curriculum = json.loads((root / "curriculum.json").read_text(encoding="utf-8"))
    assert all(not challenge["savestate_path"] for challenge in curriculum["challenges"])
    captures = json.loads((root / "captures.json").read_text(encoding="utf-8"))
    assert all(not scenario["evidence"] for scenario in captures["scenarios"])
    assert all(scenario["reviewed"] is False for scenario in captures["scenarios"])


def test_init_refuses_to_overwrite_evidence_without_force(tmp_path: Path) -> None:
    root = tmp_path / "jak-validation"
    init_workspace(root)
    with pytest.raises(ValidationWorkspaceError, match="refusing to overwrite"):
        init_workspace(root)


def test_status_after_init_reports_finite_live_blocker_list(tmp_path: Path) -> None:
    root = tmp_path / "jak-validation"
    init_workspace(root)
    report = status_workspace(root)

    assert report["ready"] is False
    assert report["graduation_logs_found"] == 0
    assert report["graduation_autonomous_asserted"] is False
    assert report["graduation_fresh_boots_asserted"] is False
    assert report["acceptance"]["passed"] is False
    assert report["captures"]["passed"] is False
    assert report["route_summary"]["ready"] is False
    assert report["curriculum_summary"]["ready"] is False
    assert report["next_actions"] == [
        "capture/validate semantic ground-air-ground telemetry",
        "calibrate and validate the Geyser route manifest",
        "capture all required deterministic curriculum savestates",
        "collect and review every required visual capture scenario",
        "record 5 independent fresh autonomous Geyser graduation runs",
    ]


def test_workspace_config_carries_explicit_graduation_assertions(tmp_path: Path) -> None:
    root = tmp_path / "jak-validation"
    init_workspace(root)
    config = json.loads((root / "validation.json").read_text(encoding="utf-8"))

    assert config["graduation_autonomous_asserted"] is False
    assert config["graduation_fresh_boots_asserted"] is False


def test_status_rejects_non_boolean_graduation_assertions(tmp_path: Path) -> None:
    root = tmp_path / "jak-validation"
    init_workspace(root)
    config_path = root / "validation.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["graduation_autonomous_asserted"] = "yes"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ValidationWorkspaceError, match="must be true or false"):
        status_workspace(root)


def test_status_propagates_explicit_graduation_assertions(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "jak-validation"
    init_workspace(root)
    config_path = root / "validation.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["graduation_autonomous_asserted"] = True
    config["graduation_fresh_boots_asserted"] = True
    config_path.write_text(json.dumps(config), encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_acceptance(**kwargs):
        captured.update(kwargs)
        return {
            "passed": False,
            "sections": {
                "v21_semantics": {"passed": False},
                "v21_route": {"passed": False},
                "v22_curriculum": {"passed": False},
                "v23_graduation": {"passed": False},
            },
        }

    monkeypatch.setattr(
        "ps2_autopilot.jak_validation_workspace.evaluate_acceptance",
        fake_acceptance,
    )
    status_workspace(root)

    assert captured["autonomous_asserted"] is True
    assert captured["fresh_boots_asserted"] is True
