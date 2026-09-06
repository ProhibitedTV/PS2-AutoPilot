from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .base import GameProfile
from .generic_chaos import GenericChaosProfile
from .guitar_hero_v8 import GuitarHeroV8Profile
from .jak_and_daxter_v22_hardened import JakAndDaxterV22Profile
from .madden2005_v32 import Madden2005V32Profile
from .nfs_hot_pursuit_2_v14 import NfsHotPursuit2V14Profile


@dataclass(frozen=True)
class ProfileSpec:
    name: str
    display_name: str
    template_namespace: str
    maturity: str
    factory: Callable[[dict], GameProfile]


def _generic_factory(cfg: dict) -> GameProfile:
    return GenericChaosProfile(float(cfg.get("action_seconds", 1.25)))


def _guitar_hero_factory(cfg: dict) -> GameProfile:
    return GuitarHeroV8Profile(dict(cfg))


def _madden_factory(cfg: dict) -> GameProfile:
    return Madden2005V32Profile(dict(cfg))


def _jak_factory(cfg: dict) -> GameProfile:
    return JakAndDaxterV22Profile(dict(cfg))


def _nfs_hp2_factory(cfg: dict) -> GameProfile:
    return NfsHotPursuit2V14Profile(dict(cfg))


PROFILE_SPECS: dict[str, ProfileSpec] = {
    "generic_chaos": ProfileSpec(
        name="generic_chaos",
        display_name="Generic Smoke Test",
        template_namespace="generic_chaos",
        maturity="diagnostic",
        factory=_generic_factory,
    ),
    "guitar_hero": ProfileSpec(
        name="guitar_hero",
        display_name="Guitar Hero (2005)",
        template_namespace="guitar_hero",
        # V8 keeps V7's now-proven first-run/menu route and replaces the permissive
        # per-color gameplay detector with joint five-receptor geometry plus a narrow
        # note-arrival zone. Keep diagnostic until a full song/results loop survives
        # live timing calibration.
        maturity="diagnostic",
        factory=_guitar_hero_factory,
    ),
    "madden2005": ProfileSpec(
        name="madden2005",
        display_name="Madden NFL 2005",
        template_namespace="madden2005",
        # V23 earned a seven-game unattended lifecycle soak. V24/V25 changed
        # special-teams/kick ownership; V26/V27 changed active football semantics;
        # V28 added root-menu reacquisition; V29 added live-evidence visual play-call
        # reacquisition and Ask Madden fallback; V30 owns Controller Layout safely;
        # V31 keeps special-teams side ownership across playcall -> field setup and
        # makes session checkpoint locks non-fatal; V32 makes visual play-call
        # reacquisition team-theme tolerant and latches quarter-break presentation.
        # Re-soak the complete active stack.
        maturity="production-candidate",
        factory=_madden_factory,
    ),
    "jak_and_daxter": ProfileSpec(
        name="jak_and_daxter",
        display_name="Jak and Daxter: The Precursor Legacy",
        template_namespace="jak_and_daxter",
        maturity="production-candidate",
        factory=_jak_factory,
    ),
    "nfs_hot_pursuit_2": ProfileSpec(
        name="nfs_hot_pursuit_2",
        display_name="Need for Speed: Hot Pursuit 2",
        template_namespace="nfs_hot_pursuit_2",
        # V14 keeps V13's replay-chrome escape and V12's weak selected-row
        # reacquisition, while interlocking V6's generic bootstrap behind V3's
        # evidence-backed pending menu transactions. A known Confirm transition now
        # gets a quiet acknowledgement window instead of competing bootstrap inputs.
        # Keep diagnostic until the active unattended stack earns a fresh soak.
        maturity="diagnostic",
        factory=_nfs_hp2_factory,
    ),
}

PROFILE_ALIASES = {
    "gh": "guitar_hero",
    "gh1": "guitar_hero",
    "guitarhero": "guitar_hero",
    "guitar_hero_1": "guitar_hero",
    "madden": "madden2005",
    "jak": "jak_and_daxter",
    "jak1": "jak_and_daxter",
    "precursor_legacy": "jak_and_daxter",
    "nfs": "nfs_hot_pursuit_2",
    "nfs_hp2": "nfs_hot_pursuit_2",
    "hot_pursuit_2": "nfs_hot_pursuit_2",
    "need_for_speed_hot_pursuit_2": "nfs_hot_pursuit_2",
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
