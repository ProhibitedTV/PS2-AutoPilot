from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

from ps2_autopilot.overlay import OverlayServer


def _overlay(tmp_path: Path) -> OverlayServer:
    root = tmp_path / "overlay"
    runtime = tmp_path / "runtime"
    root.mkdir()
    return OverlayServer("127.0.0.1", 0, root, runtime, state_hz=2.0)


def test_state_publish_retries_transient_permission_error(monkeypatch, tmp_path: Path) -> None:
    server = _overlay(tmp_path)
    real_replace = Path.replace
    calls = 0

    def flaky_replace(path: Path, target: Path) -> Path:
        nonlocal calls
        if path.name.startswith(".state-") and calls < 2:
            calls += 1
            raise PermissionError(5, "simulated Windows file lock")
        return real_replace(path, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    assert server.write_state({"status": "running", "score": 47}, force=True) is True
    assert server.state_write_retries == 2
    assert server.state_write_failures == 0
    assert server.state_write_last_error is None
    assert json.loads(server.state_file.read_text(encoding="utf-8"))["score"] == 47


def test_locked_state_file_never_crashes_and_http_serves_memory(
    monkeypatch, tmp_path: Path
) -> None:
    server = _overlay(tmp_path)

    def locked_replace(path: Path, target: Path) -> Path:
        raise PermissionError(5, "simulated persistent Windows file lock")

    monkeypatch.setattr(Path, "replace", locked_replace)

    # A permanently locked compatibility file is non-fatal: the newest state is
    # still authoritative in memory and write_state reports an accepted update.
    assert server.write_state({"status": "running", "quarter": 4}, force=True) is True
    assert server.state_write_retries == 3
    assert server.state_write_failures == 1
    assert "PermissionError" in (server.state_write_last_error or "")
    assert json.loads(server.state_payload())["quarter"] == 4

    server.start()
    try:
        assert server.server is not None
        port = int(server.server.server_address[1])
        with urlopen(f"http://127.0.0.1:{port}/state.json", timeout=2.0) as response:
            body = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate"
        assert body == {"status": "running", "quarter": 4}
    finally:
        server.stop()
