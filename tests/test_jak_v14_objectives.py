import json

from ps2_autopilot.jak_benchmark import summarize
from ps2_autopilot.jak_knowledge import JakProgression
from ps2_autopilot.jak_objectives import GeyserObjective, GeyserRockPlanner, SparseWorldGraph
from ps2_autopilot.profiles.jak_and_daxter_v14 import JakAndDaxterV14Profile
from ps2_autopilot.profiles.registry import build_profile


def planner(**extra):
    cfg = {
        "geyser_baseline_power_cells": 0,
        "geyser_baseline_orbs": 0,
        "geyser_baseline_scout_flies": 0,
        "objective_replan_seconds": 12.0,
    }
    cfg.update(extra)
    return GeyserRockPlanner(cfg)


def test_geyser_curriculum_advances_from_observed_progress():
    p = planner()
    assert p.observe(JakProgression(0, 0, 0), {}, 0.0).stage == GeyserObjective.FIRST_CELL
    assert p.observe(JakProgression(1, 5, 0), {}, 1.0).stage == GeyserObjective.SCOUT_FLIES
    assert p.observe(JakProgression(1, 15, 7), {}, 2.0).stage == GeyserObjective.BLUE_ECO_DOOR
    assert p.observe(JakProgression(3, 25, 7), {}, 3.0).stage == GeyserObjective.CLIFF_CELL
    assert p.observe(JakProgression(4, 30, 7), {}, 4.0).stage == GeyserObjective.RETURN_WARP


def test_verified_pine_progress_overrides_stale_visual_counts():
    p = planner()
    semantic = {
        "pine_available": True,
        "pine_verified": True,
        "pine_stale": False,
        "power_cells": 3,
        "precursor_orbs": 19,
        "scout_flies": 7,
    }
    snap = p.observe(JakProgression(1, 3, 1), semantic, 1.0)
    assert snap.progress_source == "pine"
    assert snap.cells_delta == 3
    assert snap.flies_delta == 7
    assert snap.stage == GeyserObjective.CLIFF_CELL


def test_stalled_objective_requests_replan():
    p = planner(objective_replan_seconds=12.0)
    p.observe(JakProgression(0, 0, 0), {}, 0.0)
    snap = p.observe(JakProgression(0, 0, 0), {}, 12.5)
    assert snap.replan_due is True
    assert p.replans == 1


def test_sparse_world_graph_finds_nearest_calibrated_node():
    graph = SparseWorldGraph.from_config(
        [
            {"name": "warp", "x": 0, "y": 0, "z": 0},
            {"name": "door", "x": 10, "y": 0, "z": 0},
        ]
    )
    name, distance = graph.nearest((8.0, 0.0, 0.0))
    assert name == "door"
    assert distance == 2.0


def test_registry_promotes_jak_to_v14():
    p = build_profile({"name": "jak_and_daxter", "ocr_enabled": False})
    assert isinstance(p, JakAndDaxterV14Profile)


def test_benchmark_summarizes_objective_progress(tmp_path):
    path = tmp_path / "verbose.jsonl"
    rows = [
        {
            "timestamp": 10.0,
            "jak_policy_version": "v14",
            "jak_objective_stage": "first_cell",
            "jak_goal_completion_percent": 0,
            "jak_goal_cells_delta": 0,
            "jak_goal_orbs_delta": 0,
            "jak_goal_flies_delta": 0,
            "jak_goal_progress_events": 0,
            "action": "jak: purposeful arc",
        },
        {
            "timestamp": 20.0,
            "jak_policy_version": "v14",
            "jak_objective_stage": "scout_flies",
            "jak_goal_completion_percent": 35,
            "jak_goal_cells_delta": 1,
            "jak_goal_orbs_delta": 4,
            "jak_goal_flies_delta": 1,
            "jak_goal_progress_events": 2,
            "jak_roll_jump_attempts": 1,
            "action": "jak: Scout Fly dive complete",
        },
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    report = summarize(path)
    assert report["samples"] == 2
    assert report["duration_seconds"] == 10.0
    assert report["max_geyser_cells"] == 1
    assert report["max_geyser_orbs"] == 4
    assert report["max_completion_percent"] == 35
    assert report["objective_progress_events"] == 2
