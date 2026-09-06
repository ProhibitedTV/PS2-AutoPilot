import pytest

from ps2_autopilot.profiles import GuitarHeroProfile, Madden2005Profile
from ps2_autopilot.profiles.guitar_hero_v2 import GuitarHeroV2Profile
from ps2_autopilot.profiles.guitar_hero_v3 import GuitarHeroV3Profile
from ps2_autopilot.profiles.guitar_hero_v4 import GuitarHeroV4Profile
from ps2_autopilot.profiles.guitar_hero_v5 import GuitarHeroV5Profile
from ps2_autopilot.profiles.guitar_hero_v6 import GuitarHeroV6Profile
from ps2_autopilot.profiles.guitar_hero_v7 import GuitarHeroV7Profile
from ps2_autopilot.profiles.guitar_hero_v8 import GuitarHeroV8Profile
from ps2_autopilot.profiles.guitar_hero_v9 import GuitarHeroV9Profile
from ps2_autopilot.profiles.guitar_hero_v10 import GuitarHeroV10Profile
from ps2_autopilot.profiles.jak_and_daxter_v22_hardened import JakAndDaxterV22Profile
from ps2_autopilot.profiles.madden2005_v32 import Madden2005V32Profile
from ps2_autopilot.profiles.registry import (
    build_profile,
    canonical_profile_name,
    get_profile_spec,
    list_profile_specs,
)


def test_registry_selects_exact_active_madden_v32_factory():
    profile = build_profile({"name": "madden2005", "ocr_enabled": False})
    assert type(profile) is Madden2005V32Profile
    assert Madden2005Profile is Madden2005V32Profile
    assert get_profile_spec("madden2005").maturity == "production-candidate"


def test_registry_selects_guitar_hero_v10_and_aliases():
    profile = build_profile({"name": "gh1", "difficulty": "easy"})
    assert type(profile) is GuitarHeroV10Profile
    assert GuitarHeroProfile is GuitarHeroV10Profile
    assert GuitarHeroV2Profile is not GuitarHeroV10Profile
    assert GuitarHeroV3Profile is not GuitarHeroV10Profile
    assert GuitarHeroV4Profile is not GuitarHeroV10Profile
    assert GuitarHeroV5Profile is not GuitarHeroV10Profile
    assert GuitarHeroV6Profile is not GuitarHeroV10Profile
    assert GuitarHeroV7Profile is not GuitarHeroV10Profile
    assert GuitarHeroV8Profile is not GuitarHeroV10Profile
    assert GuitarHeroV9Profile is not GuitarHeroV10Profile
    assert get_profile_spec("guitarhero").name == "guitar_hero"
    assert get_profile_spec("gh").maturity == "diagnostic"
    assert canonical_profile_name("guitar-hero-1") == "guitar_hero"


def test_registry_selects_exact_active_jak_v22_without_reusing_madden():
    profile = build_profile({"name": "jak_and_daxter", "mode": "observe"})
    assert type(profile) is JakAndDaxterV22Profile
    assert not isinstance(profile, Madden2005V32Profile)
    assert get_profile_spec("jak").name == "jak_and_daxter"
    assert get_profile_spec("jak").maturity == "production-candidate"
    assert canonical_profile_name("precursor-legacy") == "jak_and_daxter"


def test_registry_listing_contains_real_games():
    names = {spec.name for spec in list_profile_specs()}
    assert {"madden2005", "jak_and_daxter", "guitar_hero"} <= names


def test_unknown_profile_has_useful_error():
    with pytest.raises(ValueError, match="Available profiles"):
        build_profile({"name": "not_a_real_game"})
