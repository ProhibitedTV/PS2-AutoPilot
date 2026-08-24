from __future__ import annotations

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .jak_and_daxter_v22 import JakAndDaxterV22Profile as _JakAndDaxterV22BaseProfile


class JakAndDaxterV22Profile(_JakAndDaxterV22BaseProfile):
    """Reviewed V22 production wrapper with upstream ownership gaps closed.

    The first atomic-engine review found three call paths above `_on_foot` that could
    violate transaction ownership. Keep the fixes isolated here so the initial V22
    implementation remains readable while the registered/exported V22 class is the
    hardened variant.
    """

    def _maybe_progress_probe(self, controller: Controller, ctx: ProfileContext) -> bool:
        # `_production_gameplay` checks the HUD probe before dispatching to `_on_foot`.
        # An active atomic skill therefore has to defer the probe at this higher layer
        # or an 0.8s R2 hold can consume most/all of a jump transaction's timeout.
        if self.atomic_skills.is_active:
            if self.next_progress_probe_at is not None and ctx.now >= self.next_progress_probe_at:
                self.next_progress_probe_at = ctx.now + max(1.0, self.v22_skill_timeout)
            return False
        return super()._maybe_progress_probe(controller, ctx)

    def _verify_atomic_skill(self, skill, ctx: ProfileContext) -> bool:
        # If trusted contact telemetry explicitly says Jak is airborne, neither XYZ
        # nor ordinary optical motion may close a landing-required transaction. This
        # check must happen before the base motion fallback, not only in its XYZ branch.
        grounded = self._semantic_grounded(ctx)
        if grounded is False and not bool(skill.payload.get("allow_air_success", False)):
            return False
        return super()._verify_atomic_skill(skill, ctx)

    def _start_ledge_jump(self, controller: Controller, ctx: ProfileContext) -> str:
        # The base constructor starts the transaction. Service ALIGN once more with the
        # live controller on the same tick so a previously persistent stick command
        # cannot survive for nearly the whole alignment window.
        super()._start_ledge_jump(controller, ctx)
        return self._service_atomic_skill(controller, ctx)

    def _start_mobility_probe(self, controller: Controller, ctx: ProfileContext) -> str:
        super()._start_mobility_probe(controller, ctx)
        return self._service_atomic_skill(controller, ctx)
