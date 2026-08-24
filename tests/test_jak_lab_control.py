import json
from pathlib import Path

import pytest

from ps2_autopilot.jak_curriculum import default_manifest as default_curriculum
from ps2_autopilot.jak_lab import build_template, check_manifest
from ps2_autopilot.jak_lab_control import reset_episode


class FakeSavestateClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def request_load_state(self, slot: int, **kwargs):
        self.calls.append({"slot": slot, **kwargs})
        return {
            "accepted": True,
            "completed": False,
            "command": "load_state",
            "slot": slot,
            "target": {"game_id": "SCUS-TEST", "emulator_status": "running"},
        }


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _manifest(tmp_path: Path, *, configure_objective: bool) -> Path:
    curriculum = default_curriculum()
    for challenge in curriculum["challenges"]:
        name = f"{challenge['id']}.p2s"
        challenge["savestate_path"] = name
        (tmp_path / name).write_bytes(b"test-state")
    curriculum_path = tmp_path / "curriculum.json"
    _write_json(curriculum_path, curriculum)

    lab = build_template(str(curriculum_path))
    lab["expected_game_ids"] = ["SCUS-TEST"]
    lab["expected_crcs"] = ["DEADBEEF"]
    for slot, episode in enumerate(lab["episodes"]):
        episode["pine_slot"] = slot
        if configure_objective and not episode["success_rules"]:
            episode["success_rules"] = [
                {"key": "jak_goal_progress_events", "op": "increase"}
            ]
    path = tmp_path / "lab.json"
    _write_json(path, lab)
    return path


def test_reset_requires_explicit_mutation_opt_in_before_touching_manifest(tmp_path: Path):
    with pytest.raises(ValueError, match="explicit --allow-savestate-control"):
        reset_episode(
            tmp_path / "does-not-need-to-exist.json",
            "first-gap-hop",
            allow_savestate_control=False,
        )


def test_ready_episode_can_reset_even_when_an_unrelated_episode_is_not_ready(tmp_path: Path):
    manifest = _manifest(tmp_path, configure_objective=False)
    readiness = check_manifest(manifest)
    assert readiness["ready"] is False
    hop = next(item for item in readiness["episodes"] if item["challenge_id"] == "first-gap-hop")
    objective = next(
        item for item in readiness["episodes"] if item["challenge_id"] == "blue-eco-door-run"
    )
    assert hop["ready"] is True
    assert objective["ready"] is False

    client = FakeSavestateClient()
    result = reset_episode(
        manifest,
        "first-gap-hop",
        allow_savestate_control=True,
        client=client,
    )

    assert result["schema"] == "jak-lab-reset-v1"
    assert result["challenge_id"] == "first-gap-hop"
    assert result["load_request"]["accepted"] is True
    assert result["load_request"]["completed"] is False
    assert client.calls == [
        {
            "slot": 0,
            "expected_game_ids": ["SCUS-TEST"],
            "expected_crcs": ["DEADBEEF"],
            "expected_title_contains": "jak and daxter",
        }
    ]


def test_unready_episode_is_rejected_before_pine_mutation(tmp_path: Path):
    manifest = _manifest(tmp_path, configure_objective=False)
    client = FakeSavestateClient()
    with pytest.raises(ValueError, match="success-predicate-not-configured"):
        reset_episode(
            manifest,
            "blue-eco-door-run",
            allow_savestate_control=True,
            client=client,
        )
    assert client.calls == []


def test_reset_returns_contract_needed_for_independent_grade(tmp_path: Path):
    manifest = _manifest(tmp_path, configure_objective=True)
    client = FakeSavestateClient()
    result = reset_episode(
        manifest,
        "blue-eco-door-run",
        allow_savestate_control=True,
        client=client,
    )
    contract = result["episode_contract"]
    assert contract["max_seconds"] == 60.0
    assert contract["success_rules"] == [
        {"key": "jak_goal_progress_events", "op": "increase"}
    ]
    assert "grade" in result["next_step"]
