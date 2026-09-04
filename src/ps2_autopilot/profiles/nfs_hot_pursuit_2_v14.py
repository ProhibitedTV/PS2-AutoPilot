from __future__ import annotations

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .nfs_hot_pursuit_2_v13 import NfsHotPursuit2V13Profile


class NfsHotPursuit2V14Profile(NfsHotPursuit2V13Profile):
    """V14: keep generic bootstrap out of evidence-backed menu transitions.

    The overnight V12 soak exposed a layering race between V3's menu transaction
    model and V6's unattended bootstrap. V12 repeatedly earned a weak but stable
    World Racing semantic, pressed Confirm, and correctly created a pending menu
    transaction. On the next transition frame the selected-row template disappeared,
    so V3 intentionally waited for positive destination evidence. V6 then saw only
    UNKNOWN and immediately ran its generic Start/Down/Confirm bootstrap anyway.

    That allowed the fallback ladder to inject unrelated inputs while a known menu
    action was still settling. V14 makes the arbitration explicit: while V3 owns a
    pending evidence-backed menu transaction, unattended bootstrap may observe but
    may not emit an input. If the transaction eventually resolves positively, normal
    routing continues. If it times out as ambiguous, V3 clears it and bootstrap is
    free to resume on subsequent ticks.
    """

    name = "nfs_hot_pursuit_2"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.menu_bootstrap_interlock_ticks = 0
        self.menu_bootstrap_interlock_events = 0
        self._menu_bootstrap_interlock_active = False

    def _bootstrap_tick(self, controller: Controller, ctx: ProfileContext) -> str:
        pending_action = self.pending_menu_action
        pending_screen = self.pending_menu_screen
        if pending_action is not None and pending_screen is not None:
            controller.release_all()
            controller.neutral_sticks()
            self.menu_bootstrap_interlock_ticks += 1
            if not self._menu_bootstrap_interlock_active:
                self.menu_bootstrap_interlock_events += 1
                self._menu_bootstrap_interlock_active = True
            age = max(0.0, ctx.now - self.pending_menu_since)
            return (
                "unattended bootstrap interlocked: pending menu "
                f"{pending_action} from {pending_screen.value} age={age:.1f}s"
            )

        self._menu_bootstrap_interlock_active = False
        return super()._bootstrap_tick(controller, ctx)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        pending_age = None
        if self.pending_menu_action is not None:
            pending_age = round(max(0.0, ctx.now - self.pending_menu_since), 2)
        state.update(
            {
                "nfs_policy_version": 14,
                "nfs_menu_bootstrap_interlock_active": self._menu_bootstrap_interlock_active,
                "nfs_menu_bootstrap_interlock_ticks": self.menu_bootstrap_interlock_ticks,
                "nfs_menu_bootstrap_interlock_events": self.menu_bootstrap_interlock_events,
                "nfs_menu_bootstrap_interlock_pending_action": self.pending_menu_action,
                "nfs_menu_bootstrap_interlock_pending_age": pending_age,
            }
        )
        return state
