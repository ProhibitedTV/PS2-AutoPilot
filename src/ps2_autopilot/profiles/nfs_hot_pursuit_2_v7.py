from __future__ import annotations

from dataclasses import replace

from ps2_autopilot.nfs_hp2_hud import GameplayHudObservation, estimate_gameplay_hud
from ps2_autopilot.vision import TemplateMatch

from .base import ProfileContext
from .nfs_hot_pursuit_2 import NfsPhase
from .nfs_hot_pursuit_2_v6 import NfsHotPursuit2V6Profile


class NfsHotPursuit2V7Profile(NfsHotPursuit2V6Profile):
    """V7: fixed-HUD gameplay ownership for long-running unattended races.

    The overnight V6 failure corpus contained 30 retained motion-watchdog bundles.
    Every screenshot showed the active HP2 race HUD while the car was stationary and
    the race timer had exceeded eight hours. Because road segmentation was near zero,
    V6 treated the scene as UNKNOWN and repeatedly ran bootstrap/watchdog Start probes.

    V7 recognizes the PS2 gameplay HUD from normalized geometry. When the HUD is
    strong and no better explicit template exists, it injects a synthetic race-HUD
    semantic into the existing V2-V6 state machine. This preserves all established
    racing, hazard and recovery behavior while preventing active gameplay from falling
    back into menu bootstrap merely because the pavement detector is blind.
    """

    name = "nfs_hot_pursuit_2"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.hud_gameplay_enabled = bool(cfg.get("hud_gameplay_enabled", True))
        self.hud_gameplay_threshold = max(
            0.55, min(0.99, float(cfg.get("hud_gameplay_threshold", 0.82)))
        )
        self.hud = GameplayHudObservation.unavailable()
        self.hud_gameplay_claims = 0
        self.hud_gameplay_ticks = 0
        self.hud_watchdog_promotions = 0
        self._hud_claim_active = False

    def _context_with_hud_semantic(self, ctx: ProfileContext) -> ProfileContext:
        self.hud = estimate_gameplay_hud(ctx.frame)
        if not self.hud_gameplay_enabled or self.hud.confidence < self.hud_gameplay_threshold:
            self._hud_claim_active = False
            return ctx

        self.hud_gameplay_ticks += 1

        # Explicit calibrated templates remain authoritative for results, replay,
        # pause, BUSTED, known menus, etc. Geometry fills only the no-template hole.
        if ctx.template is not None and ctx.template.score >= self.template_threshold:
            self._hud_claim_active = False
            return ctx

        if not self._hud_claim_active:
            self.hud_gameplay_claims += 1
        self._hud_claim_active = True

        synthetic_score = max(self.template_threshold + 0.01, self.hud.confidence)
        return replace(
            ctx,
            template=TemplateMatch("nfs_race_hud_geometry", min(1.0, synthetic_score)),
        )

    def tick(self, controller, ctx: ProfileContext) -> str:
        owned_ctx = self._context_with_hud_semantic(ctx)
        action = super().tick(controller, owned_ctx)
        if self._hud_claim_active:
            self.last_action = (
                f"hud-owned gameplay conf={self.hud.confidence:.2f}: {action}"
            )
            return self.last_action
        return action

    def recover(self, controller) -> str:
        # The shared watchdog has no ProfileContext. If the immediately preceding
        # decision positively saw the fixed gameplay HUD, promote ownership back to
        # racing before delegating so recovery is reverse/steer, never a menu Start.
        if (
            self.hud_gameplay_enabled
            and self.hud.confidence >= self.hud_gameplay_threshold
            and self.phase not in {NfsPhase.RACING, NfsPhase.RECOVERY}
        ):
            self.phase = NfsPhase.RACING
            self.hud_watchdog_promotions += 1
        return super().recover(controller)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "nfs_policy_version": 7,
                "nfs_hud_gameplay_enabled": self.hud_gameplay_enabled,
                "nfs_hud_confidence": round(self.hud.confidence, 3),
                "nfs_hud_rank_score": round(self.hud.rank_score, 3),
                "nfs_hud_status_score": round(self.hud.status_score, 3),
                "nfs_hud_tach_score": round(self.hud.tach_score, 3),
                "nfs_hud_map_score": round(self.hud.map_score, 3),
                "nfs_hud_gameplay_claim_active": self._hud_claim_active,
                "nfs_hud_gameplay_claims": self.hud_gameplay_claims,
                "nfs_hud_gameplay_ticks": self.hud_gameplay_ticks,
                "nfs_hud_watchdog_promotions": self.hud_watchdog_promotions,
            }
        )
        return state
