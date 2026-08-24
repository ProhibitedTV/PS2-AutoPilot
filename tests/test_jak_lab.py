import json
from pathlib import Path

from ps2_autopilot.jak_curriculum import default_manifest as default_curriculum
from ps2_autopilot.jak_lab import build_template, check_manifest, grade_trace


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _curriculum(tmp_path: Path, *, create_states: bool = True) -> Path:
    raw = default_curriculum()
    for challenge in raw["challenges"]:
        filename = f"{challenge['id']}.p2s"
        challenge["savestate_path"] = filename
        if create_states:
            (tmp_path / filename).write_bytes(b"test-state")
    path = tmp_path / "curriculum.json"
    _write_json(path, raw)
    return path


def _configured_lab(tmp_path: Path) -> Path:
    curriculum = _curriculum(tmp_path)
    raw = build_template(str(curriculum))
    raw["expected_game_ids"] = ["SCUS-TEST"]
    for slot, episode in enumerate(raw["episodes"]):
        episode["pine_slot"] = slot
        if not episode["success_rules"]:
            # Objective contracts intentionally require an explicit, calibrated
            # completion predicate. Use a generic progress event here only to test
            # the manifest/evaluator mechanism, not as a production default.
            episode["success_rules"] = [{"key": "jak_goal_progress_events", "op": "increase"}]
    path = tmp_path / "lab.json"
    _write_json(path, raw)
    return path


def _trace(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_template_derives_only_strong_self_verification_rules(tmp_path: Path):
    curriculum = _curriculum(tmp_path, create_states=False)
    raw = build_template(str(curriculum))
    episodes = {item["challenge_id"]: item for item in raw["episodes"]}

    hop = episodes["first-gap-hop"]
    assert hop["success_rules"] == [
        {"key": "jak_skill_hop_step_successes", "op": "increase"},
        {"key": "jak_atomic_skill_active", "op": "equals", "value": False},
    ]
    assert hop["failure_rules"] == [
        {"key": "jak_skill_hop_step_safety_aborts", "op": "increase"}
    ]

    water = episodes["shoreline-swim-escape"]
    assert water["success_rules"] == [
        {"key": "jak_learning_water_escape_events_v22", "op": "increase"}
    ]

    objective = episodes["blue-eco-door-run"]
    assert objective["success_rules"] == []
    assert "manual success predicate" in objective["notes"]


def test_new_template_fails_closed_until_identity_slots_and_objective_predicate_exist(tmp_path: Path):
    curriculum = _curriculum(tmp_path)
    lab = tmp_path / "lab.json"
    _write_json(lab, build_template(str(curriculum)))

    report = check_manifest(lab)
    assert report["ready"] is False
    assert report["identity_ready"] is False
    assert report["required_ready"] == 0
    assert all("exact-game-identity-not-configured" in row["blockers"] for row in report["episodes"])
    assert all("pine-slot-not-configured" in row["blockers"] for row in report["episodes"])
    blue = next(row for row in report["episodes"] if row["challenge_id"] == "blue-eco-door-run")
    assert "success-predicate-not-configured" in blue["blockers"]


def test_fully_bound_manifest_can_become_ready_without_running_emulator(tmp_path: Path):
    lab = _configured_lab(tmp_path)
    report = check_manifest(lab)
    assert report["ready"] is True
    assert report["required_ready"] == report["required_total"]
    assert report["duplicate_slots"] == []


def test_duplicate_pine_slots_are_rejected_from_readiness(tmp_path: Path):
    lab = _configured_lab(tmp_path)
    raw = json.loads(lab.read_text(encoding="utf-8"))
    raw["episodes"][1]["pine_slot"] = raw["episodes"][0]["pine_slot"]
    _write_json(lab, raw)

    report = check_manifest(lab)
    assert report["ready"] is False
    assert report["duplicate_slots"] == [0]
    affected = [row for row in report["episodes"] if row["pine_slot"] == 0]
    assert len(affected) == 2
    assert all("pine-slot-duplicated" in row["blockers"] for row in affected)


def test_grade_accepts_verified_atomic_success_within_budget(tmp_path: Path):
    lab = _configured_lab(tmp_path)
    trace = tmp_path / "verbose.jsonl"
    _trace(
        trace,
        [
            {
                "utc": "2026-08-24T20:00:00Z",
                "state": {
                    "jak_skill_hop_step_successes": 2,
                    "jak_skill_hop_step_safety_aborts": 0,
                    "jak_atomic_skill_active": False,
                },
            },
            {
                "utc": "2026-08-24T20:00:01Z",
                "state": {
                    "jak_skill_hop_step_successes": 2,
                    "jak_skill_hop_step_safety_aborts": 0,
                    "jak_atomic_skill_active": True,
                },
            },
            {
                "utc": "2026-08-24T20:00:02Z",
                "state": {
                    "jak_skill_hop_step_successes": 3,
                    "jak_skill_hop_step_safety_aborts": 0,
                    "jak_atomic_skill_active": False,
                },
            },
        ],
    )

    report = grade_trace(lab, "first-gap-hop", trace)
    assert report["passed"] is True
    assert report["duration_seconds"] == 2.0
    assert all(rule["passed"] for rule in report["success_rules"])
    assert not any(rule["passed"] for rule in report["failure_rules"])


def test_grade_fails_if_safety_abort_occurred_even_if_success_counter_increased(tmp_path: Path):
    lab = _configured_lab(tmp_path)
    trace = tmp_path / "verbose.jsonl"
    _trace(
        trace,
        [
            {
                "utc": "2026-08-24T20:00:00Z",
                "state": {
                    "jak_skill_hop_step_successes": 0,
                    "jak_skill_hop_step_safety_aborts": 0,
                    "jak_atomic_skill_active": False,
                },
            },
            {
                "utc": "2026-08-24T20:00:02Z",
                "state": {
                    "jak_skill_hop_step_successes": 1,
                    "jak_skill_hop_step_safety_aborts": 1,
                    "jak_atomic_skill_active": False,
                },
            },
        ],
    )

    report = grade_trace(lab, "first-gap-hop", trace)
    assert report["passed"] is False
    assert "failure-predicate-triggered" in report["blockers"]


def test_grade_fails_closed_when_trace_duration_is_unknown(tmp_path: Path):
    lab = _configured_lab(tmp_path)
    trace = tmp_path / "verbose.jsonl"
    _trace(
        trace,
        [
            {
                "state": {
                    "jak_skill_hop_step_successes": 0,
                    "jak_skill_hop_step_safety_aborts": 0,
                    "jak_atomic_skill_active": False,
                }
            },
            {
                "state": {
                    "jak_skill_hop_step_successes": 1,
                    "jak_skill_hop_step_safety_aborts": 0,
                    "jak_atomic_skill_active": False,
                }
            },
        ],
    )

    report = grade_trace(lab, "first-gap-hop", trace)
    assert report["passed"] is False
    assert "trace-duration-unknown" in report["blockers"]


def test_grade_enforces_episode_time_budget(tmp_path: Path):
    lab = _configured_lab(tmp_path)
    raw = json.loads(lab.read_text(encoding="utf-8"))
    raw["episodes"][0]["max_seconds"] = 1.0
    _write_json(lab, raw)
    trace = tmp_path / "verbose.jsonl"
    _trace(
        trace,
        [
            {
                "utc": "2026-08-24T20:00:00Z",
                "state": {
                    "jak_skill_hop_step_successes": 0,
                    "jak_skill_hop_step_safety_aborts": 0,
                    "jak_atomic_skill_active": False,
                },
            },
            {
                "utc": "2026-08-24T20:00:02Z",
                "state": {
                    "jak_skill_hop_step_successes": 1,
                    "jak_skill_hop_step_safety_aborts": 0,
                    "jak_atomic_skill_active": False,
                },
            },
        ],
    )

    report = grade_trace(lab, "first-gap-hop", trace)
    assert report["passed"] is False
    assert "episode-time-budget-exceeded" in report["blockers"]
