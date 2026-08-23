from __future__ import annotations

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .jak_and_daxter import JakPhase
from .jak_and_daxter_v10 import JakAndDaxterV10Profile


class JakAndDaxterV11Profile(JakAndDaxterV10Profile):
    """Make mid-session gameplay ownership evidence adaptive instead of brittle.

    The 2026-08-23 V9 soak bundle captured 30 watchdog failures while Jak was visibly
    swimming against the same Geyser Rock wall. The camera-only attach probe *was*
    causing repeatable screen motion (typically ~0.004-0.007 from ~0.001-0.003 idle
    baselines), but V7's inherited absolute response floor was 0.018. As a result the
    profile spent roughly 25 minutes doing nothing but probing, failing closed and
    incrementing watchdog recoveries.

    V11 replaces the all-or-nothing attach decision with an evidence accumulator:

    * each reversible right-stick pulse is judged relative to its own idle baseline;
    * modest but repeatable responses accumulate confidence instead of requiring two
      consecutive high-amplitude hits;
    * opposite-direction pulses are still used so a one-off animated scene is weak
      evidence while repeatable controller-correlated response is strong evidence;
    * a strongly coherent water surface lowers the number of required confirmations
      because the live failure bundle is unambiguously an in-game swim state, but
      water alone never grants gameplay ownership;
    * no controller response still fails closed exactly as before.

    The same adaptive threshold is also used when a previously proven gameplay session
    temporarily falls back to UNKNOWN. That closes the second hole exposed by the live
    data: a weak camera response near a wall must not strand the sticky gameplay owner.

    Once gameplay ownership is recovered, V9/V10 immediately regain authority, so the
    coherent-water escape and local obstacle recovery can finally act on the actual
    problem instead of being stranded behind UNKNOWN state.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        self.attach_adaptive_min_motion = max(
            0.0015, min(0.03, float(cfg.get("attach_adaptive_min_motion", 0.0042)))
        )
        self.attach_adaptive_delta = max(
            0.0008, min(0.03, float(cfg.get("attach_adaptive_delta", 0.0020)))
        )
        self.attach_adaptive_multiplier = max(
            1.02, min(3.0, float(cfg.get("attach_adaptive_multiplier", 1.30)))
        )
        self.attach_evidence_required = max(
            1.0, min(8.0, float(cfg.get("attach_evidence_required", 2.0)))
        )
        self.attach_evidence_decay = max(
            0.0, min(1.0, float(cfg.get("attach_evidence_decay", 0.35)))
        )
        self.attach_evidence_window_seconds = max(
            3.0, float(cfg.get("attach_evidence_window_seconds", 14.0))
        )
        self.attach_water_assist_ratio = max(
            0.20, min(0.95, float(cfg.get("attach_water_assist_ratio", 0.55)))
        )
        self.attach_water_assist_required = max(
            1.0,
            min(
                self.attach_evidence_required,
                float(cfg.get("attach_water_assist_required", 1.5)),
            ),
        )

        self.attach_evidence = 0.0
        self.attach_evidence_started_at: float | None = None
        self.attach_last_threshold = self.attach_adaptive_min_motion
        self.attach_last_delta = 0.0
        self.attach_adaptive_hits = 0
        self.attach_adaptive_misses = 0
        self.attach_water_assist_active = False
        self.attach_water_geometry_ratio = 0.0

    def _reset_adaptive_evidence(self, *, hard: bool = False) -> None:
        if hard:
            self.attach_evidence = 0.0
            self.attach_evidence_started_at = None
        else:
            self.attach_evidence = max(
                0.0, self.attach_evidence - self.attach_evidence_decay
            )
            if self.attach_evidence <= 0.0:
                self.attach_evidence_started_at = None

    def _adaptive_probe_threshold(self) -> float:
        return max(
            self.attach_adaptive_min_motion,
            self.attach_probe_baseline_motion * self.attach_adaptive_multiplier
            + self.attach_adaptive_delta,
        )

    def _refresh_attach_water_context(self, ctx: ProfileContext) -> None:
        geometry = self._water_geometry(ctx.frame)
        self.attach_water_geometry_ratio = geometry.largest_component_ratio
        self.attach_water_assist_active = bool(
            geometry.confirmed
            and geometry.candidate_ratio >= self.attach_water_assist_ratio
        )

    def _accept_mid_session_gameplay(
        self, controller: Controller, ctx: ProfileContext
    ) -> str:
        controller.neutral_sticks()
        self._set_phase(JakPhase.GAMEPLAY)
        self.last_gameplay_at = ctx.now
        self.gameplay_assumed_after_opening = True
        self.attach_probe_successes += 1
        self.gameplay_session_established = True
        if self.gameplay_session_established_at is None:
            self.gameplay_session_established_at = ctx.now
        self.lost_gameplay_since = None
        self.current_action = (
            "jak: adaptive camera-response evidence confirmed -> GAMEPLAY"
        )
        self._reset_attach_probe(clear_confirmations=True)
        self._reset_adaptive_evidence(hard=True)
        return self._production_gameplay(controller, ctx)

    def _service_attach_probe(
        self, controller: Controller, ctx: ProfileContext
    ) -> str | None:
        # Only for restarting AutoPilot while PCSX2 is already inside a running
        # session. Full boot ownership remains on title/menu/opening transactions.
        if self.campaign_launch_at is not None or self.phase == JakPhase.GAMEPLAY:
            return None
        if self.runtime_started_at is None:
            return None
        if ctx.now - self.runtime_started_at < self.attach_probe_after_seconds:
            return None

        blocker = self._gameplay_blocker()
        if blocker:
            self._reset_attach_probe(clear_confirmations=True)
            self._reset_adaptive_evidence(hard=True)
            return None

        self._refresh_attach_water_context(ctx)

        # Evidence is intentionally short-lived. A camera response from a much earlier
        # scene cannot combine with a later unrelated pulse to grant ownership.
        if (
            self.attach_evidence_started_at is not None
            and ctx.now - self.attach_evidence_started_at
            > self.attach_evidence_window_seconds
        ):
            self._reset_adaptive_evidence(hard=True)

        if self.attach_probe_stage == "drive":
            self.attach_probe_peak_motion = max(
                self.attach_probe_peak_motion, float(ctx.motion)
            )
            if ctx.now < self.attach_probe_release_at:
                controller.set_right_stick(
                    self.attach_probe_direction * self.attach_probe_camera_x, 0.0
                )
                self._neutralized = False
                self.current_action = "jak: adaptive attach probe; nudge camera"
                return self.current_action
            controller.set_right_stick(0.0, 0.0)
            self.attach_probe_stage = "observe"
            self.attach_probe_deadline = ctx.now + self.attach_probe_observe_seconds
            self.current_action = "jak: adaptive attach probe; observe response"
            return self.current_action

        if self.attach_probe_stage == "observe":
            self.attach_probe_peak_motion = max(
                self.attach_probe_peak_motion, float(ctx.motion)
            )
            threshold = self._adaptive_probe_threshold()
            self.attach_last_threshold = threshold
            self.attach_last_delta = max(
                0.0,
                self.attach_probe_peak_motion - self.attach_probe_baseline_motion,
            )

            if self.attach_probe_peak_motion >= threshold:
                self.attach_adaptive_hits += 1
                if self.attach_evidence_started_at is None:
                    self.attach_evidence_started_at = ctx.now

                # Stronger pulses earn slightly more evidence, capped so a single
                # pathological frame cannot jump directly from zero to ownership.
                strength = self.attach_probe_peak_motion / max(threshold, 1e-6)
                increment = min(
                    1.25, max(0.75, 0.85 + (strength - 1.0) * 0.60)
                )
                self.attach_evidence = min(
                    self.attach_evidence_required + 1.0,
                    self.attach_evidence + increment,
                )
                self.attach_probe_direction *= -1.0

                required = (
                    self.attach_water_assist_required
                    if self.attach_water_assist_active
                    else self.attach_evidence_required
                )
                if self.attach_evidence >= required:
                    return self._accept_mid_session_gameplay(controller, ctx)

                self._reset_attach_probe(clear_confirmations=False)
                self.attach_probe_next_at = ctx.now + self.attach_probe_retry_seconds
                self.current_action = (
                    f"jak: adaptive attach evidence {self.attach_evidence:.2f}/"
                    f"{required:.2f}; probe opposite direction"
                )
                return self.current_action

            if ctx.now < self.attach_probe_deadline:
                self.current_action = (
                    f"jak: adaptive attach waiting; peak={self.attach_probe_peak_motion:.4f} "
                    f"need={threshold:.4f}"
                )
                return self.current_action

            self.attach_adaptive_misses += 1
            self._reset_adaptive_evidence(hard=False)
            self.attach_probe_direction *= -1.0
            self._reset_attach_probe(clear_confirmations=False)
            self.attach_probe_next_at = ctx.now + self.attach_probe_retry_seconds
            self.current_action = (
                f"jak: adaptive attach inconclusive; evidence={self.attach_evidence:.2f}"
            )
            return self.current_action

        if ctx.now < self.attach_probe_next_at:
            return None
        if float(ctx.motion) > self.attach_probe_baseline_max:
            self.attach_probe_idle_since = None
            return None
        if self.attach_probe_idle_since is None:
            self.attach_probe_idle_since = ctx.now
            self.attach_probe_baseline_motion = float(ctx.motion)
            return None
        self.attach_probe_baseline_motion = max(
            self.attach_probe_baseline_motion, float(ctx.motion)
        )
        if ctx.now - self.attach_probe_idle_since < self.attach_probe_idle_seconds:
            return None

        self.attach_probe_attempts += 1
        self.attach_probe_stage = "drive"
        self.attach_probe_peak_motion = 0.0
        self.attach_probe_release_at = ctx.now + self.attach_probe_drive_seconds
        controller.set_right_stick(
            self.attach_probe_direction * self.attach_probe_camera_x, 0.0
        )
        self._neutralized = False
        self.current_action = "jak: adaptive attach probe; nudge camera"
        return self.current_action

    def _service_lost_gameplay_probe(
        self, controller: Controller, ctx: ProfileContext
    ) -> str | None:
        """Reacquire sticky gameplay with the same live-calibrated motion threshold."""
        if not self.gameplay_session_established:
            return None
        if self.phase == JakPhase.GAMEPLAY:
            self._mark_gameplay_session(ctx)
            self._reset_attach_probe(clear_confirmations=True)
            return None

        blocker = self._gameplay_blocker()
        if blocker:
            self.lost_gameplay_since = None
            self._reset_attach_probe(clear_confirmations=True)
            return None

        if self.lost_gameplay_since is None:
            self.lost_gameplay_since = ctx.now
            self._reset_attach_probe(clear_confirmations=True)
            return None
        if ctx.now - self.lost_gameplay_since < self.reacquire_after_seconds:
            return None

        if self.attach_probe_stage == "drive":
            self.attach_probe_peak_motion = max(
                self.attach_probe_peak_motion, float(ctx.motion)
            )
            if ctx.now < self.attach_probe_release_at:
                controller.set_right_stick(
                    self.attach_probe_direction * self.attach_probe_camera_x, 0.0
                )
                self._neutralized = False
                self.current_action = "jak: adaptive lost-gameplay probe; nudge camera"
                return self.current_action
            controller.set_right_stick(0.0, 0.0)
            self.attach_probe_stage = "observe"
            self.current_action = (
                "jak: adaptive lost-gameplay probe; observe camera response"
            )
            return self.current_action

        if self.attach_probe_stage == "observe":
            self.attach_probe_peak_motion = max(
                self.attach_probe_peak_motion, float(ctx.motion)
            )
            threshold = self._adaptive_probe_threshold()
            self.attach_last_threshold = threshold
            self.attach_last_delta = max(
                0.0,
                self.attach_probe_peak_motion - self.attach_probe_baseline_motion,
            )
            if self.attach_probe_peak_motion >= threshold:
                self.attach_probe_confirmations += 1
                if (
                    self.attach_probe_confirmations
                    >= self.reacquire_confirmations_required
                ):
                    self.reacquire_probe_successes += 1
                    self.attach_probe_successes += 1
                    self.attach_probe_direction *= -1.0
                    self.post_reacquire_escape_direction = self.attach_probe_direction
                    self.post_reacquire_escape_until = (
                        ctx.now + self.post_reacquire_escape_seconds
                    )
                    self.post_reacquire_escapes += 1
                    self._set_phase(JakPhase.GAMEPLAY)
                    self.last_gameplay_at = ctx.now
                    self.gameplay_assumed_after_opening = True
                    self._mark_gameplay_session(ctx)
                    self._reset_attach_probe(clear_confirmations=True)
                    return self._post_reacquire_escape(controller, ctx)
                self.attach_probe_direction *= -1.0
                self._reset_attach_probe(clear_confirmations=False)
                self.attach_probe_next_at = (
                    ctx.now + self.attach_probe_retry_seconds
                )
                self.current_action = (
                    f"jak: adaptive lost-gameplay confirmation "
                    f"{self.attach_probe_confirmations}/"
                    f"{self.reacquire_confirmations_required}"
                )
                return self.current_action
            if ctx.now < self.attach_probe_deadline:
                self.current_action = (
                    f"jak: adaptive lost-gameplay waiting; "
                    f"peak={self.attach_probe_peak_motion:.4f} need={threshold:.4f}"
                )
                return self.current_action

            self._reset_attach_probe(clear_confirmations=True)
            self.attach_probe_next_at = ctx.now + self.attach_probe_retry_seconds
            self.current_action = (
                "jak: adaptive lost-gameplay probe inconclusive; hold inputs"
            )
            return self.current_action

        if ctx.now < self.attach_probe_next_at:
            return None
        if float(ctx.motion) > self.attach_probe_baseline_max:
            self.attach_probe_idle_since = None
            return None
        if self.attach_probe_idle_since is None:
            self.attach_probe_idle_since = ctx.now
            self.attach_probe_baseline_motion = float(ctx.motion)
            return None
        self.attach_probe_baseline_motion = max(
            self.attach_probe_baseline_motion, float(ctx.motion)
        )
        if ctx.now - self.attach_probe_idle_since < self.attach_probe_idle_seconds:
            return None
        return self._start_reacquire_probe(controller, ctx)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "jak_policy_version": "v11",
                "jak_attach_evidence": round(self.attach_evidence, 3),
                "jak_attach_last_threshold": round(self.attach_last_threshold, 4),
                "jak_attach_last_delta": round(self.attach_last_delta, 4),
                "jak_attach_adaptive_hits": self.attach_adaptive_hits,
                "jak_attach_adaptive_misses": self.attach_adaptive_misses,
                "jak_attach_water_assist": self.attach_water_assist_active,
                "jak_attach_water_component_ratio": round(
                    self.attach_water_geometry_ratio, 4
                ),
            }
        )
        return state
