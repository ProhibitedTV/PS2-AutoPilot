from __future__ import annotations

import random
import time

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.jak_knowledge import JakControlMode, JakProgression, control_mode_for_template
from ps2_autopilot.jak_perception import JakSceneMemory, merge_progress, parse_progress_ocr

from .base import ProfileContext
from .jak_and_daxter import JakPhase
from .jak_and_daxter_v2 import JakAndDaxterV2Profile


class JakAndDaxterV3Profile(JakAndDaxterV2Profile):
    """Research-driven production candidate for The Precursor Legacy.

    V2 owns the verified boot gates. V3 adds the runtime structure required by the
    actual game: continuous analog exploration, a camera, distinct vehicle/animal/
    machine control modes, persistent collectible progress, cutscene preservation,
    short-term visual memory and gameplay-only recovery. Unknown menus still fail
    closed.

    This is deliberately a production *candidate*: exact ledge/enemy/collectible
    detectors remain calibration work, so no policy is allowed to infer a dangerous
    button from an unknown static screen.
    """

    def __init__(self, cfg: dict) -> None:
        requested_mode = str(cfg.get("mode", "production")).strip().lower()
        if requested_mode not in {"observe", "explore", "production"}:
            raise ValueError("Jak profile mode must be 'observe', 'explore', or 'production'")

        parent_cfg = dict(cfg)
        # V1/V2 predate production mode; their explore path is the appropriate
        # inheritance behavior after V3 has made an explicit gameplay decision.
        parent_cfg["mode"] = "explore" if requested_mode == "production" else requested_mode
        super().__init__(parent_cfg)
        self.mode = requested_mode

        self.production_random = random.Random(int(cfg.get("production_random_seed", 2001)))
        self.scene_memory = JakSceneMemory(
            sample_seconds=float(cfg.get("scene_memory_sample_seconds", 1.25)),
            history=int(cfg.get("scene_memory_history", 48)),
        )
        self.scene_metrics = self.scene_memory.last_metrics

        self.control_mode = JakControlMode.UNKNOWN
        self.last_gameplay_at = -1e9
        self.gameplay_grace_seconds = max(1.0, float(cfg.get("gameplay_grace_seconds", 8.0)))
        self.opening_cinematic_hold_seconds = max(
            0.0, float(cfg.get("opening_cinematic_hold_seconds", 390.0))
        )
        self.campaign_launch_at: float | None = None
        self.gameplay_assumed_after_opening = False

        self.production_forward = max(0.2, min(1.0, float(cfg.get("production_forward", 0.70))))
        self.production_turn = max(0.1, min(0.95, float(cfg.get("production_turn", 0.52))))
        self.production_camera = max(0.05, min(0.8, float(cfg.get("production_camera", 0.22))))
        self.production_burst_min = max(0.25, float(cfg.get("production_burst_min_seconds", 0.75)))
        self.production_burst_max = max(
            self.production_burst_min, float(cfg.get("production_burst_max_seconds", 1.45))
        )
        self.production_jump_probability = max(
            0.0, min(0.6, float(cfg.get("production_jump_probability", 0.18)))
        )
        self.double_jump_probability = max(
            0.0, min(1.0, float(cfg.get("double_jump_probability", 0.65)))
        )
        self.loop_similarity_threshold = max(
            0.5, min(0.999, float(cfg.get("loop_similarity_threshold", 0.94)))
        )
        self.stagnant_motion_threshold = max(
            0.0, min(0.2, float(cfg.get("stagnant_motion_threshold", 0.008)))
        )

        self.progress = JakProgression()
        self.progress_probe_seconds = max(20.0, float(cfg.get("progress_probe_seconds", 120.0)))
        self.progress_probe_hold_seconds = max(
            0.7, float(cfg.get("progress_probe_hold_seconds", 1.25))
        )
        self.next_progress_probe_at = 0.0
        self.progress_probe_release_at = 0.0
        self.progress_probe_active = False

        self.next_production_action_at = 0.0
        self.second_jump_at = 0.0
        self.second_jump_pending = False
        self.held_cross_until = 0.0
        self.recovery_step = 0

        self.production_bursts = 0
        self.camera_sweeps = 0
        self.jump_attempts_production = 0
        self.double_jumps = 0
        self.roll_jumps = 0
        self.spin_recoveries = 0
        self.anti_loop_turns = 0
        self.progress_probes = 0
        self.control_mode_changes = 0
        self.progress_updates = 0

    def _main_menu_gate(self, controller: Controller, ctx: ProfileContext) -> str:
        before = self.main_menu_confirms
        action = super()._main_menu_gate(controller, ctx)
        if self.main_menu_confirms > before and self.campaign_launch_at is None:
            self.campaign_launch_at = ctx.now
        return action

    def _semantic_refresh(self, ctx: ProfileContext) -> None:
        # Keep OCR alive during gameplay too: progress/status overlays are a useful
        # source of persistent reward signals, and the bounded worker is off-path.
        snapshot = self.ocr.read(ctx.frame, ctx.now)
        self.last_ocr_text = snapshot.text
        self.last_ocr_confidence = snapshot.mean_confidence
        observed = parse_progress_ocr(snapshot.text)
        merged = merge_progress(self.progress, observed)
        if merged != self.progress:
            self.progress = merged
            self.progress_updates += 1

    def _set_control_mode(self, mode: JakControlMode) -> None:
        if mode != self.control_mode:
            self.control_mode = mode
            self.control_mode_changes += 1
            self.recovery_step = 0

    def _update_control_mode(self) -> None:
        from_template = control_mode_for_template(self.last_template_name)
        if from_template != JakControlMode.UNKNOWN:
            self._set_control_mode(from_template)
        elif self.phase == JakPhase.GAMEPLAY and self.control_mode == JakControlMode.UNKNOWN:
            self._set_control_mode(JakControlMode.ON_FOOT)
        elif self.phase != JakPhase.GAMEPLAY:
            self._set_control_mode(JakControlMode.UNKNOWN)

    def _unknown_after_opening_can_be_gameplay(self, ctx: ProfileContext) -> bool:
        if self.campaign_launch_at is None:
            return False
        if ctx.now - self.campaign_launch_at < self.opening_cinematic_hold_seconds:
            return False
        compact = self._compact_text(self.last_ocr_text)
        # Static save/menu screens often contain these words. Never promote them to
        # gameplay just because a timer elapsed.
        blockers = ("SAVE", "LOADGAME", "NEWGAME", "OPTIONS", "SELECTGAME", "MEMORYCARD")
        if any(marker in compact for marker in blockers):
            return False
        self.gameplay_assumed_after_opening = True
        return True

    def _production_phase(self, ctx: ProfileContext) -> JakPhase:
        phase = self._observe_phase(ctx)
        if phase == JakPhase.GAMEPLAY:
            self.last_gameplay_at = ctx.now
            return phase

        # A short ambiguity latch prevents a single bad template/OCR frame from
        # taking controller ownership away from an active platforming sequence.
        if phase == JakPhase.UNKNOWN and ctx.now - self.last_gameplay_at <= self.gameplay_grace_seconds:
            self._set_phase(JakPhase.GAMEPLAY)
            return JakPhase.GAMEPLAY

        if phase == JakPhase.UNKNOWN and self._unknown_after_opening_can_be_gameplay(ctx):
            self._set_phase(JakPhase.GAMEPLAY)
            self.last_gameplay_at = ctx.now
            return JakPhase.GAMEPLAY
        return phase

    def _release_timed_holds(self, controller: Controller, ctx: ProfileContext) -> None:
        if self.held_cross_until and ctx.now >= self.held_cross_until:
            controller.release("cross")
            self.held_cross_until = 0.0
        if self.progress_probe_active and ctx.now >= self.progress_probe_release_at:
            controller.release("r2")
            self.progress_probe_active = False
            self.next_progress_probe_at = ctx.now + self.progress_probe_seconds
            self.current_action = "jak: progress probe complete; resume exploration"

    def _maybe_progress_probe(self, controller: Controller, ctx: ProfileContext) -> bool:
        if self.progress_probe_active:
            self._neutral_once(controller)
            self.current_action = "jak: reading L2/R2 progress totals; hold traversal"
            return True
        if ctx.now < self.next_progress_probe_at:
            return False
        controller.neutral_sticks()
        controller.hold("r2")
        self.progress_probe_active = True
        self.progress_probe_release_at = ctx.now + self.progress_probe_hold_seconds
        self.progress_probes += 1
        self._neutralized = False
        self.current_action = "jak: R2 progress probe"
        return True

    def _schedule_jump(self, controller: Controller, ctx: ProfileContext) -> str:
        controller.tap("cross", 0.07)
        self.jump_attempts_production += 1
        if self.production_random.random() < self.double_jump_probability:
            self.second_jump_pending = True
            self.second_jump_at = ctx.now + 0.18
            return " + double-jump queued"
        return " + jump"

    def _service_jump_combo(self, controller: Controller, ctx: ProfileContext) -> bool:
        if not self.second_jump_pending or ctx.now < self.second_jump_at:
            return False
        controller.tap("cross", 0.07)
        self.second_jump_pending = False
        self.double_jumps += 1
        self.current_action = "jak: second jump"
        return True

    def _anti_loop(self, controller: Controller, ctx: ProfileContext) -> str:
        direction = self.production_random.choice((-1.0, 1.0))
        controller.set_left_stick(direction * self.production_turn, self.production_forward * 0.65)
        controller.set_right_stick(-direction * self.production_camera, 0.0)
        self.anti_loop_turns += 1
        self.camera_sweeps += 1
        self.production_bursts += 1
        self.next_production_action_at = ctx.now + 1.15
        self._neutralized = False
        self.current_action = "jak: visual loop/stagnation -> turn + camera sweep"
        return self.current_action

    def _on_foot(self, controller: Controller, ctx: ProfileContext) -> str:
        if self._service_jump_combo(controller, ctx):
            return self.current_action
        if ctx.now < self.next_production_action_at:
            return self.current_action

        looping = self.scene_metrics.loop_similarity >= self.loop_similarity_threshold
        stagnant = (
            self.scene_metrics.center_motion <= self.stagnant_motion_threshold
            and self.scene_metrics.lower_motion <= self.stagnant_motion_threshold
        )
        if looping or stagnant:
            return self._anti_loop(controller, ctx)

        heading = self.production_random.uniform(-0.18, 0.18)
        camera = self.production_random.uniform(-self.production_camera * 0.35, self.production_camera * 0.35)
        controller.set_left_stick(heading, self.production_forward)
        controller.set_right_stick(camera, 0.0)
        self._neutralized = False
        suffix = ""
        if self.production_random.random() < self.production_jump_probability:
            suffix = self._schedule_jump(controller, ctx)

        duration = self.production_random.uniform(self.production_burst_min, self.production_burst_max)
        self.next_production_action_at = ctx.now + duration
        self.production_bursts += 1
        self.current_action = f"jak: on-foot explore x={heading:+.2f} cam={camera:+.2f}{suffix}"
        return self.current_action

    def _zoomer(self, controller: Controller, ctx: ProfileContext) -> str:
        if ctx.now < self.next_production_action_at:
            return self.current_action
        steer = self.production_random.uniform(-0.32, 0.32)
        controller.set_left_stick(steer, 0.0)
        controller.hold("cross")
        self.held_cross_until = ctx.now + 0.85
        if self.production_random.random() < 0.12:
            controller.tap(self.production_random.choice(("l1", "r1")), 0.07)
        self.next_production_action_at = ctx.now + 0.90
        self.production_bursts += 1
        self._neutralized = False
        self.current_action = f"jak: Zoomer accelerate/steer {steer:+.2f}"
        return self.current_action

    def _flut_flut(self, controller: Controller, ctx: ProfileContext) -> str:
        if ctx.now < self.next_production_action_at:
            return self.current_action
        heading = self.production_random.uniform(-0.20, 0.20)
        controller.set_left_stick(heading, 0.72)
        if self.production_random.random() < 0.30:
            controller.tap("cross", 0.07)
            controller.tap("cross", 0.07)
            self.double_jumps += 1
        self.next_production_action_at = ctx.now + 0.95
        self.production_bursts += 1
        self._neutralized = False
        self.current_action = f"jak: Flut Flut traverse x={heading:+.2f}"
        return self.current_action

    def _cannon(self, controller: Controller, ctx: ProfileContext) -> str:
        if ctx.now < self.next_production_action_at:
            return self.current_action
        aim = self.production_random.uniform(-0.65, 0.65)
        controller.set_left_stick(aim, self.production_random.uniform(-0.20, 0.20))
        controller.tap("cross", self.production_random.uniform(0.08, 0.24))
        self.next_production_action_at = ctx.now + 0.75
        self.production_bursts += 1
        self._neutralized = False
        self.current_action = f"jak: cannon scan/fire x={aim:+.2f}"
        return self.current_action

    def _production_gameplay(self, controller: Controller, ctx: ProfileContext) -> str:
        self.scene_metrics = self.scene_memory.observe(ctx.frame, ctx.previous_frame, ctx.now)
        self.last_gameplay_at = ctx.now
        self._update_control_mode()
        self._release_timed_holds(controller, ctx)

        if self._maybe_progress_probe(controller, ctx):
            return self.current_action

        if self.control_mode == JakControlMode.ON_FOOT:
            return self._on_foot(controller, ctx)
        if self.control_mode == JakControlMode.ZOOMER:
            return self._zoomer(controller, ctx)
        if self.control_mode == JakControlMode.FLUT_FLUT:
            return self._flut_flut(controller, ctx)
        if self.control_mode == JakControlMode.CANNON:
            return self._cannon(controller, ctx)
        if self.control_mode == JakControlMode.FISHING:
            self._neutral_once(controller)
            self.current_action = "jak: fishing mode recognized; await dedicated fish perception"
            return self.current_action

        self._neutral_once(controller)
        self.current_action = "jak: gameplay control mode unknown; hold inputs"
        return self.current_action

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        self._semantic_refresh(ctx)
        phase = self._production_phase(ctx) if self.mode == "production" else self._observe_phase(ctx)

        if self.title_gate_visible:
            return self._title_gate(controller, ctx)
        if self.main_menu_visible:
            return self._main_menu_gate(controller, ctx)

        if self.mode == "observe":
            return self._observe_only(controller, ctx)
        if self.mode == "explore":
            return super().tick(controller, ctx)

        # Production mode owns only explicit gameplay. Story presentation and every
        # unknown/static menu remain hands-off until calibrated.
        if phase == JakPhase.GAMEPLAY:
            return self._production_gameplay(controller, ctx)

        self._release_timed_holds(controller, ctx)
        controller.neutral_sticks()
        self._neutralized = True
        if self.campaign_launch_at is not None and phase == JakPhase.UNKNOWN:
            elapsed = ctx.now - self.campaign_launch_at
            if elapsed < self.opening_cinematic_hold_seconds:
                remaining = self.opening_cinematic_hold_seconds - elapsed
                self.current_action = f"jak: preserve opening presentation; {remaining:.0f}s safety window"
                return self.current_action
        self.current_action = f"jak: production {phase.value}; fail closed / hold inputs"
        return self.current_action

    def recover(self, controller: Controller) -> str:
        """Gameplay-only recovery ladder; menus/cutscenes never inherit it."""
        if self.mode != "production" or self.phase != JakPhase.GAMEPLAY:
            return super().recover(controller)

        step = self.recovery_step % 4
        self.recovery_step += 1
        self.second_jump_pending = False
        controller.release_all()
        controller.neutral_sticks()
        direction = -1.0 if step % 2 == 0 else 1.0

        if self.control_mode == JakControlMode.ZOOMER:
            controller.set_left_stick(direction * 0.72, 0.0)
            controller.hold("cross")
            controller.tap("r1", 0.08)
            self.current_action = "jak: Zoomer recovery steer + hop"
            return self.current_action
        if self.control_mode == JakControlMode.FLUT_FLUT:
            controller.set_left_stick(direction * 0.35, 0.70)
            controller.tap("cross", 0.08)
            controller.tap("cross", 0.08)
            self.current_action = "jak: Flut Flut recovery flutter"
            return self.current_action
        if self.control_mode in {JakControlMode.CANNON, JakControlMode.FISHING}:
            self.current_action = "jak: special-mode watchdog neutral hold"
            return self.current_action

        if step == 0:
            controller.set_left_stick(direction * 0.75, 0.45)
            controller.set_right_stick(-direction * 0.45, 0.0)
            self.camera_sweeps += 1
            self.current_action = "jak: recovery L1 turn/camera escape"
        elif step == 1:
            controller.set_left_stick(direction * 0.25, 0.72)
            controller.tap("cross", 0.08)
            controller.tap("cross", 0.08)
            self.double_jumps += 1
            self.current_action = "jak: recovery L2 double jump"
        elif step == 2:
            controller.set_left_stick(direction * 0.20, 0.82)
            controller.tap("r1", 0.08)
            controller.tap("cross", 0.08)
            self.roll_jumps += 1
            self.current_action = "jak: recovery L3 roll jump"
        else:
            controller.set_left_stick(direction * 0.65, 0.40)
            controller.tap("circle", 0.08)
            self.spin_recoveries += 1
            self.current_action = "jak: recovery L4 spin + redirect"
        self._neutralized = False
        return self.current_action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "jak_mode": self.mode,
                "jak_control_mode": self.control_mode.value,
                "jak_gameplay_assumed_after_opening": self.gameplay_assumed_after_opening,
                "jak_scene_novelty": round(self.scene_metrics.novelty, 4),
                "jak_loop_similarity": round(self.scene_metrics.loop_similarity, 4),
                "jak_center_motion": round(self.scene_metrics.center_motion, 4),
                "jak_lower_motion": round(self.scene_metrics.lower_motion, 4),
                "jak_power_cells": self.progress.power_cells,
                "jak_precursor_orbs": self.progress.precursor_orbs,
                "jak_scout_flies": self.progress.scout_flies,
                "jak_progress_updates": self.progress_updates,
                "jak_progress_probes": self.progress_probes,
                "jak_production_bursts": self.production_bursts,
                "jak_anti_loop_turns": self.anti_loop_turns,
                "jak_camera_sweeps": self.camera_sweeps,
                "jak_production_jump_attempts": self.jump_attempts_production,
                "jak_double_jumps": self.double_jumps,
                "jak_roll_jumps": self.roll_jumps,
                "jak_spin_recoveries": self.spin_recoveries,
                "jak_control_mode_changes": self.control_mode_changes,
            }
        )
        return state
