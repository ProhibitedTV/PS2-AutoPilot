from __future__ import annotations

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.nfs_hp2_hud import estimate_gameplay_hud
from ps2_autopilot.nfs_hp2_vision import RoadObservation

from .base import ProfileContext
from .nfs_hot_pursuit_2 import NfsPhase
from .nfs_hot_pursuit_2_v2 import NfsScreen
from .nfs_hot_pursuit_2_v8 import NfsHotPursuit2V8Profile


class NfsHotPursuit2V9Profile(NfsHotPursuit2V8Profile):
    """V9: adaptive racecraft, smarter wall escape and broadcast showmanship.

    V8 closes the eight-hour wall-stall loop with fixed-HUD ownership, alternating
    recovery and a bounded Restart Race fallback. V9 makes the actual racing policy
    less brittle and the stream less repetitive without trading away those safety
    boundaries:

    * strong fixed-HUD ownership can extend the last coherent road solution through
      short texture/shadow dropouts, but only while the scene is meaningfully moving;
    * the first wall-recovery attempts use the last known road-center direction to
      steer back toward the course instead of alternating blindly;
    * Restart Race visual success is measured *after* the restart confirmation, not
      from the earlier pause-menu animation;
    * repeated Restart Race failures escalate to the documented Pause -> Quit Race
      path so a 24/7 stream has a terminal escape from a poisoned race state;
    * replay presentation length cycles through a small broadcast cadence;
    * racer modes can make an occasional safe straight-line horn flourish. Cop mode
      never uses the flourish because Circle has pursuit semantics there.

    All entertainment inputs are bounded and subordinate to racing/recovery/hazard
    ownership. Camera switching remains intentionally disabled because it would alter
    the chase-camera vision geometry that the driving stack depends on.
    """

    name = "nfs_hot_pursuit_2"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        # HUD-backed road memory. V3 already provides a very short generic grace
        # window. V9 extends it only when the fixed gameplay HUD is strong and the
        # scene is moving enough that a stationary wall trap cannot inherit stale road.
        self.hud_road_memory_seconds = max(
            self.road_grace_seconds,
            min(1.5, float(cfg.get("hud_road_memory_seconds", 0.85))),
        )
        self.hud_road_memory_min_motion = max(
            self.race_motion_threshold,
            float(cfg.get("hud_road_memory_min_motion", 0.012)),
        )
        self.hud_road_memory_fills = 0

        # Use the last coherent road center as a directional hint for the first
        # couple of wall escapes. Later attempts fall back to V4/V8 alternation so a
        # stale road solution cannot repeat the same failed maneuver forever.
        self.recovery_center_hint_seconds = max(
            0.5, float(cfg.get("recovery_center_hint_seconds", 5.0))
        )
        self.recovery_center_hint_min_abs = max(
            0.05, min(0.8, float(cfg.get("recovery_center_hint_min_abs", 0.14)))
        )
        self.recovery_center_hint_max_streak = max(
            1, int(cfg.get("recovery_center_hint_max_streak", 2))
        )
        self.recovery_center_guided = 0

        # Terminal race escape. Restart Race remains the preferred fallback. Only
        # repeated restart failures arm Quit Race, which returns lifecycle ownership
        # to the existing unattended menu bootstrap rather than parking forever.
        self.hard_quit_enabled = bool(cfg.get("hard_quit_enabled", True))
        self.hard_quit_after_restart_failures = max(
            1, int(cfg.get("hard_quit_after_restart_failures", 2))
        )
        self.hard_quit_action_seconds = max(
            0.30, float(cfg.get("hard_quit_action_seconds", 0.55))
        )
        self.hard_quit_wait_seconds = max(
            2.0, float(cfg.get("hard_quit_wait_seconds", 8.0))
        )
        self.hard_quit_progress_delta = max(
            0.005, min(0.30, float(cfg.get("hard_quit_progress_delta", 0.025)))
        )
        self.hard_quit_cooldown_seconds = max(
            5.0, float(cfg.get("hard_quit_cooldown_seconds", 30.0))
        )

        self.consecutive_restart_failures = 0
        self.hard_quit_armed = False
        self.hard_quit_stage: str | None = None
        self.hard_quit_stage_since = -1e9
        self.hard_quit_reference = None
        self.hard_quit_down_remaining = 0
        self.hard_quit_next_allowed_at = -1e9
        self.hard_quit_attempts = 0
        self.hard_quit_inputs = 0
        self.hard_quit_successes = 0
        self.hard_quit_failures = 0

        # Broadcast pacing: preserve some replays longer than others so a 24/7 feed
        # is not an identical race->8s replay->race cadence forever.
        configured_holds = cfg.get("broadcast_replay_holds", [6.0, 10.0, 14.0])
        holds: list[float] = []
        if isinstance(configured_holds, (list, tuple)):
            for value in configured_holds:
                try:
                    holds.append(max(2.0, min(30.0, float(value))))
                except (TypeError, ValueError):
                    continue
        self.broadcast_replay_holds = tuple(holds) or (6.0, 10.0, 14.0)
        self.broadcast_replay_variation_enabled = bool(
            cfg.get("broadcast_replay_variation_enabled", True)
        )
        self.broadcast_replay_hold = self.replay_hold_seconds

        # Harmless showmanship on clean straights. Circle is horn for racer modes but
        # target/siren in You're The Cop, so cop mode is explicitly excluded.
        self.showmanship_horn_enabled = bool(cfg.get("showmanship_horn_enabled", True))
        self.showmanship_horn_action = str(cfg.get("showmanship_horn_action", "circle"))
        self.showmanship_horn_interval_seconds = max(
            15.0, float(cfg.get("showmanship_horn_interval_seconds", 45.0))
        )
        self.showmanship_horn_warmup_seconds = max(
            3.0, float(cfg.get("showmanship_horn_warmup_seconds", 12.0))
        )
        self.showmanship_horn_min_road_confidence = max(
            self.drive_confidence,
            min(1.0, float(cfg.get("showmanship_horn_min_road_confidence", 0.68))),
        )
        self.showmanship_horn_max_steer = max(
            0.05, min(0.50, float(cfg.get("showmanship_horn_max_steer", 0.22)))
        )
        self.showmanship_horn_max_curvature = max(
            0.02, min(0.50, float(cfg.get("showmanship_horn_max_curvature", 0.10)))
        )
        self.showmanship_horn_min_motion = max(
            self.race_motion_threshold,
            float(cfg.get("showmanship_horn_min_motion", 0.012)),
        )
        self.last_showmanship_horn_at = -1e9
        self.showmanship_horns = 0

    def _observe_road(self, ctx: ProfileContext) -> None:
        super()._observe_road(ctx)
        if self.road.confidence >= self.drive_confidence:
            return
        if not self._hud_owned() or ctx.motion < self.hud_road_memory_min_motion:
            return

        age = ctx.now - self.last_good_road_at
        if not (0.0 <= age <= self.hud_road_memory_seconds):
            return
        if self.last_good_road.confidence < self.drive_confidence:
            return

        remaining = max(0.0, 1.0 - age / max(1e-6, self.hud_road_memory_seconds))
        confidence = max(
            self.drive_confidence + 0.005,
            self.last_good_road.confidence * (0.60 + 0.40 * remaining),
        )
        # Slightly decay the remembered bend toward straight ahead as it ages. This
        # bridges pavement-texture dropouts without holding a stale hard turn.
        bend_keep = 0.72 + 0.28 * remaining
        self.road = RoadObservation(
            confidence=min(1.0, confidence),
            center_x=self.last_good_road.center_x * bend_keep,
            curvature=self.last_good_road.curvature * bend_keep,
            width=self.last_good_road.width,
            coverage=self.last_good_road.coverage,
            center_contact=self.last_good_road.center_contact,
        )
        self.hud_road_memory_fills += 1

    def _start_recovery(self, controller: Controller, ctx: ProfileContext, reason: str) -> str:
        action = super()._start_recovery(controller, ctx, reason)
        if self.phase is not NfsPhase.RECOVERY:
            return action
        if self.recovery_streak > self.recovery_center_hint_max_streak:
            return action

        age = ctx.now - self.last_good_road_at
        center = self.last_good_road.center_x
        if not (0.0 <= age <= self.recovery_center_hint_seconds):
            return action
        if abs(center) < self.recovery_center_hint_min_abs:
            return action

        # Recovery forward steering is -recovery_direction in V4. Therefore a road
        # center to the right wants a negative reverse direction, then a positive
        # forward direction back toward the course (and vice versa).
        desired_forward = 1.0 if center > 0.0 else -1.0
        self.recovery_direction = -desired_forward
        self.recovery_center_guided += 1
        side = "right" if desired_forward > 0.0 else "left"
        return f"{action}; center-guided forward={side}"

    def _finish_hard_restart(self, ctx: ProfileContext, *, progressed: bool) -> str:
        action = super()._finish_hard_restart(ctx, progressed=progressed)
        if progressed:
            self.consecutive_restart_failures = 0
            self.hard_quit_armed = False
            return action

        self.consecutive_restart_failures += 1
        if (
            self.hard_quit_enabled
            and self.consecutive_restart_failures >= self.hard_quit_after_restart_failures
            and ctx.now >= self.hard_quit_next_allowed_at
        ):
            self.hard_quit_armed = True
            return f"{action}; Quit Race escalation armed"
        return action

    def _tick_hard_restart(self, controller: Controller, ctx: ProfileContext) -> str:
        """V8 restart ladder with post-confirm visual acknowledgement.

        V8's reference frame was captured before opening Pause, so merely opening the
        pause menu could mark the whole restart as a visual success. V9 resets the
        reference after the final restart confirmation; only a subsequent change can
        count as restart progress.
        """
        controller.release_all()
        controller.neutral_sticks()
        current_fp = self._fingerprint(ctx.frame)
        age = max(0.0, ctx.now - self.hard_restart_stage_since)

        if self.hard_restart_stage == "pause":
            if age < self.hard_restart_action_seconds:
                return "hud hard-stall restart: wait for pause menu"
            controller.tap("down", 0.08)
            self.hard_restart_inputs += 1
            self.hard_restart_stage = "restart_selected"
            self.hard_restart_stage_since = ctx.now
            return "hud hard-stall restart: select Restart Race"

        if self.hard_restart_stage == "restart_selected":
            if age < self.hard_restart_action_seconds:
                return "hud hard-stall restart: settle restart selection"
            controller.tap("confirm", 0.08)
            self.hard_restart_inputs += 1
            self.hard_restart_stage = "restart_confirm"
            self.hard_restart_stage_since = ctx.now
            return "hud hard-stall restart: confirm Restart Race"

        if self.hard_restart_stage == "restart_confirm":
            if age < self.hard_restart_action_seconds:
                return "hud hard-stall restart: wait for restart confirmation"
            controller.tap("confirm", 0.08)
            self.hard_restart_inputs += 1
            self.hard_restart_stage = "wait_restart"
            self.hard_restart_stage_since = ctx.now
            self.hard_restart_reference = current_fp
            self.hard_restart_progress_seen = False
            return "hud hard-stall restart: bounded second confirm; verify post-confirm progress"

        if self.hard_restart_stage == "wait_restart":
            delta = self._fingerprint_delta(self.hard_restart_reference, current_fp)
            if delta >= self.hard_restart_progress_delta:
                self.hard_restart_progress_seen = True
            if self.hard_restart_progress_seen and age >= 1.25:
                return self._finish_hard_restart(ctx, progressed=True)
            if age >= self.hard_restart_wait_seconds:
                return self._finish_hard_restart(ctx, progressed=False)
            return (
                "hud hard-stall restart: awaiting post-confirm loading/countdown "
                f"delta={delta:.3f}"
            )

        return self._finish_hard_restart(ctx, progressed=False)

    def _begin_hard_quit(self, controller: Controller, ctx: ProfileContext) -> str:
        controller.release_all()
        controller.neutral_sticks()
        controller.tap("start", 0.08)
        self.hard_quit_stage = "pause"
        self.hard_quit_stage_since = ctx.now
        self.hard_quit_reference = None
        self.hard_quit_down_remaining = 5
        self.hard_quit_armed = False
        self.hard_quit_attempts += 1
        self.hard_quit_inputs += 1
        return (
            "hud terminal escape: tap start for Quit Race "
            f"restart_failures={self.consecutive_restart_failures}"
        )

    def _finish_hard_quit(self, ctx: ProfileContext, *, progressed: bool) -> str:
        self.hard_quit_stage = None
        self.hard_quit_stage_since = -1e9
        self.hard_quit_reference = None
        self.hard_quit_down_remaining = 0
        self.hard_quit_armed = False
        self.hard_quit_next_allowed_at = ctx.now + self.hard_quit_cooldown_seconds
        self.consecutive_restart_failures = 0
        self.hud_stall_since = None
        self.hud_stall_recovery_attempts = 0
        self.road_lost_since = None
        self._set_phase(NfsPhase.CALIBRATION, ctx.now)
        if progressed:
            self.hard_quit_successes += 1
            return "hud terminal escape: Quit Race progressed; hand back to menu lifecycle"
        self.hard_quit_failures += 1
        return "hud terminal escape: Quit Race no progress; hand back to reacquisition"

    def _tick_hard_quit(self, controller: Controller, ctx: ProfileContext) -> str:
        controller.release_all()
        controller.neutral_sticks()
        current_fp = self._fingerprint(ctx.frame)
        age = max(0.0, ctx.now - self.hard_quit_stage_since)

        if self.hard_quit_stage == "pause":
            if age < self.hard_quit_action_seconds:
                return "hud terminal escape: wait for pause menu"
            controller.tap("down", 0.08)
            self.hard_quit_inputs += 1
            self.hard_quit_down_remaining = 4
            self.hard_quit_stage = "down_to_quit"
            self.hard_quit_stage_since = ctx.now
            return "hud terminal escape: move toward Quit Race 1/5"

        if self.hard_quit_stage == "down_to_quit":
            if age < self.hard_quit_action_seconds:
                completed = 5 - self.hard_quit_down_remaining
                return f"hud terminal escape: settle menu move {completed}/5"
            controller.tap("down", 0.08)
            self.hard_quit_inputs += 1
            self.hard_quit_down_remaining -= 1
            completed = 5 - self.hard_quit_down_remaining
            self.hard_quit_stage_since = ctx.now
            if self.hard_quit_down_remaining <= 0:
                self.hard_quit_stage = "quit_selected"
                return "hud terminal escape: Quit Race selected"
            return f"hud terminal escape: move toward Quit Race {completed}/5"

        if self.hard_quit_stage == "quit_selected":
            if age < self.hard_quit_action_seconds:
                return "hud terminal escape: settle Quit Race selection"
            controller.tap("confirm", 0.08)
            self.hard_quit_inputs += 1
            self.hard_quit_stage = "quit_confirm"
            self.hard_quit_stage_since = ctx.now
            return "hud terminal escape: confirm Quit Race"

        if self.hard_quit_stage == "quit_confirm":
            if age < self.hard_quit_action_seconds:
                return "hud terminal escape: wait for quit confirmation"
            controller.tap("confirm", 0.08)
            self.hard_quit_inputs += 1
            self.hard_quit_stage = "wait_quit"
            self.hard_quit_stage_since = ctx.now
            self.hard_quit_reference = current_fp
            return "hud terminal escape: bounded second confirm; verify menu return"

        if self.hard_quit_stage == "wait_quit":
            delta = self._fingerprint_delta(self.hard_quit_reference, current_fp)
            if delta >= self.hard_quit_progress_delta and age >= 1.25:
                return self._finish_hard_quit(ctx, progressed=True)
            if age >= self.hard_quit_wait_seconds:
                return self._finish_hard_quit(ctx, progressed=False)
            return f"hud terminal escape: awaiting menu return delta={delta:.3f}"

        return self._finish_hard_quit(ctx, progressed=False)

    def _special_screen_tick(
        self,
        controller: Controller,
        ctx: ProfileContext,
        screen: NfsScreen,
    ) -> str | None:
        if screen is NfsScreen.REPLAY and self.broadcast_replay_variation_enabled:
            index = max(0, self.race_entries - 1) % len(self.broadcast_replay_holds)
            self.broadcast_replay_hold = self.broadcast_replay_holds[index]
            self.replay_hold_seconds = self.broadcast_replay_hold
        return super()._special_screen_tick(controller, ctx, screen)

    def _showmanship_safe(self, ctx: ProfileContext) -> bool:
        if not self.showmanship_horn_enabled or self.phase is not NfsPhase.RACING:
            return False
        if self.drive_mode == "cop":
            return False
        if self.hard_restart_stage is not None or self.hard_quit_stage is not None:
            return False
        if ctx.now - self.phase_since < self.showmanship_horn_warmup_seconds:
            return False
        if ctx.now - self.last_showmanship_horn_at < self.showmanship_horn_interval_seconds:
            return False
        if ctx.now - self.last_good_road_at > max(0.30, self.road_grace_seconds):
            return False
        if self.road.confidence < self.showmanship_horn_min_road_confidence:
            return False
        if ctx.motion < self.showmanship_horn_min_motion:
            return False
        if abs(self.last_steer) > self.showmanship_horn_max_steer:
            return False
        if abs(self.road.curvature) > self.showmanship_horn_max_curvature:
            return False
        if ctx.now <= self.template_hazard_until or ctx.now <= self.pursuit_threat_until:
            return False
        if self._shortcut_active(ctx.now):
            return False
        if self.obstacle_avoid_enabled and self.hazard_track_confirmed:
            return False
        return True

    def _maybe_showmanship_horn(self, controller: Controller, ctx: ProfileContext) -> bool:
        if not self._showmanship_safe(ctx):
            return False
        controller.tap(self.showmanship_horn_action, 0.04)
        self.last_showmanship_horn_at = ctx.now
        self.showmanship_horns += 1
        return True

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        if self.hard_quit_stage is not None:
            self.hud = estimate_gameplay_hud(ctx.frame)
            self.last_action = self._tick_hard_quit(controller, ctx)
            return self.last_action

        if self.hard_quit_armed and ctx.now >= self.hard_quit_next_allowed_at:
            self.hud = estimate_gameplay_hud(ctx.frame)
            if self._hud_owned():
                self.last_action = self._begin_hard_quit(controller, ctx)
                return self.last_action

        action = super().tick(controller, ctx)
        if self.phase is NfsPhase.RACING and self._maybe_showmanship_horn(controller, ctx):
            action = f"{action} showmanship=horn"
            self.last_action = action
        return action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "nfs_policy_version": 9,
                "nfs_hud_road_memory_seconds": round(self.hud_road_memory_seconds, 2),
                "nfs_hud_road_memory_fills": self.hud_road_memory_fills,
                "nfs_recovery_center_guided": self.recovery_center_guided,
                "nfs_consecutive_restart_failures": self.consecutive_restart_failures,
                "nfs_hard_quit_enabled": self.hard_quit_enabled,
                "nfs_hard_quit_armed": self.hard_quit_armed,
                "nfs_hard_quit_stage": self.hard_quit_stage,
                "nfs_hard_quit_attempts": self.hard_quit_attempts,
                "nfs_hard_quit_inputs": self.hard_quit_inputs,
                "nfs_hard_quit_successes": self.hard_quit_successes,
                "nfs_hard_quit_failures": self.hard_quit_failures,
                "nfs_broadcast_replay_hold": round(self.broadcast_replay_hold, 2),
                "nfs_showmanship_horns": self.showmanship_horns,
                "nfs_showmanship_horn_enabled": self.showmanship_horn_enabled,
            }
        )
        return state
