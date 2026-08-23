from __future__ import annotations

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .jak_and_daxter import JakPhase
from .jak_and_daxter_v7 import JakAndDaxterV7Profile


class JakAndDaxterV8Profile(JakAndDaxterV7Profile):
    """Harden gameplay ownership after the first live autonomous traversal.

    The live V6/V7 calibration run exposed two related failures once Jak had already
    reached playable Geyser Rock:

    * foliage in the calibrated NEW GAME ROI could satisfy the V4 visual fallback and
      steal controller ownership from gameplay, repeatedly pressing Cross;
    * once a static/occluded gameplay view fell out of the short gameplay grace window,
      the fail-closed watchdog could only remain neutral forever.

    V8 makes gameplay-session ownership sticky, disables visual-only boot-menu
    detection after gameplay has been established, and uses a camera-response probe
    to safely reacquire a lost/static gameplay state. The probe does not move Jak and
    is ignored by cutscenes. Only after a measurable camera response does V8 perform
    a short back-and-turn escape from the occluder before returning to normal V7
    shoreline-aware traversal.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.gameplay_session_established = False
        self.gameplay_session_established_at: float | None = None

        self.lost_gameplay_since: float | None = None
        self.reacquire_after_seconds = max(
            0.8, float(cfg.get("reacquire_after_seconds", 2.0))
        )
        self.reacquire_confirmations_required = max(
            1, min(3, int(cfg.get("reacquire_confirmations_required", 1)))
        )
        self.reacquire_probe_attempts = 0
        self.reacquire_probe_successes = 0
        self.reacquire_visual_menu_suppressions = 0

        self.post_reacquire_escape_seconds = max(
            0.5, float(cfg.get("post_reacquire_escape_seconds", 1.35))
        )
        self.post_reacquire_back_speed = max(
            0.25, min(0.95, float(cfg.get("post_reacquire_back_speed", 0.64)))
        )
        self.post_reacquire_turn = max(
            0.25, min(0.95, float(cfg.get("post_reacquire_turn", 0.60)))
        )
        self.post_reacquire_camera = max(
            0.10, min(0.70, float(cfg.get("post_reacquire_camera", 0.32)))
        )
        self.post_reacquire_escape_until = 0.0
        self.post_reacquire_escape_direction = 1.0
        self.post_reacquire_escapes = 0

    def _mark_gameplay_session(self, ctx: ProfileContext) -> None:
        if not self.gameplay_session_established:
            self.gameplay_session_established = True
            self.gameplay_session_established_at = ctx.now
        self.lost_gameplay_since = None

    def _should_suppress_visual_main_menu(self) -> bool:
        return bool(
            self.gameplay_session_established
            and self.main_menu_visible
            and self.main_menu_detection_source == "visual-fallback"
            and self.main_menu_ocr_markers < self.main_menu_ocr_quorum
        )

    def _read_ocr_title_gate(self, ctx: ProfileContext) -> bool:
        visible = super()._read_ocr_title_gate(ctx)
        if not self._should_suppress_visual_main_menu():
            return visible

        # The calibrated green NEW GAME ROI is useful during boot, but a jungle scene
        # can contain the same hue distribution. Once gameplay has ever been proven,
        # visual-only evidence may no longer impersonate the boot menu. Strong OCR can
        # still identify a real menu after gameplay, and unknown states remain neutral.
        self.main_menu_visible = False
        self.new_game_selected = False
        self.new_game_green_ratio = 0.0
        self.main_menu_detection_source = "post-gameplay-visual-suppressed"
        self.reacquire_visual_menu_suppressions += 1
        return self.title_gate_visible or self.save_prompt_visible or self.save_file_selector_visible

    def _start_reacquire_probe(self, controller: Controller, ctx: ProfileContext) -> str:
        self.reacquire_probe_attempts += 1
        self.attach_probe_attempts += 1
        self.attach_probe_stage = "drive"
        self.attach_probe_peak_motion = 0.0
        self.attach_probe_baseline_motion = float(ctx.motion)
        self.attach_probe_release_at = ctx.now + self.attach_probe_drive_seconds
        self.attach_probe_deadline = self.attach_probe_release_at + self.attach_probe_observe_seconds
        controller.set_right_stick(self.attach_probe_direction * self.attach_probe_camera_x, 0.0)
        self._neutralized = False
        self.current_action = "jak: lost-gameplay probe; nudge camera only"
        return self.current_action

    def _service_lost_gameplay_probe(
        self, controller: Controller, ctx: ProfileContext
    ) -> str | None:
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
            self.attach_probe_peak_motion = max(self.attach_probe_peak_motion, float(ctx.motion))
            if ctx.now < self.attach_probe_release_at:
                controller.set_right_stick(
                    self.attach_probe_direction * self.attach_probe_camera_x, 0.0
                )
                self._neutralized = False
                self.current_action = "jak: lost-gameplay probe; nudge camera only"
                return self.current_action
            controller.set_right_stick(0.0, 0.0)
            self.attach_probe_stage = "observe"
            self.current_action = "jak: lost-gameplay probe; observe camera response"
            return self.current_action

        if self.attach_probe_stage == "observe":
            self.attach_probe_peak_motion = max(self.attach_probe_peak_motion, float(ctx.motion))
            threshold = max(
                self.attach_probe_response_min,
                self.attach_probe_baseline_motion * 1.6 + 0.004,
            )
            if self.attach_probe_peak_motion >= threshold:
                self.attach_probe_confirmations += 1
                if self.attach_probe_confirmations >= self.reacquire_confirmations_required:
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
                self.attach_probe_next_at = ctx.now + self.attach_probe_retry_seconds
                self.current_action = (
                    f"jak: lost-gameplay probe confirmation {self.attach_probe_confirmations}/"
                    f"{self.reacquire_confirmations_required}"
                )
                return self.current_action
            if ctx.now < self.attach_probe_deadline:
                self.current_action = "jak: lost-gameplay probe; waiting for camera motion"
                return self.current_action

            # No camera response: treat this as a likely presentation/menu/static state.
            # Do not move Jak. Try another reversible camera probe later.
            self._reset_attach_probe(clear_confirmations=True)
            self.attach_probe_next_at = ctx.now + self.attach_probe_retry_seconds
            self.current_action = "jak: lost-gameplay probe inconclusive; hold inputs"
            return self.current_action

        if ctx.now < self.attach_probe_next_at:
            return None
        if float(ctx.motion) > self.attach_probe_baseline_max:
            # A moving unknown scene is more likely presentation; wait for it to settle
            # before applying the reversible camera probe.
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

    def _post_reacquire_escape(self, controller: Controller, ctx: ProfileContext) -> str:
        direction = self.post_reacquire_escape_direction
        self.second_jump_pending = False
        controller.set_left_stick(direction * self.post_reacquire_turn, -self.post_reacquire_back_speed)
        controller.set_right_stick(-direction * self.post_reacquire_camera, 0.0)
        self._neutralized = False
        self.current_action = (
            "jak: gameplay reacquired; back-turn away from camera occluder"
        )
        return self.current_action

    def _production_gameplay(self, controller: Controller, ctx: ProfileContext) -> str:
        self._mark_gameplay_session(ctx)
        if ctx.now < self.post_reacquire_escape_until:
            # Still refresh scene/water telemetry while performing the bounded escape.
            self.scene_metrics = self.scene_memory.observe(ctx.frame, ctx.previous_frame, ctx.now)
            self.last_gameplay_at = ctx.now
            self._refresh_water_state(ctx)
            if self.water_escape_active:
                self.post_reacquire_escape_until = 0.0
                return self._water_escape(controller, ctx)
            return self._post_reacquire_escape(controller, ctx)
        return super()._production_gameplay(controller, ctx)

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        action = super().tick(controller, ctx)
        if self.mode != "production":
            return action

        if self.phase == JakPhase.GAMEPLAY:
            self._mark_gameplay_session(ctx)
            return action

        probe_action = self._service_lost_gameplay_probe(controller, ctx)
        return probe_action if probe_action is not None else action

    def recover(self, controller: Controller) -> str:
        # The runtime watchdog has no frame context. If a previously proven gameplay
        # session temporarily looks unknown, V8's tick-level camera probe owns
        # reacquisition. The watchdog must not inject generic Start/confirm/cancel.
        if self.gameplay_session_established and self.phase != JakPhase.GAMEPLAY:
            controller.release_all()
            controller.neutral_sticks()
            self.current_action = "jak: watchdog hold; V8 camera probe owns reacquisition"
            return self.current_action
        return super().recover(controller)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "jak_policy_version": "v8",
                "jak_gameplay_session_established": self.gameplay_session_established,
                "jak_gameplay_session_established_at": self.gameplay_session_established_at,
                "jak_lost_gameplay_since": self.lost_gameplay_since,
                "jak_reacquire_probe_attempts": self.reacquire_probe_attempts,
                "jak_reacquire_probe_successes": self.reacquire_probe_successes,
                "jak_visual_menu_suppressions": self.reacquire_visual_menu_suppressions,
                "jak_post_reacquire_escapes": self.post_reacquire_escapes,
                "jak_post_reacquire_escape_active": ctx.now < self.post_reacquire_escape_until,
            }
        )
        return state
