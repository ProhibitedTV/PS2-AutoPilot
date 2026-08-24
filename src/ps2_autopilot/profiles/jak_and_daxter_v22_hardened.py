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

    The production wrapper also closes a consequence-learning gap around water. A
    confirmed water entry is severe enough to remain dangerous after the short
    episode quarantine expires, and a successful swim exit teaches the experience
    table which left/right escape direction actually returned Jak to land.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        # V21's original 0.72 water penalty intentionally required two water entries
        # before a route crossed the 1.25 persistent-danger threshold. V13+ now uses a
        # much stricter coherent-water ownership gate, so a *confirmed* entry can be
        # treated as strong evidence immediately. Progress rewards can still
        # rehabilitate an area later if the classification was overly conservative.
        default_floor = self.learning_danger_threshold + 0.10
        self.v22_water_danger_floor = max(
            self.learning_danger_threshold + 0.01,
            min(10.0, float(cfg.get("v22_water_danger_floor", default_floor))),
        )
        self.learning_water_penalty = max(
            self.learning_water_penalty,
            self.v22_water_danger_floor,
        )

        self.v22_water_entry_keys: tuple[str, ...] = ()
        self.v22_water_escape_learning_events = 0
        self.v22_last_water_escape_direction = 0.0

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

    def _record_water_entry(self, ctx: ProfileContext) -> None:
        super()._record_water_entry(ctx)
        # Preserve the approach cells before normal sampling moves the current scene
        # away from the shoreline. These are the cells that should learn which escape
        # direction worked when the swim controller eventually reaches dry land.
        self.v22_water_entry_keys = tuple(self.learning_episode_avoid_keys)

    def _remember_successful_water_escape(
        self,
        ctx: ProfileContext,
        *,
        direction: float,
    ) -> None:
        keys = self.v22_water_entry_keys
        if not self.learning_enabled or not keys:
            self.v22_water_entry_keys = ()
            return

        learned_direction = 1.0 if float(direction) >= 0.0 else -1.0
        self.experience.mark_escape_result(
            keys,
            direction=learned_direction,
            success=True,
            now=ctx.now,
        )
        self.v22_water_escape_learning_events += 1
        self.v22_last_water_escape_direction = learned_direction
        self.learning_last_event = "water-escape-success"
        self.v22_water_entry_keys = ()
        # A successful exit is valuable persistent evidence. Respect the normal save
        # cadence rather than forcing disk I/O on the gameplay tick.
        self.experience.maybe_save(ctx.now)

    def _refresh_water_state(self, ctx: ProfileContext) -> None:
        was_active = self.water_escape_active
        previous_direction = float(getattr(self, "water_nav_direction", 0.0))
        super()._refresh_water_state(ctx)

        if was_active and not self.water_escape_active:
            # V16 has already converted the same successful heading into an 8-second
            # inland commitment by the time control returns here. Reuse that evidence
            # for persistent route learning instead of making future avoidance guess.
            direction = previous_direction or float(getattr(self, "shore_exit_direction", 0.0))
            if direction == 0.0:
                direction = -1.0 if float(getattr(self, "route_bias", 0.0)) > 0.0 else 1.0
            self._remember_successful_water_escape(ctx, direction=direction)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "jak_learning_water_danger_floor_v22": round(self.v22_water_danger_floor, 3),
                "jak_learning_water_escape_events_v22": self.v22_water_escape_learning_events,
                "jak_learning_last_water_escape_direction_v22": (
                    "R" if self.v22_last_water_escape_direction > 0.0
                    else "L" if self.v22_last_water_escape_direction < 0.0
                    else "none"
                ),
                "jak_learning_water_entry_keys_pending_v22": len(self.v22_water_entry_keys),
            }
        )
        return state
