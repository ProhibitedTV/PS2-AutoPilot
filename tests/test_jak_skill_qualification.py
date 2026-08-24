from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from ps2_autopilot.jak_curriculum import default_manifest as default_curriculum
from ps2_autopilot.jak_lab import build_template
from ps2_autopilot.jak_skill_qualification import evaluate_qualification


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _configured_lab(tmp_path: Path) -> tuple[Path, list[str]]:
    curriculum = default_curriculum()
    challenge_ids: list[str] = []
    for challenge in curriculum["challenges"]:
        challenge_ids.append(challenge["id"])
        filename = f"{challenge['id']}.p2s"
        challenge["savestate_path"] = filename
        (tmp_path / filename).write_bytes(b"state")
    curriculum_path = tmp_path / "curriculum.json"
    _write_json(curriculum_path, curriculum)

    lab = build_template(str(curriculum_path))
    lab["expected_game_ids"] = ["SCUS-TEST"]
    for slot, episode in enumerate(lab["episodes"]):
        episode["pine_slot"] = slot
        if not episode["success_rules"]:
            episode["success_rules"] = [
                {"key": f"qualified_{episode['challenge_id']}", "op": "increase"}
            ]
    lab_path = tmp_path / "lab.json"
    _write_json(lab_path, lab)
    return lab_path, challenge_ids


def _trace(path: Path, challenge_id: str, *, passed: bool = True, seconds: int = 2) -> None:
    start = datetime(2026, 8, 24, 21, 0, tzinfo=timezone.utc)
    if challenge_id == "first-gap-hop":
        before = {
            "jak_skill_hop_step_successes": 0,
            "jak_skill_hop_step_safety_aborts": 0,
            "jak_atomic_skill_active": False,
        }
        after = {
            "jak_skill_hop_step_successes": 1 if passed else 0,
            "jak_skill_hop_step_safety_aborts": 0,
            "jak_atomic_skill_active": False,
        }
    elif challenge_id == "shoreline-swim-escape":
        before = {"jak_learning_water_escape_events_v22": 0}
        after = {"jak_learning_water_escape_events_v22": 1 if passed else 0}
    else:
        key = f"qualified_{challenge_id}"
        before = {key: 0}
        after = {key: 1 if passed else 0}
    rows = [
        {"utc": start.isoformat(), "kind": "verbose", "state": before},
        {
            "utc": (start + timedelta(seconds=seconds)).isoformat(),
            "kind": "verbose",
            "state": after,
        },
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _all_trials(tmp_path: Path, challenge_ids: list[str], *, count: int = 3):
    trials: list[tuple[str, Path]] = []
    for challenge_id in challenge_ids:
        for index in range(count):
            path = tmp_path / f"{challenge_id}-{index}.jsonl"
            _trace(path, challenge_id)
            trials.append((challenge_id, path))
    return trials


def test_three_of_three_independent_runs_per_required_skill_qualifies(tmp_path: Path):
    manifest, challenge_ids = _configured_lab(tmp_path)
    report = evaluate_qualification(
        manifest,
        _all_trials(tmp_path, challenge_ids),
        required_runs=3,
    )
    assert report["qualified"] is True
    assert report["suite_integrity"] is True
    assert report["qualified_skills"] == report["required_skills"] == len(challenge_ids)
    assert all(row["supplied_runs"] == 3 for row in report["skills"])
    assert all(row["passing_runs"] == 3 for row in report["skills"])


def test_one_failed_run_keeps_skill_and_suite_red(tmp_path: Path):
    manifest, challenge_ids = _configured_lab(tmp_path)
    trials = _all_trials(tmp_path, challenge_ids)
    failed_path = tmp_path / "first-gap-hop-1.jsonl"
    _trace(failed_path, "first-gap-hop", passed=False)

    report = evaluate_qualification(manifest, trials, required_runs=3)
    hop = next(row for row in report["skills"] if row["challenge_id"] == "first-gap-hop")
    assert hop["qualified"] is False
    assert hop["passing_runs"] == 2
    assert "one-or-more-runs-failed" in hop["blockers"]
    assert report["qualified"] is False


def test_insufficient_runs_do_not_qualify_even_when_all_pass(tmp_path: Path):
    manifest, challenge_ids = _configured_lab(tmp_path)
    report = evaluate_qualification(
        manifest,
        _all_trials(tmp_path, challenge_ids, count=2),
        required_runs=3,
    )
    assert report["qualified"] is False
    assert all("insufficient-independent-runs" in row["blockers"] for row in report["skills"])


def test_same_trace_cannot_be_reused_to_satisfy_repeatability(tmp_path: Path):
    manifest, challenge_ids = _configured_lab(tmp_path)
    trials = _all_trials(tmp_path, challenge_ids)
    duplicate = tmp_path / "first-gap-hop-0.jsonl"
    trials.append(("first-gap-hop", duplicate))

    report = evaluate_qualification(manifest, trials, required_runs=3)
    assert report["qualified"] is False
    assert report["suite_integrity"] is False
    assert len(report["duplicate_traces"]) == 1


def test_current_manifest_readiness_is_rechecked_before_qualification(tmp_path: Path):
    manifest, challenge_ids = _configured_lab(tmp_path)
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    hop = next(item for item in raw["episodes"] if item["challenge_id"] == "first-gap-hop")
    hop["pine_slot"] = None
    _write_json(manifest, raw)

    report = evaluate_qualification(
        manifest,
        _all_trials(tmp_path, challenge_ids),
        required_runs=3,
    )
    hop_report = next(row for row in report["skills"] if row["challenge_id"] == "first-gap-hop")
    assert hop_report["qualified"] is False
    assert "episode-not-ready:pine-slot-not-configured" in hop_report["blockers"]
    assert report["qualified"] is False
