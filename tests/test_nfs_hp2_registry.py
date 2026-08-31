from ps2_autopilot.profiles import NfsHotPursuit2Profile, NfsHotPursuit2V1Profile
from ps2_autopilot.profiles.registry import build_profile, canonical_profile_name, get_profile_spec


def test_registry_selects_nfs_hp2_v1():
    profile = build_profile({"name": "nfs_hot_pursuit_2"})
    assert type(profile) is NfsHotPursuit2V1Profile
    assert NfsHotPursuit2Profile is NfsHotPursuit2V1Profile
    assert get_profile_spec("nfs").name == "nfs_hot_pursuit_2"
    assert get_profile_spec("nfs_hp2").maturity == "diagnostic"


def test_nfs_aliases_are_canonicalized():
    assert canonical_profile_name("Hot Pursuit 2") == "nfs_hot_pursuit_2"
    assert canonical_profile_name("Need-for-Speed-Hot-Pursuit-2") == "nfs_hot_pursuit_2"
