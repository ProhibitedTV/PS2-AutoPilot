from __future__ import annotations

from ps2_autopilot.guitar_hero_vision_v8 import GuitarHeroVisionV8

from .base import ProfileContext
from .guitar_hero_v7 import GuitarHeroV7Profile


class GuitarHeroV8Profile(GuitarHeroV7Profile):
    """V8: replace permissive live-note vision with joint highway tracking.

    V7 proved the entire boot/menu route can reach a song, but the first live gameplay
    attempt immediately behaved like random button mashing.  The root problem is in
    the original gameplay detector: each fret receptor was chosen independently from
    any same-colored contour in the lower screen, then a broad strip above that point
    could fire a note.  A concert stage is full of exactly those colors.

    V8 keeps the now-proven V7 lifecycle and swaps only the highway observer.  The new
    observer solves all five fret receptors as a single ordered/equally-spaced row,
    locks that geometry only after repeated agreement, and emits a fret hit only when
    a matching note component reaches a narrow arrival zone immediately above the
    locked receptor.
    """

    policy_version = 8

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.vision = GuitarHeroVisionV8(self.cfg)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        vision = self.vision
        state.update(
            {
                "gh_policy_version": self.policy_version,
                "gh_highway_layout_locked": bool(vision.layout_locked),
                "gh_highway_lock_support": int(vision.lock_support),
                "gh_note_gaps": [
                    None if value is None else round(float(value), 4)
                    for value in vision.note_gaps
                ],
                "gh_note_trigger_gap": round(float(vision.note_trigger_gap), 4),
            }
        )
        return state
