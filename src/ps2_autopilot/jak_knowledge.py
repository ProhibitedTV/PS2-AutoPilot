from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class JakControlMode(str, Enum):
    UNKNOWN = "unknown"
    ON_FOOT = "on_foot"
    ZOOMER = "zoomer"
    FLUT_FLUT = "flut_flut"
    CANNON = "cannon"
    FISHING = "fishing"


@dataclass(frozen=True)
class JakProgression:
    power_cells: int | None = None
    precursor_orbs: int | None = None
    scout_flies: int | None = None


# The original game uses these persistent collectible totals. They are useful as
# production telemetry/goal signals, but the runtime never assumes a value changed
# unless it was actually observed in-game.
COLLECTIBLE_TOTALS = {
    "power_cells": 101,
    "precursor_orbs": 2000,
    "scout_flies": 112,
}

# Research-backed progression gates. Different old guides disagree about the exact
# late-game Lava Tube quota (72 vs 80), so only uncontroversial gates are encoded as
# strict milestones here; late-game progress remains observation-driven.
PROGRESSION_MILESTONES = (
    (20, "Fire Canyon access"),
    (45, "Mountain Pass access"),
)

# Controls differ substantially by gameplay mode. Keep this as machine-readable
# documentation so policies do not accidentally reuse on-foot semantics on vehicles.
CONTROL_SCHEMAS: dict[JakControlMode, dict[str, str]] = {
    JakControlMode.ON_FOOT: {
        "left_stick": "move",
        "right_stick": "camera",
        "cross": "jump / double jump",
        "square": "punch / yellow eco projectile",
        "circle": "spin attack",
        "triangle": "look-around view",
        "l1/r1": "crouch or roll",
        "l2/r2": "heads-up progress totals",
        "start": "pause / progress screen",
    },
    JakControlMode.ZOOMER: {
        "left_stick": "steer / pitch",
        "right_stick": "camera",
        "cross": "accelerate",
        "l1/r1": "hop / tight turn",
        "l2/r2": "heads-up progress totals",
        "start": "pause / progress screen",
    },
    JakControlMode.FLUT_FLUT: {
        "left_stick": "move",
        "right_stick": "camera",
        "cross": "jump / flutter",
        "square": "headbutt / yellow eco projectile",
        "l2/r2": "heads-up progress totals",
        "start": "pause / progress screen",
    },
    JakControlMode.CANNON: {
        "left_stick": "aim",
        "cross": "fire / charge shot",
        "triangle": "leave cannon",
        "l2/r2": "heads-up progress totals",
        "start": "pause / progress screen",
    },
    JakControlMode.FISHING: {
        "left_stick": "move fishing net laterally",
    },
    JakControlMode.UNKNOWN: {},
}


_MODE_MARKERS: tuple[tuple[JakControlMode, tuple[str, ...]], ...] = (
    (JakControlMode.ZOOMER, ("zoomer", "a_grav", "agrav", "fire_canyon", "mountain_pass", "lava_tube")),
    (JakControlMode.FLUT_FLUT, ("flut", "flutflut")),
    (JakControlMode.CANNON, ("cannon", "turret")),
    (JakControlMode.FISHING, ("fishing", "fish_game", "fisherman_minigame")),
    (JakControlMode.ON_FOOT, ("gameplay", "world", "explore", "on_foot", "geyser", "sandover")),
)


def control_mode_for_template(name: str | None) -> JakControlMode:
    value = str(name or "").strip().lower().replace("-", "_").replace(" ", "_")
    for mode, markers in _MODE_MARKERS:
        if any(marker in value for marker in markers):
            return mode
    return JakControlMode.UNKNOWN
