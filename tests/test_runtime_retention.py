from pathlib import Path

from ps2_autopilot.runtime_retention import RuntimeRetention


def _write(path: Path, size: int = 64) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def test_clear_previous_run_preserves_stop_flag(tmp_path):
    root = tmp_path / "runtime"
    _write(root / "events.jsonl")
    _write(root / "unknown" / "one.png")
    _write(root / "STOP24X7", 1)

    retention = RuntimeRetention(root)
    result = retention.clear_previous_run()

    assert result.removed_items >= 2
    assert (root / "STOP24X7").exists()
    assert not (root / "events.jsonl").exists()
    assert not (root / "unknown").exists()


def test_prune_caps_failure_bundles_and_unknown_captures(tmp_path):
    root = tmp_path / "runtime"
    for index in range(5):
        _write(root / "failures" / f"bundle-{index}" / "frame.png", 128)
        _write(root / "unknown" / f"unknown-{index}.png", 128)
        _write(root / "unknown" / f"unknown-{index}.json", 32)

    retention = RuntimeRetention(
        root,
        max_total_mb=32,
        max_failure_bundles=2,
        max_unknown_captures=2,
    )
    result = retention.prune()

    assert len(list((root / "failures").iterdir())) == 2
    assert len(list((root / "unknown").glob("*.png"))) == 2
    assert len(list((root / "unknown").glob("*.json"))) == 2
    assert result.removed_items >= 6
