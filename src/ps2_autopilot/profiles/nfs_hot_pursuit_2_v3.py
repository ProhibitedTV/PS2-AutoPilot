from __future__ import annotations

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.nfs_hp2_vision import RoadObservation

from .base import ProfileContext
from .nfs_hot_pursuit_2 import NfsPhase
from .nfs_hot_pursuit_2_v2 import (
    NfsHotPursuit2V2Profile,
    NfsScreen,
)


class NfsHotPursuit2V3Profile(NfsHotPursuit2V2Profile):
    """Evidence-stable menus and predictive speed control for Hot Pursuit 2.

    V3 focuses on the two failure modes most likely to appear during the first live
    PCSX2 session:

    * selected-row template latency can make a policy repeat DOWN/UP/Confirm and
      overshoot a menu before capture catches up;
    * a pixel driver that stays at full throttle until it needs a brake tap tends to
      enter bends too quickly and then oscillate while recovering.

    Menu actions therefore become evidence transactions: a selected row must be
    stable, one input is emitted, and the policy waits for visual progress. One
    bounded retry is allowed only when the *same positively recognized row* remains
    visible. Unknown/generic frames never authorize a retry.

    Driving adds brief road-dropout grace, center/curvature rate prediction and
    confidence/turn-demand based coasting before the existing brake/handbrake hooks.
    The profile still remains calibration-first and never invents menu progress.
    """

    name = "nfs_hot_pursuit_2"

    _MENU_SCREENS = {
        NfsScreen.MAIN_HOT_PURSUIT,
        NfsScreen.MAIN_WORLD_RACING,
        NfsScreen.MAIN_OPTIONS,
        NfsScreen.WORLD_QUICK_RACE,
        NfsScreen.WORLD_CHALLENGE,
        NfsScreen.WORLD_CHAMPIONSHIP,
        NfsScreen.HOT_QUICK_RACE,
        NfsScreen.HOT_COP,
        NfsScreen.HOT_CHALLENGE,
        NfsScreen.HOT_ULTIMATE,
        NfsScreen.EVENT_SELECT,
        NfsScreen.CAR_SELECT,
        NfsScreen.RACE_SETUP,
    }
    _GENERIC_MENU_SCREENS = {
        NfsScreen.MAIN_GENERIC,
        NfsScreen.WORLD_GENERIC,
        NfsScreen.HOT_GENERIC,
    }

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        # Template evidence and menu transactions.
        self.menu_stability_frames = max(1, int(cfg.get("menu_stability_frames", 2)))
        self.menu_progress_timeout_seconds = max(
            0.5, float(cfg.get("menu_progress_timeout_seconds", 1.6))
        )
        self.menu_max_retries = max(0, min(2, int(cfg.get("menu_max_retries", 1))))
        self.menu_escape_takeover_seconds = max(
            0.5, float(cfg.get("menu_escape_takeover_seconds", 2.0))
        )

        self.raw_screen = NfsScreen.UNKNOWN
        self.screen_candidate = NfsScreen.UNKNOWN
        self.screen_candidate_frames = 0
        self.screen_stability_accepts = 0
        self.unknown_screen_since: float | None = None

        self.pending_menu_action: str | None = None
        self.pending_menu_screen: NfsScreen | None = None
        self.pending_menu_since = -1e9
        self.pending_menu_retries = 0
        self.blocked_menu_screen: NfsScreen | None = None
        self.menu_progress_events = 0
        self.menu_retry_actions = 0
        self.menu_stalled_transactions = 0
        self.menu_unknown_waits = 0

        # One-shot lifecycle transactions. Once sent, wait for positive visual
        # progress instead of repeating Start/Confirm every menu-action interval.
        self.replay_exit_sent = False
        self.pause_resume_sent = False
        self.results_confirm_sent = False
        self.replay_exit_actions = 0
        self.pause_resume_actions = 0
        self.results_confirm_actions = 0

        # Short road dropout grace protects against one bad segmentation frame.
        self.road_grace_seconds = max(0.0, float(cfg.get("road_grace_seconds", 0.22)))
        self.last_good_road = RoadObservation.unavailable()
        self.last_good_road_at = -1e9
        self.road_grace_fills = 0

        # Predictive steering and speed management.
        self.steering_prediction_seconds = max(
            0.0, min(0.6, float(cfg.get("steering_prediction_seconds", 0.18)))
        )
        self.center_rate_gain = max(0.0, float(cfg.get("center_rate_gain", 1.0)))
        self.curvature_rate_gain = max(0.0, float(cfg.get("curvature_rate_gain", 0.75)))
        self.rate_smoothing = max(0.0, min(0.95, float(cfg.get("rate_smoothing", 0.58))))
        self.corner_coast_threshold = max(
            0.25, min(1.0, float(cfg.get("corner_coast_threshold", 0.70)))
        )
        self.low_confidence_coast_threshold = max(
            self.drive_confidence,
            min(1.0, float(cfg.get("low_confidence_coast_threshold", 0.44))),
        )
        self.edge_correction_start = max(
            0.25, min(0.95, float(cfg.get("edge_correction_start", 0.62)))
        )
        self.edge_correction_gain = max(0.0, float(cfg.get("edge_correction_gain", 0.28)))

        self.last_prediction_at = -1e9
        self.previous_center_x = 0.0
        self.previous_curvature = 0.0
        self.center_rate = 0.0
        self.curvature_rate = 0.0
        self.predicted_center_x = 0.0
        self.predicted_curvature = 0.0
        self.turn_demand = 0.0
        self.coast_ticks = 0
        self.low_confidence_coast_ticks = 0
        self.edge_corrections = 0

    @staticmethod
    def _dynamic_screen(screen: NfsScreen) -> bool:
        return screen in {
            NfsScreen.RACING,
            NfsScreen.COP_RACING,
            NfsScreen.WRONG_WAY,
            NfsScreen.RESULTS,
            NfsScreen.REPLAY,
            NfsScreen.PAUSED,
            NfsScreen.SAVING,
            NfsScreen.BUSTED,
            NfsScreen.LOADING,
            NfsScreen.COUNTDOWN,
            NfsScreen.TITLE,
        }

    def _recognized_screen(self, ctx: ProfileContext) -> NfsScreen:
        template = ctx.template
        if template is None or template.score < self.template_threshold:
            self.last_template = None
            self.last_template_score = 0.0
            raw = NfsScreen.UNKNOWN
        else:
            self.last_template = template.name
            self.last_template_score = float(template.score)
            raw = self._screen_from_template(template.name)

        self.raw_screen = raw
        required = 1 if self._dynamic_screen(raw) else self.menu_stability_frames

        if raw is NfsScreen.UNKNOWN:
            self.screen_candidate = NfsScreen.UNKNOWN
            self.screen_candidate_frames = 0
            self.screen = NfsScreen.UNKNOWN
            return self.screen

        if raw is not self.screen_candidate:
            self.screen_candidate = raw
            self.screen_candidate_frames = 1
        else:
            self.screen_candidate_frames += 1

        if self.screen_candidate_frames >= required:
            if self.screen is not raw:
                self.screen_stability_accepts += 1
            self.screen = raw
            return raw

        # A selected-row candidate is not actionable until it earns stability.
        self.screen = NfsScreen.UNKNOWN
        return self.screen

    def _observe_road(self, ctx: ProfileContext) -> None:
        super()._observe_road(ctx)
        if self.road.confidence >= self.drive_confidence:
            self.last_good_road = self.road
            self.last_good_road_at = ctx.now
            return

        age = ctx.now - self.last_good_road_at
        if (
            self.road_grace_seconds > 0.0
            and 0.0 <= age <= self.road_grace_seconds
            and self.last_good_road.confidence >= self.drive_confidence
            and ctx.motion >= self.race_motion_threshold
        ):
            remaining = max(0.0, 1.0 - age / max(1e-6, self.road_grace_seconds))
            confidence = max(
                self.drive_confidence + 0.005,
                self.last_good_road.confidence * (0.72 + 0.28 * remaining),
            )
            self.road = RoadObservation(
                confidence=min(1.0, confidence),
                center_x=self.last_good_road.center_x,
                curvature=self.last_good_road.curvature,
                width=self.last_good_road.width,
                coverage=self.last_good_road.coverage,
                center_contact=self.last_good_road.center_contact,
            )
            self.road_grace_fills += 1

    def _clear_menu_transaction(self) -> None:
        self.pending_menu_action = None
        self.pending_menu_screen = None
        self.pending_menu_since = -1e9
        self.pending_menu_retries = 0

    def _record_menu_input(
        self,
        controller: Controller,
        ctx: ProfileContext,
        screen: NfsScreen,
        action: str,
        *,
        retry: bool,
    ) -> str:
        ready = ctx.now >= self.next_menu_action_at
        result = self._safe_menu_tap(controller, action, ctx)
        if not ready:
            return result

        self.pending_menu_action = action
        self.pending_menu_screen = screen
        self.pending_menu_since = ctx.now
        if retry:
            self.pending_menu_retries += 1
            self.menu_retry_actions += 1
        else:
            self.pending_menu_retries = 0
            self.menu_route_actions += 1
        return result

    def _menu_tick(self, controller: Controller, ctx: ProfileContext, screen: NfsScreen) -> str:
        # A blocked row stays blocked until positive evidence proves that the cursor
        # moved elsewhere. This prevents an unattended infinite retry loop.
        if self.blocked_menu_screen is not None:
            if screen is self.blocked_menu_screen or screen in self._GENERIC_MENU_SCREENS:
                controller.release_all()
                controller.neutral_sticks()
                self.fail_closed_menu_ticks += 1
                return f"menu blocked awaiting progress: {self.blocked_menu_screen.value}"
            if screen is not NfsScreen.UNKNOWN:
                self.blocked_menu_screen = None

        if self.pending_menu_action is not None and self.pending_menu_screen is not None:
            age = max(0.0, ctx.now - self.pending_menu_since)

            if screen is self.pending_menu_screen:
                if age < self.menu_progress_timeout_seconds:
                    controller.release_all()
                    controller.neutral_sticks()
                    return (
                        f"menu transaction waiting: {self.pending_menu_action} "
                        f"from {screen.value}"
                    )

                action_now = self._route_menu_action(screen)
                if (
                    self.pending_menu_retries < self.menu_max_retries
                    and action_now == self.pending_menu_action
                ):
                    result = self._record_menu_input(
                        controller,
                        ctx,
                        screen,
                        self.pending_menu_action,
                        retry=True,
                    )
                    return f"menu bounded retry: {result}"

                stalled_screen = screen
                self._clear_menu_transaction()
                self.blocked_menu_screen = stalled_screen
                self.menu_stalled_transactions += 1
                self.fail_closed_menu_ticks += 1
                controller.release_all()
                controller.neutral_sticks()
                return f"menu transaction stalled: {stalled_screen.value}; fail-closed"

            if screen is NfsScreen.UNKNOWN or screen in self._GENERIC_MENU_SCREENS:
                # The old selected row disappeared, which may mean progress, but an
                # unlabeled frame does not prove where the cursor landed. Never retry
                # through ambiguity.
                controller.release_all()
                controller.neutral_sticks()
                self.menu_unknown_waits += 1
                if age >= self.menu_progress_timeout_seconds * 2.0:
                    self._clear_menu_transaction()
                    self.fail_closed_menu_ticks += 1
                    return "menu transaction unresolved after ambiguous transition; fail-closed"
                return "menu transaction awaiting positive destination evidence"

            # A different positively recognized screen proves progress. Clear the
            # transaction and permit the route planner to act on the new evidence.
            self._clear_menu_transaction()
            self.menu_progress_events += 1

        action = self._route_menu_action(screen)
        if action is None:
            controller.release_all()
            controller.neutral_sticks()
            self.fail_closed_menu_ticks += 1
            return f"menu fail-closed: screen={screen.value} route={self.route.value}"

        result = self._record_menu_input(controller, ctx, screen, action, retry=False)
        return f"route {self.route.value}: {result}"

    def _special_screen_tick(
        self,
        controller: Controller,
        ctx: ProfileContext,
        screen: NfsScreen,
    ) -> str | None:
        if screen is NfsScreen.REPLAY:
            controller.release_all()
            controller.neutral_sticks()
            if self.replay_visible_since is None:
                self.replay_visible_since = ctx.now
            if self.replay_exit_sent:
                return "replay: exit sent; awaiting visual progress"
            if ctx.now - self.replay_visible_since >= self.replay_hold_seconds:
                ready = ctx.now >= self.next_menu_action_at
                result = self._safe_menu_tap(controller, "start", ctx)
                if ready:
                    self.replay_exit_sent = True
                    self.replay_exit_actions += 1
                return result
            return "replay: preserve broadcast"

        if screen is NfsScreen.PAUSED:
            controller.release_all()
            controller.neutral_sticks()
            if self.pause_visible_since is None:
                self.pause_visible_since = ctx.now
            if self.pause_resume_sent:
                return "pause: resume sent; awaiting visual progress"
            if ctx.now - self.pause_visible_since >= self.pause_resume_seconds:
                ready = ctx.now >= self.next_menu_action_at
                result = self._safe_menu_tap(controller, "start", ctx)
                if ready:
                    self.pause_resume_sent = True
                    self.pause_resume_actions += 1
                return result
            return "pause: bounded hold before resume"

        if screen in {NfsScreen.SAVING, NfsScreen.BUSTED}:
            controller.release_all()
            controller.neutral_sticks()
            return f"{screen.value}: preserve/fail-closed"
        return None

    def _update_prediction(self, ctx: ProfileContext) -> None:
        if self.last_prediction_at > -1e8:
            dt = max(1e-3, min(0.5, ctx.now - self.last_prediction_at))
            raw_center_rate = self._clamp((self.road.center_x - self.previous_center_x) / dt, 4.0)
            raw_curve_rate = self._clamp((self.road.curvature - self.previous_curvature) / dt, 4.0)
            keep = self.rate_smoothing
            self.center_rate = self.center_rate * keep + raw_center_rate * (1.0 - keep)
            self.curvature_rate = self.curvature_rate * keep + raw_curve_rate * (1.0 - keep)

        self.last_prediction_at = ctx.now
        self.previous_center_x = self.road.center_x
        self.previous_curvature = self.road.curvature
        horizon = self.steering_prediction_seconds
        self.predicted_center_x = self._clamp(
            self.road.center_x + self.center_rate * horizon * self.center_rate_gain
        )
        self.predicted_curvature = self._clamp(
            self.road.curvature + self.curvature_rate * horizon * self.curvature_rate_gain
        )

    def _drive(self, controller: Controller, ctx: ProfileContext) -> str:
        if self.road.confidence < self.drive_confidence:
            self.low_motion_since = None
            return super()._drive(controller, ctx)

        if ctx.motion < self.stall_motion_threshold:
            if self.low_motion_since is None:
                self.low_motion_since = ctx.now
            elif ctx.now - self.low_motion_since >= self.stall_seconds:
                self.stall_recoveries += 1
                self.low_motion_since = None
                return self._start_recovery(controller, ctx, "race motion stalled")
        else:
            self.low_motion_since = None

        self.road_lost_since = None
        self._update_prediction(ctx)

        center = self.predicted_center_x
        edge_over = max(0.0, abs(center) - self.edge_correction_start)
        if edge_over > 0.0:
            center += (1.0 if center > 0.0 else -1.0) * edge_over * self.edge_correction_gain
            center = self._clamp(center)
            self.edge_corrections += 1

        raw = self._clamp(
            self.steering_gain * center + self.curvature_gain * self.predicted_curvature,
            self.max_steer,
        )
        if self.last_steer * raw < -0.04 and abs(raw - self.last_steer) >= self.oscillation_delta:
            raw *= self.oscillation_damping
            self.oscillation_damps += 1

        alpha = 1.0 - self.steering_smoothing
        steer = self._clamp(
            self.last_steer * self.steering_smoothing + raw * alpha,
            self.max_steer,
        )
        self.last_steer = steer
        controller.set_left_stick(steer, 0.0)

        self.turn_demand = max(
            abs(steer),
            min(1.0, abs(self.predicted_curvature) * 1.65),
        )
        tags: list[str] = []

        brake_ready = (
            self.turn_demand >= self.corner_brake_threshold
            and ctx.now - self.last_brake_at >= self.corner_brake_interval_seconds
        )
        low_confidence = self.road.confidence < self.low_confidence_coast_threshold
        should_coast = self.turn_demand >= self.corner_coast_threshold or low_confidence

        if brake_ready:
            controller.release(self.accelerate_action)
            controller.tap(self.brake_action, 0.055)
            self.last_brake_at = ctx.now
            tags.append("brake")
        elif should_coast:
            controller.release(self.accelerate_action)
            self.coast_ticks += 1
            if low_confidence:
                self.low_confidence_coast_ticks += 1
                tags.append("confidence-coast")
            else:
                tags.append("coast")
        else:
            controller.hold(self.accelerate_action)

        if (
            self.handbrake_enabled
            and self.turn_demand >= self.handbrake_threshold
            and abs(self.predicted_curvature) >= self.handbrake_curvature
            and self.road.confidence >= 0.55
            and ctx.now - self.last_handbrake_at >= self.handbrake_interval_seconds
        ):
            controller.tap(self.handbrake_action, 0.045)
            self.last_handbrake_at = ctx.now
            self.handbrake_taps += 1
            tags.append("handbrake")

        tags.extend(self._maybe_cop_actions(controller, ctx, steer))
        suffix = " " + ",".join(tags) if tags else ""
        return (
            f"{self.drive_mode} racing: steer={steer:+.2f} "
            f"road={self.road.confidence:.2f} demand={self.turn_demand:.2f}{suffix}"
        )

    def _road_takeover_allowed(self, ctx: ProfileContext, screen: NfsScreen) -> bool:
        if self.race_evidence_frames < self.race_enter_frames:
            return False
        if screen is not NfsScreen.UNKNOWN:
            return False
        if self.phase in {NfsPhase.CALIBRATION, NfsPhase.LOADING, NfsPhase.COUNTDOWN}:
            return True
        if self.phase in {NfsPhase.MAIN_MENU, NfsPhase.RACE_SETUP}:
            return (
                self.unknown_screen_since is not None
                and ctx.now - self.unknown_screen_since >= self.menu_escape_takeover_seconds
            )
        return self.phase is NfsPhase.RACING

    def _reset_positive_progress_guards(self, screen: NfsScreen) -> None:
        # UNKNOWN is not enough evidence to reset a one-shot transaction. A positive
        # different semantic is, which prevents template flicker from causing spam.
        if screen is NfsScreen.UNKNOWN:
            return
        if screen is not NfsScreen.REPLAY:
            self.replay_visible_since = None
            self.replay_exit_sent = False
        if screen is not NfsScreen.PAUSED:
            self.pause_visible_since = None
            self.pause_resume_sent = False
        if screen is not NfsScreen.RESULTS:
            self.results_confirm_sent = False

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        if self.phase_since == 0.0:
            self.phase_since = ctx.now

        screen = self._recognized_screen(ctx)
        self._observe_road(ctx)
        self._reset_positive_progress_guards(screen)

        if screen is NfsScreen.UNKNOWN:
            if self.unknown_screen_since is None:
                self.unknown_screen_since = ctx.now
        else:
            self.unknown_screen_since = None

        if self.phase is NfsPhase.RECOVERY:
            self.last_action = self._tick_recovery(controller, ctx)
            return self.last_action

        if screen is NfsScreen.WRONG_WAY and self.phase is NfsPhase.RACING:
            return self._start_recovery(controller, ctx, "wrong-way HUD")

        special = self._special_screen_tick(controller, ctx, screen)
        if special is not None:
            self.last_action = special
            return special

        if screen in {NfsScreen.RACING, NfsScreen.COP_RACING}:
            self._clear_menu_transaction()
            self.blocked_menu_screen = None
            new_mode = "cop" if screen is NfsScreen.COP_RACING else "racer"
            if new_mode != self.drive_mode:
                self.cop_siren_sent = False
            self.drive_mode = new_mode
            if self.phase is not NfsPhase.RACING:
                self.race_entries += 1
            self._set_phase(NfsPhase.RACING, ctx.now)
        elif screen is NfsScreen.RESULTS:
            self._clear_menu_transaction()
            self._set_phase(NfsPhase.RESULTS, ctx.now)
        elif screen is NfsScreen.LOADING:
            self._clear_menu_transaction()
            self._set_phase(NfsPhase.LOADING, ctx.now)
        elif screen is NfsScreen.COUNTDOWN:
            self._clear_menu_transaction()
            self._set_phase(NfsPhase.COUNTDOWN, ctx.now)
        elif screen in {NfsScreen.MAIN_GENERIC, *self.ROOT_INDEX.keys()}:
            self._set_phase(NfsPhase.MAIN_MENU, ctx.now)
            self.drive_mode = "racer"
            self.cop_siren_sent = False
        elif screen in {
            NfsScreen.WORLD_GENERIC,
            NfsScreen.HOT_GENERIC,
            *self.WORLD_INDEX.keys(),
            *self.HOT_INDEX.keys(),
            NfsScreen.EVENT_SELECT,
            NfsScreen.CAR_SELECT,
            NfsScreen.RACE_SETUP,
        }:
            self._set_phase(NfsPhase.RACE_SETUP, ctx.now)
        elif screen is NfsScreen.TITLE:
            self._clear_menu_transaction()
            self._set_phase(NfsPhase.TITLE, ctx.now)
        elif self._road_takeover_allowed(ctx, screen):
            if self.phase is not NfsPhase.RACING:
                self.race_entries += 1
            self._clear_menu_transaction()
            self.blocked_menu_screen = None
            self.drive_mode = "racer"
            self._set_phase(NfsPhase.RACING, ctx.now)

        if self.phase is NfsPhase.RACING:
            self.last_action = self._drive(controller, ctx)
        elif self.phase is NfsPhase.TITLE:
            self.last_action = self._safe_menu_tap(controller, "start", ctx)
        elif self.phase in {NfsPhase.MAIN_MENU, NfsPhase.RACE_SETUP}:
            self.last_action = self._menu_tick(controller, ctx, screen)
        elif self.phase is NfsPhase.RESULTS:
            controller.release_all()
            controller.neutral_sticks()
            if self.results_visible_since is None:
                self.results_visible_since = ctx.now
            if self.results_confirm_sent:
                self.last_action = "results: confirm sent; awaiting visual progress"
            elif ctx.now - self.results_visible_since >= self.results_hold_seconds:
                ready = ctx.now >= self.next_menu_action_at
                self.last_action = self._safe_menu_tap(controller, "confirm", ctx)
                if ready:
                    self.results_confirm_sent = True
                    self.results_confirm_actions += 1
            else:
                self.last_action = "results: preserve presentation"
        elif self.phase in {NfsPhase.LOADING, NfsPhase.COUNTDOWN}:
            controller.release_all()
            controller.neutral_sticks()
            self.last_action = f"{self.phase.value}: preserve presentation"
        else:
            controller.release_all()
            controller.neutral_sticks()
            self.last_action = "calibration: fail-closed awaiting template or road evidence"
        return self.last_action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        pending_age = None
        if self.pending_menu_action is not None:
            pending_age = round(max(0.0, ctx.now - self.pending_menu_since), 2)
        state.update(
            {
                "nfs_policy_version": 3,
                "nfs_raw_screen": self.raw_screen.value,
                "nfs_screen_candidate": self.screen_candidate.value,
                "nfs_screen_candidate_frames": self.screen_candidate_frames,
                "nfs_screen_stability_accepts": self.screen_stability_accepts,
                "nfs_pending_menu_action": self.pending_menu_action,
                "nfs_pending_menu_screen": None
                if self.pending_menu_screen is None
                else self.pending_menu_screen.value,
                "nfs_pending_menu_age": pending_age,
                "nfs_pending_menu_retries": self.pending_menu_retries,
                "nfs_blocked_menu_screen": None
                if self.blocked_menu_screen is None
                else self.blocked_menu_screen.value,
                "nfs_menu_progress_events": self.menu_progress_events,
                "nfs_menu_retry_actions": self.menu_retry_actions,
                "nfs_menu_stalled_transactions": self.menu_stalled_transactions,
                "nfs_menu_unknown_waits": self.menu_unknown_waits,
                "nfs_replay_exit_actions": self.replay_exit_actions,
                "nfs_pause_resume_actions": self.pause_resume_actions,
                "nfs_results_confirm_actions": self.results_confirm_actions,
                "nfs_road_grace_fills": self.road_grace_fills,
                "nfs_center_rate": round(self.center_rate, 3),
                "nfs_curvature_rate": round(self.curvature_rate, 3),
                "nfs_predicted_center_x": round(self.predicted_center_x, 3),
                "nfs_predicted_curvature": round(self.predicted_curvature, 3),
                "nfs_turn_demand": round(self.turn_demand, 3),
                "nfs_coast_ticks": self.coast_ticks,
                "nfs_low_confidence_coast_ticks": self.low_confidence_coast_ticks,
                "nfs_edge_corrections": self.edge_corrections,
            }
        )
        return state
