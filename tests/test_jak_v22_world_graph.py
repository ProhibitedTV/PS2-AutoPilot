import numpy as np

from ps2_autopilot.profiles import JakAndDaxterProfile
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v22_world import JakAndDaxterV22Profile
from ps2_autopilot.profiles.registry import build_profile


def ctx(now: float) -> ProfileContext:
    return ProfileContext(
        frame=np.zeros((120, 160, 3), dtype=np.uint8),
        previous_frame=np.zeros((120, 160, 3), dtype=np.uint8),
        motion=0.01,
        template=None,
        now=now,
        semantic={},
        performance={},
    )


def profile(tmp_path, **overrides):
    cfg = {
        "ocr_enabled": False,
        "learning_enabled": True,
        "learning_memory_path": str(tmp_path / "experience.json"),
        "world_graph_path": str(tmp_path / "world.json"),
        "world_graph_bucket_size": 1.0,
        "world_graph_save_interval_seconds": 0.25,
    }
    cfg.update(overrides)
    return JakAndDaxterV22Profile(cfg)


def test_registered_profile_includes_world_graph_layer(tmp_path):
    assert JakAndDaxterProfile is JakAndDaxterV22Profile
    built = build_profile(
        {
            "name": "jak_and_daxter",
            "ocr_enabled": False,
            "learning_memory_path": str(tmp_path / "experience.json"),
            "world_graph_path": str(tmp_path / "world.json"),
        }
    )
    assert isinstance(built, JakAndDaxterV22Profile)


def test_world_graph_waits_for_validated_semantic_position(tmp_path):
    p = profile(tmp_path)
    p.learning_current_position = (1.0, 2.0, 3.0)
    p.learning_position_validated = False

    p._observe_world_graph(ctx(1.0))

    assert p.world_graph.nodes == {}
    assert p.world_graph_last_status == "waiting-semantic-position"
    assert p.world_graph_waiting_samples == 1


def test_world_graph_records_typed_skill_edge_after_position_validation(tmp_path):
    p = profile(tmp_path)
    p.learning_position_validated = True
    p.learning_current_position = (0.1, 0.1, 0.0)
    p._observe_world_graph(ctx(1.0))

    p._begin_atomic(
        "roll_jump",
        ctx(1.1),
        heading=0.0,
        forward=0.7,
        reason="test",
        payload={"min_displacement": 0.2},
    )
    p.learning_current_position = (1.2, 0.1, 0.0)
    p._observe_world_graph(ctx(1.2))

    assert len(p.world_graph.nodes) == 2
    assert len(p.world_graph.edges) == 1
    edge = next(iter(p.world_graph.edges.values()))
    assert edge.edge_type == "ROLL_JUMP"
    assert p.world_graph_transitions_seen == 1


def test_world_graph_copies_experience_danger_and_reward(tmp_path):
    p = profile(tmp_path)
    p.learning_position_validated = True
    p.learning_current_position = (0.1, 0.1, 0.0)
    key = p.experience.position_key(p.learning_current_position)
    assert key is not None
    p.experience.mark(
        [key],
        now=1.0,
        event="test-consequence",
        danger=2.5,
        reward=0.5,
    )

    p._observe_world_graph(ctx(1.1))

    node = p.world_graph.nodes[p.world_graph.current_node]
    assert node.danger > 2.0
    assert node.reward == 0.5


def test_water_consequence_marks_current_node_and_incoming_edge(tmp_path):
    p = profile(tmp_path)
    p.learning_position_validated = True
    p.learning_current_position = (0.1, 0.1, 0.0)
    p._observe_world_graph(ctx(1.0))
    p.learning_current_position = (1.2, 0.1, 0.0)
    p._observe_world_graph(ctx(2.0))

    p._record_water_entry(ctx(2.1))

    current = p.world_graph.nodes[p.world_graph.current_node]
    edge = p.world_graph.edges[p.world_graph.last_edge]
    assert current.danger >= p.learning_water_penalty
    assert "outcome:water-entry" in current.labels
    assert edge.failures >= 1
    assert edge.danger >= p.learning_water_penalty


def test_graph_telemetry_exposes_mapping_state_and_edge_types(tmp_path):
    p = profile(tmp_path)
    p.learning_position_validated = True
    p.learning_current_position = (0.1, 0.1, 0.0)
    p._observe_world_graph(ctx(1.0))
    p.learning_current_position = (1.2, 0.1, 0.0)
    p._set_graph_edge_mode("PLATFORM_CHAIN", 1.1)
    p._observe_world_graph(ctx(1.2))

    state = p.telemetry(ctx(1.3))
    assert state["jak_world_graph_status"] == "mapping"
    assert state["jak_world_graph_nodes"] == 2
    assert state["jak_world_graph_edges"] == 1
    assert state["jak_world_graph_edge_types"]["PLATFORM_CHAIN"] == 1
