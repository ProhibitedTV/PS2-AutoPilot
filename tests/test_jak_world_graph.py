from ps2_autopilot.jak_world_graph import JakWorldGraph


def test_graph_learns_only_observed_nodes_and_typed_directed_edges(tmp_path):
    path = tmp_path / "graph.json"
    graph = JakWorldGraph(path, bucket_size=2.0, save_interval_seconds=0.25)

    assert graph.observe((0.2, 0.1, 0.0), now=1.0) is None
    transition = graph.observe(
        (2.3, 0.1, 0.0),
        now=2.0,
        edge_type="ROLL_JUMP",
        reward=1.0,
        labels=("objective:first_cell",),
    )

    assert transition is not None
    assert transition.edge_type == "ROLL_JUMP"
    assert len(graph.nodes) == 2
    assert len(graph.edges) == 1
    edge = graph.edges[transition.edge_key]
    assert edge.traversals == 1
    assert edge.successes == 1
    assert edge.reward == 1.0
    assert "objective:first_cell" in graph.nodes[transition.dst].labels


def test_graph_persists_nodes_edges_and_consequences(tmp_path):
    path = tmp_path / "graph.json"
    graph = JakWorldGraph(path, bucket_size=1.0, save_interval_seconds=0.25)
    graph.observe((0.1, 0.1, 0.0), now=1.0)
    transition = graph.observe((1.2, 0.1, 0.0), now=2.0, edge_type="WALK")
    assert transition is not None

    graph.mark_current(now=2.1, danger=3.0, label="outcome:water-entry")
    graph.mark_last_edge(now=2.1, success=False, danger=3.0)
    assert graph.maybe_save(2.5, force=True)

    restored = JakWorldGraph(path, bucket_size=1.0)
    assert len(restored.nodes) == 2
    assert len(restored.edges) == 1
    assert restored.nodes[transition.dst].danger == 3.0
    edge = restored.edges[transition.edge_key]
    assert edge.failures == 1
    assert edge.danger == 3.0
    assert restored.transitions == 1


def test_graph_rejects_invalid_positions_instead_of_polluting_topology(tmp_path):
    graph = JakWorldGraph(tmp_path / "graph.json")
    assert graph.observe(None, now=1.0) is None
    assert graph.observe((float("nan"), 0.0, 0.0), now=1.0) is None
    assert graph.observe((float("inf"), 0.0, 0.0), now=1.0) is None
    assert graph.nodes == {}
    assert graph.edges == {}


def test_best_neighbor_prefers_safer_more_rewarding_observed_edge(tmp_path):
    graph = JakWorldGraph(tmp_path / "graph.json", bucket_size=1.0)
    graph.observe((0.1, 0.1, 0.0), now=1.0)
    first = graph.observe((1.1, 0.1, 0.0), now=2.0, edge_type="WALK", danger=4.0)
    assert first is not None

    # Return to the source, then discover a second outgoing route with reward.
    graph.observe((0.1, 0.1, 0.0), now=3.0, edge_type="WALK")
    second = graph.observe((0.1, 1.1, 0.0), now=4.0, edge_type="GAP_JUMP", reward=2.0)
    assert second is not None

    best = graph.best_neighbor(first.src)
    assert best is not None
    assert best[0] == second.edge_key
    assert best[1].edge_type == "GAP_JUMP"
