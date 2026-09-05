from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_generic_24x7_launcher_requires_explicit_config():
    script = _read("run24x7.cmd")

    assert 'if "%CONFIG%"=="" goto :missing_config' in script
    assert 'if "%CONFIG%"=="" set "CONFIG=config\\madden2005.yaml"' not in script
    assert "run-guitar-hero24x7.cmd" in script
    assert "run-nfs24x7.cmd" in script
    assert "run-madden24x7.cmd" in script
    assert "run-jak24x7.cmd" in script


def test_game_specific_launchers_pin_their_configs():
    assert "config\\guitar_hero.yaml" in _read("run-guitar-hero24x7.cmd")
    assert "config\\nfs_hot_pursuit_2.yaml" in _read("run-nfs24x7.cmd")
    assert "config\\madden2005.yaml" in _read("run-madden24x7.cmd")
    assert "config\\jak_and_daxter.yaml" in _read("run-jak24x7.cmd")

    for name in (
        "run-guitar-hero24x7.cmd",
        "run-nfs24x7.cmd",
        "run-madden24x7.cmd",
        "run-jak24x7.cmd",
    ):
        assert "call run24x7.cmd" in _read(name)
