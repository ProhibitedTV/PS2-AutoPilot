from __future__ import annotations

from pathlib import Path

import pytest

from ps2_autopilot.jak_capture_suite import (
    DEFAULT_SCENARIOS,
    CaptureSuiteError,
    add_evidence,
    check_manifest,
    default_manifest,
    review_scenario,
    save_manifest,
)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fixture")
    return path


def test_default_manifest_covers_required_jak_acceptance_capture_classes(tmp_path: Path) -> None:
    manifest_path = tmp_path / "jak-captures.json"
    save_manifest(manifest_path, default_manifest())

    report = check_manifest(manifest_path)
    expected = {item["id"] for item in DEFAULT_SCENARIOS}
    assert {row["id"] for row in report["scenarios"]} == expected
    assert report["ready"] is False
    assert report["required_total"] == len(DEFAULT_SCENARIOS)
    assert report["required_ready"] == 0
    assert report["next_missing"] == DEFAULT_SCENARIOS[0]["id"]


def test_files_alone_do_not_make_a_scenario_ready(tmp_path: Path) -> None:
    manifest = default_manifest()
    scenario_id = "pause_menu"
    first = _touch(tmp_path / "pause_1.png")
    second = _touch(tmp_path / "pause_2.png")
    manifest = add_evidence(manifest, scenario_id, [first.name, second.name])
    manifest_path = tmp_path / "jak-captures.json"
    save_manifest(manifest_path, manifest)

    row = next(item for item in check_manifest(manifest_path)["scenarios"] if item["id"] == scenario_id)
    assert row["enough_samples"] is True
    assert row["reviewed"] is False
    assert row["ready"] is False


def test_explicit_review_plus_enough_existing_evidence_makes_scenario_ready(tmp_path: Path) -> None:
    manifest = default_manifest()
    scenario_id = "pause_menu"
    for index in range(2):
        _touch(tmp_path / f"pause_{index}.png")
    manifest = add_evidence(manifest, scenario_id, ["pause_0.png", "pause_1.png"])
    manifest = review_scenario(
        manifest,
        scenario_id,
        approved=True,
        note="Confirmed pause menu boundary against controllable gameplay.",
    )
    manifest_path = tmp_path / "jak-captures.json"
    save_manifest(manifest_path, manifest)

    row = next(item for item in check_manifest(manifest_path)["scenarios"] if item["id"] == scenario_id)
    assert row["enough_samples"] is True
    assert row["reviewed"] is True
    assert row["ready"] is True


def test_adding_new_evidence_invalidates_prior_review() -> None:
    manifest = review_scenario(
        default_manifest(),
        "pause_menu",
        approved=True,
        note="Reviewed initial set.",
    )
    assert next(item for item in manifest["scenarios"] if item["id"] == "pause_menu")["reviewed"] is True

    manifest = add_evidence(manifest, "pause_menu", ["new_capture.png"])
    scenario = next(item for item in manifest["scenarios"] if item["id"] == "pause_menu")
    assert scenario["reviewed"] is False
    assert scenario["review_note"] is None


def test_review_requires_a_note() -> None:
    with pytest.raises(CaptureSuiteError, match="review note"):
        review_scenario(default_manifest(), "pause_menu", approved=True, note="   ")


def test_unsupported_or_missing_files_do_not_count_as_usable(tmp_path: Path) -> None:
    manifest = default_manifest()
    _touch(tmp_path / "pause.txt")
    manifest = add_evidence(manifest, "pause_menu", ["pause.txt", "missing.png"])
    manifest = review_scenario(
        manifest,
        "pause_menu",
        approved=True,
        note="Intentional fixture for evidence validation.",
    )
    manifest_path = tmp_path / "jak-captures.json"
    save_manifest(manifest_path, manifest)

    row = next(item for item in check_manifest(manifest_path)["scenarios"] if item["id"] == "pause_menu")
    assert row["usable_samples"] == 0
    assert row["enough_samples"] is False
    assert row["ready"] is False


def test_full_suite_only_passes_when_every_required_scenario_is_ready(tmp_path: Path) -> None:
    manifest = default_manifest()
    for scenario in manifest["scenarios"]:
        scenario["min_samples"] = 1
        filename = f"{scenario['id']}.png"
        _touch(tmp_path / filename)
        scenario["evidence"] = [filename]
        scenario["reviewed"] = True
        scenario["review_note"] = "Reviewed representative capture."

    manifest_path = tmp_path / "jak-captures.json"
    save_manifest(manifest_path, manifest)
    report = check_manifest(manifest_path)
    assert report["ready"] is True
    assert report["required_ready"] == report["required_total"]
    assert report["missing_required"] == 0
    assert report["next_missing"] is None

    manifest["scenarios"][0]["reviewed"] = False
    save_manifest(manifest_path, manifest)
    report = check_manifest(manifest_path)
    assert report["ready"] is False
    assert report["next_missing"] == manifest["scenarios"][0]["id"]


def test_missing_or_extra_scenarios_keep_suite_red(tmp_path: Path) -> None:
    manifest = default_manifest()
    removed = manifest["scenarios"].pop()
    manifest["scenarios"].append(
        {
            "id": "invented_state",
            "group": "test",
            "description": "not part of the acceptance suite",
            "min_samples": 1,
            "required": False,
            "evidence": [],
            "reviewed": False,
            "review_note": None,
        }
    )
    manifest_path = tmp_path / "jak-captures.json"
    save_manifest(manifest_path, manifest)
    report = check_manifest(manifest_path)
    assert report["ready"] is False
    assert removed["id"] in report["missing_scenarios"]
    assert "invented_state" in report["extra_scenarios"]
