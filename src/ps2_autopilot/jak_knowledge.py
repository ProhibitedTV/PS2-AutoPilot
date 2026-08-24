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


COLLECTIBLE_TOTALS = {
    "power_cells": 101,
    "precursor_orbs": 2000,
    "scout_flies": 112,
}

PROGRESSION_MILESTONES = (
    (20, "Fire Canyon access"),
    (45, "Mountain Pass access"),
)

# Control semantics are deliberately mode-specific. A full-game agent must never
# assume that Cross/Square mean the same thing on foot, on a Zoomer or at a cannon.
CONTROL_SCHEMAS: dict[JakControlMode, dict[str, str]] = {
    JakControlMode.ON_FOOT: {
        "left_stick": "move",
        "right_stick": "camera",
        "cross": "jump / double jump",
        "square": "punch / dive attack follow-up / yellow eco projectile",
        "circle": "spin attack / interact",
        "triangle": "first-person camera",
        "l1/r1": "crouch while still / roll while moving",
        "l1/r1 + cross": "high jump while still / rolling long jump while moving",
        "l2/r2": "heads-up progress totals",
        "start": "pause / progress screen",
    },
    JakControlMode.ZOOMER: {
        "left_stick": "steer / pitch",
        "right_stick": "camera",
        "cross": "accelerate",
        "square": "brake",
        "circle/square": "shoot yellow eco when powered",
        "l1/r1": "hop / hard-turn modifier",
        "l2/r2": "heads-up progress totals",
        "start": "pause / progress screen",
    },
    JakControlMode.FLUT_FLUT: {
        "left_stick": "move",
        "right_stick": "camera",
        "cross": "jump / second press flutter",
        "square": "headbutt / yellow eco projectile",
        "cross + square": "dive attack",
        "l2/r2": "heads-up progress totals",
        "start": "pause / progress screen",
    },
    JakControlMode.CANNON: {
        "left_stick": "aim",
        "cross": "fire / hold for charged shot",
        "triangle": "leave cannon",
        "l2/r2": "heads-up progress totals",
        "start": "pause / progress screen",
    },
    JakControlMode.FISHING: {
        "left_stick": "move fishing net laterally into fish path",
        "objective": "catch 200 pounds; avoid poisonous eel; missing 20 pounds resets",
    },
    JakControlMode.UNKNOWN: {},
}

# Campaign-wide specialist inventory. This is machine-readable planning metadata,
# not an assertion that the current visual policy can already solve every challenge.
# It tells future objective/skill planners which controller specialists must exist.
CAMPAIGN_SPECIALISTS = {
    "on_foot_platforming": JakControlMode.ON_FOOT,
    "a_grav_zoomer": JakControlMode.ZOOMER,
    "flut_flut": JakControlMode.FLUT_FLUT,
    "lurker_cannon": JakControlMode.CANNON,
    "fisherman_minigame": JakControlMode.FISHING,
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
