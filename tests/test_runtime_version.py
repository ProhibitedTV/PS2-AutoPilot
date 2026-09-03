from ps2_autopilot import __version__
from ps2_autopilot.runtime_version import package_version


def test_runtime_version_tracks_checked_out_source():
    assert package_version() == __version__
