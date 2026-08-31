from pathlib import Path

from ps2_autopilot.madden_runtime import MaddenRuntimeMonitor


def test_session_checkpoint_retries_transient_windows_replace_lock(tmp_path, monkeypatch):
    monitor = MaddenRuntimeMonitor({}, runtime_root=tmp_path)
    original_replace = Path.replace
    attempts = {"count": 0}

    def flaky_replace(path, target):
        if Path(target).name == "session.json" and attempts["count"] < 2:
            attempts["count"] += 1
            raise PermissionError(5, "Access is denied")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    assert monitor._save_session({"phase": "pre_snap", "action": "test"})
    assert monitor.session_write_retries == 2
    assert monitor.session_write_failures == 0
    assert monitor.session_write_last_error is None
    assert monitor.session_path.exists()


def test_session_checkpoint_lock_failure_never_escapes_game_loop(tmp_path, monkeypatch):
    monitor = MaddenRuntimeMonitor({}, runtime_root=tmp_path)

    def denied_replace(path, target):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(Path, "replace", denied_replace)

    assert not monitor._save_session({"phase": "kicking", "action": "kickoff"})
    assert monitor.session_write_retries == 3
    assert monitor.session_write_failures == 1
    assert "PermissionError" in str(monitor.session_write_last_error)

    telemetry = monitor.telemetry(100.0)
    assert telemetry["session_write_retries"] == 3
    assert telemetry["session_write_failures"] == 1
    assert "Access is denied" in telemetry["session_write_last_error"]
