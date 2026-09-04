from __future__ import annotations

from ps2_autopilot.vision import TemplateMatch

from .base import ProfileContext
from .nfs_hot_pursuit_2 import NfsPhase
from .nfs_hot_pursuit_2_v2 import NfsScreen
from .nfs_hot_pursuit_2_v11 import NfsHotPursuit2V11Profile


class NfsHotPursuit2V12Profile(NfsHotPursuit2V11Profile):
    """V12: temporal semantic reacquisition for weak but persistent menu matches.

    The first V11.2 unattended runtime reached a race, then spent almost the entire
    retained trace in CALIBRATION/UNKNOWN while the asynchronous template detector
    repeatedly returned the same selected menu families below the global 0.84
    threshold. The generic bootstrap treated incidental animation as progress and
    cycled forever.

    V12 keeps the strict threshold unchanged. A sub-threshold template may claim a
    menu only when the same semantic screen persists for a configured hold time and
    the route action for that screen is Confirm. That makes the fallback useful for
    selected World Racing / Quick Race rows while refusing low-confidence directional
    navigation. Gameplay/HUD/road ownership always wins.
    """

    name = "nfs_hot_pursuit_2"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.semantic_hint_enabled = bool(cfg.get("semantic_hint_enabled", True))
        self.semantic_hint_min_score = max(
            0.10,
            min(
                max(0.10, self.template_threshold - 0.01),
                float(cfg.get("semantic_hint_min_score", 0.24)),
            ),
        )
        self.semantic_hint_hold_seconds = max(
            0.50, float(cfg.get("semantic_hint_hold_seconds", 1.50))
        )
        self.semantic_hint_max_motion = max(
            0.0, float(cfg.get("semantic_hint_max_motion", 0.020))
        )

        self.semantic_hint_screen: NfsScreen | None = None
        self.semantic_hint_since: float | None = None
        self.semantic_hint_claim_active = False
        self.semantic_hint_candidates = 0
        self.semantic_hint_accepts = 0
        self.semantic_hint_resets = 0
        self.semantic_hint_rejections = 0

    def _reset_semantic_hint(self) -> None:
        if self.semantic_hint_screen is not None or self.semantic_hint_claim_active:
            self.semantic_hint_resets += 1
        self.semantic_hint_screen = None
        self.semantic_hint_since = None
        self.semantic_hint_claim_active = False

    def _semantic_hint_candidate(
        self,
        ctx: ProfileContext,
        template: TemplateMatch | None,
    ) -> NfsScreen | None:
        if not self.semantic_hint_enabled or template is None:
            return None

        score = float(template.score)
        if score < self.semantic_hint_min_score or score >= self.template_threshold:
            return None
        if self.phase in {NfsPhase.RACING, NfsPhase.RECOVERY}:
            return None
        if self._hud_owned() or self.road.confidence >= self.drive_confidence:
            return None
        if ctx.motion > self.semantic_hint_max_motion:
            return None

        screen = self._screen_from_template(template.name)
        if screen is NfsScreen.UNKNOWN:
            return None

        # The weak-evidence path may only press Confirm on a row that is already
        # selected. Any Up/Down requirement still needs a strict template or bootstrap.
        if self._route_menu_action(screen) != "confirm":
            return None
        return screen

    def _recognized_screen(self, ctx: ProfileContext) -> NfsScreen:
        strict = super()._recognized_screen(ctx)
        if strict is not NfsScreen.UNKNOWN:
            self._reset_semantic_hint()
            return strict

        template = ctx.template
        candidate = self._semantic_hint_candidate(ctx, template)
        if candidate is None:
            if template is not None and float(template.score) >= self.semantic_hint_min_score:
                self.semantic_hint_rejections += 1
            self._reset_semantic_hint()
            return strict

        if candidate is not self.semantic_hint_screen:
            self.semantic_hint_screen = candidate
            self.semantic_hint_since = ctx.now
            self.semantic_hint_claim_active = False
            self.semantic_hint_candidates += 1
            return NfsScreen.UNKNOWN

        since = self.semantic_hint_since
        age = 0.0 if since is None else max(0.0, ctx.now - since)
        if age < self.semantic_hint_hold_seconds:
            return NfsScreen.UNKNOWN

        if not self.semantic_hint_claim_active:
            self.semantic_hint_claim_active = True
            self.semantic_hint_accepts += 1

        self.screen = candidate
        self.last_template = template.name if template is not None else None
        self.last_template_score = float(template.score) if template is not None else 0.0
        return candidate

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        hint_age = None
        if self.semantic_hint_since is not None:
            hint_age = round(max(0.0, ctx.now - self.semantic_hint_since), 2)
        state.update(
            {
                "nfs_policy_version": 12,
                "nfs_semantic_hint_enabled": self.semantic_hint_enabled,
                "nfs_semantic_hint_min_score": round(self.semantic_hint_min_score, 3),
                "nfs_semantic_hint_hold_seconds": round(self.semantic_hint_hold_seconds, 2),
                "nfs_semantic_hint_max_motion": round(self.semantic_hint_max_motion, 4),
                "nfs_semantic_hint_screen": (
                    self.semantic_hint_screen.value
                    if self.semantic_hint_screen is not None
                    else None
                ),
                "nfs_semantic_hint_age": hint_age,
                "nfs_semantic_hint_claim_active": self.semantic_hint_claim_active,
                "nfs_semantic_hint_candidates": self.semantic_hint_candidates,
                "nfs_semantic_hint_accepts": self.semantic_hint_accepts,
                "nfs_semantic_hint_resets": self.semantic_hint_resets,
                "nfs_semantic_hint_rejections": self.semantic_hint_rejections,
            }
        )
        return state
