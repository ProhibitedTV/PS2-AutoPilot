from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .base import GameProfile
from .generic_chaos import GenericChaosProfile
from .jak_and_daxter_v17 import JakAndDaxterV17Profile
from .madden2005_v22 import Madden2005V22Profile


@dataclass(frozen=True)
class ProfileSpec:
    name: str
    display_name: str
    template_namespace: str
    maturity: str
    factory: Callable[[dict], GameProfile]


def _generic_factory(cfg: dict) -> GameProfile:
    return GenericChaosProfile(float(cfg.get("action_seconds", 1.25)))


def _madden_factory(cfg: dict) -> GameProfile:
    return Madden2005V22Profile(dict(cfg))


def _jak_factory(cfg: dict) -> GameProfile:
    return JakAndDaxterV17Profile(dict(cfg))


PROFILE_SPECS: dict[str, ProfileSpec] = {
    "generic_chaos": ProfileSpec(
        name="generic_chaos",
        display_name="Generic Smoke Test",
        template_namespace="generic_chaos",
        maturity="diagnostic",
        factory=_generic_factory,
    ),
    "madden2005": ProfileSpec(
        name="madden2005",
        display_name="Madden NFL 2005",
        template_namespace="madden2005",
        maturity="soak-tested",
        factory=_madden_factory,
    ),
    "jak_and_daxter": ProfileSpec(
        name="jak_and_daxter",
        display_name="Jak and Daxter: The Precursor Legacy",
        template_namespace="jak_and_daxter",
        maturity="production-candidate",
        factory=_jak_factory,
    ),
}

PROFILE_ALIASES = {
    "madden": "madden2005",
    "jak": "jak_and_daxter",
    "jak1": "jak_and_daxter",
    "precursor_legacy": "jak_and_daxter",
}


def canonical_profile_name(name: str) -> str:
    normalized = str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
    return PROFILE_ALIASES.get(normalized, normalized)


def get_profile_spec(name: str) -> ProfileSpec:
    canonical = canonical_profile_name(name)
    try:
        return PROFILE_SPECS[canonical]
    except KeyError as exc:
        available = ", ".join(sorted(PROFILE_SPECS))
        raise ValueError(f"Unknown profile '{name}'. Available profiles: {available}") from exc


def build_profile(cfg: dict) -> GameProfile:
    name = str(cfg.get("name", "generic_chaos"))
    return get_profile_spec(name).factory(dict(cfg))


def list_profile_specs() -> tuple[ProfileSpec, ...]:
    return tuple(PROFILE_SPECS[name] for name in sorted(PROFILE_SPECS))
