import json

from ps2_autopilot.overlay import OverlayServer


def test_overlay_state_writes_are_rate_limited(tmp_path):
    root = tmp_path / "overlay"
    runtime = tmp_path / "runtime"
    root.mkdir()
    server = OverlayServer("127.0.0.1", 0, root, runtime, state_hz=2.0)

    assert server.write_state({"status": "one"})
    assert not server.write_state({"status": "two"})
    assert json.loads((runtime / "state.json").read_text()) == {"status": "one"}


def test_overlay_force_write_bypasses_rate_limit(tmp_path):
    root = tmp_path / "overlay"
    runtime = tmp_path / "runtime"
    root.mkdir()
    server = OverlayServer("127.0.0.1", 0, root, runtime, state_hz=2.0)

    assert server.write_state({"status": "one"})
    assert server.write_state({"status": "stopped"}, force=True)
    assert json.loads((runtime / "state.json").read_text()) == {"status": "stopped"}
