import pytest

from ps2_autopilot.profiles.jak_and_daxter_v22_hardened import JakAndDaxterV22Profile
from ps2_autopilot.profiles.madden2005_v31 import Madden2005V31Profile
from ps2_autopilot.profiles.registry import (
    build_profile,
    canonical_profile_name,
    get_profile_spec,
    list_profile_specs,
)


def test_registry_selects_exact_active_madden_v31_factory():
    profile = build_profile({"name": "madden2005", "ocr_enabled": False})
    assert type(profile) is Madden2005V31Profile
    assert get_profile_spec("madden2005").maturity == "production-candidate"


def test_registry_selects_exact_active_jak_v22_without_reusing_madden():
    profile = build_profile({"name": "jak_and_daxter", "mode": "observe"})
    assert type(profile) is JakAndDaxterV22Profile
    assert not isinstance(profile, Madden2005V31Profile)
    assert get_profile_spec("jak").name == "jak_and_daxter"
    assert get_profile_spec("jak").maturity == "production-candidate"
    assert canonical_profile_name("precursor-legacy") == "jak_and_daxter"


def test_registry_listing_contains_both_real_games():
    names = {spec.name for spec in list_profile_specs()}
    assert {"madden2005", "jak_and_daxter"} <= names


def test_unknown_profile_has_useful_error():
    with pytest.raises(ValueError, match="Available profiles"):
        build_profile({"name": "not_a_real_game"})
