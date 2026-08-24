import json

from ps2_autopilot.overlay import OverlayServer


def make_server(tmp_path, monkeypatch, *, supervised: bool):
    runtime = tmp_path / "runtime"
    root = tmp_path / "overlay"
    runtime.mkdir()
    root.mkdir()
    if supervised:
        monkeypatch.setenv("PS2_AUTOPILOT_SUPERVISED", "1")
        monkeypatch.setenv(
            "PS2_AUTOPILOT_SUPERVISOR_STATE", str(runtime / "supervisor.json")
        )
    else:
        monkeypatch.delenv("PS2_AUTOPILOT_SUPERVISED", raising=False)
        monkeypatch.delenv("PS2_AUTOPILOT_SUPERVISOR_STATE", raising=False)
    return OverlayServer("127.0.0.1", 0, root, runtime, state_hz=4.0)


def test_supervised_overlay_merges_prefixed_supervisor_state(tmp_path, monkeypatch):
    server = make_server(tmp_path, monkeypatch, supervised=True)
    (server.runtime / "supervisor.json").write_text(
        json.dumps(
            {
                "supervisor_status": "running",
                "supervisor_autopilot_restarts": 2,
                "supervisor_emulator_restarts": 1,
                "untrusted_unprefixed": "ignore-me",
            }
        ),
        encoding="utf-8",
    )

    assert server.write_state({"status": "running", "action": "play"}, force=True)
    payload = json.loads(server.state_payload())

    assert payload["status"] == "running"
    assert payload["supervisor_status"] == "running"
    assert payload["supervisor_autopilot_restarts"] == 2
    assert payload["supervisor_emulator_restarts"] == 1
    assert "untrusted_unprefixed" not in payload


def test_direct_run_ignores_stale_supervisor_file(tmp_path, monkeypatch):
    server = make_server(tmp_path, monkeypatch, supervised=False)
    (server.runtime / "supervisor.json").write_text(
        json.dumps(
            {
                "supervisor_status": "running",
                "supervisor_autopilot_restarts": 99,
            }
        ),
        encoding="utf-8",
    )

    assert server.write_state({"status": "running"}, force=True)
    payload = json.loads(server.state_payload())

    assert payload == {"status": "running"}
