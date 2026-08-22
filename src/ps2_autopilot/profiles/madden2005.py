from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
import math
import random
import time

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_vision import MaddenObservation, MaddenVision, MaddenVisualState

from .base import GameProfile, ProfileContext


class MaddenPhase(str, Enum):
    MENU = "menu"
    TRANSITION = "transition"
    PLAYCALL = "playcall"
    PRE_SNAP = "pre_snap"
    LIVE = "live"
    POST_PLAY = "post_play"
    KICKING = "kicking"
    PAUSED = "paused"
    GAME_OVER = "game_over"


class Possession(str, Enum):
    UNKNOWN = "unknown"
    OFFENSE = "offense"
    DEFENSE = "defense"


class PlayIntent(str, Enum):
    BALANCED = "balanced"
    RUN = "run"
    PASS = "pass"
    DEFENSE = "defense"


@dataclass(frozen=True)
class QueuedTap:
    due_at: float
    action: str
    duration: float = 0.07


class Madden2005Profile(GameProfile):
    """Madden 2005 autonomous player with temporal football state reasoning.

    The profile intentionally does not require OCR or committed game assets. It
    combines field/motion vision with cause-and-effect: if our pre-snap X press is
    immediately followed by a live play, we were probably on offense; if a live
    play starts without our snap attempt, we were probably on defense. Optional
    offense/defense-labeled templates override that heuristic.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        self.vision = MaddenVision(
            field_green_threshold=float(cfg.get("field_green_threshold", 0.20)),
            live_motion_threshold=float(cfg.get("live_motion_threshold", 0.020)),
            template_threshold=float(cfg.get("template_threshold", 0.84)),
        )
        self.pre_snap_wait = float(cfg.get("pre_snap_wait_seconds", 1.15))
        self.menu_action_seconds = float(cfg.get("menu_action_seconds", 1.75))
        self.playcall_action_seconds = float(cfg.get("playcall_action_seconds", 1.15))
        self.kick_power_seconds = float(cfg.get("kick_power_seconds", 0.72))
        self.kick_accuracy_seconds = float(cfg.get("kick_accuracy_seconds", 0.34))
        self.aggression = max(0.0, min(1.0, float(cfg.get("aggression", 0.68))))
        self.steering_wander = max(0.0, min(0.8, float(cfg.get("steering_wander", 0.30))))
        self.phase_stability = float(cfg.get("phase_stability_seconds", 0.18))
        self.live_hold_seconds = float(cfg.get("live_hold_seconds", 0.55))
        self.snap_causality_seconds = float(cfg.get("snap_causality_seconds", 1.55))

        self.phase = MaddenPhase.MENU
        self.phase_since = 0.0
        self.candidate_phase: MaddenPhase | None = None
        self.candidate_since = 0.0
        self.last_live_seen_at = -1e9
        self.play_started_at = 0.0
        self.next_action_at = 0.0
        self.queue: deque[QueuedTap] = deque()
        self.kick_armed = False
        self.playcall_armed = False
        self.current_action = "boot"
        self.last_observation: MaddenObservation | None = None

        self.possession = Possession.UNKNOWN
        self.possession_confidence = 0.0
        self.planned_play = PlayIntent.BALANCED
        self.last_snap_at = -1e9
        self.snap_attempts = 0
        self.live_action_count = 0
        self.pass_icons_requested = False
        self.pass_thrown = False
        self.catch_attempted = False

        self.plays_started = 0
        self.plays_completed = 0
        self.pass_attempts = 0
        self.tackle_attempts = 0
        self.kicks = 0
        self.recoveries = 0

        seed = cfg.get("random_seed")
        self.rng = random.Random(None if seed is None else int(seed))

    @staticmethod
    def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
        return max(low, min(high, float(value)))

    def _set_possession(self, role: Possession, evidence: float) -> None:
        evidence = self._clamp(evidence, 0.0, 1.0)
        if role == Possession.UNKNOWN:
            return
        if self.possession == role:
            self.possession_confidence = min(0.99, max(self.possession_confidence, evidence))
            return
        if self.possession == Possession.UNKNOWN or evidence >= self.possession_confidence + 0.08:
            self.possession = role
            self.possession_confidence = evidence
            return
        self.possession_confidence = max(0.10, self.possession_confidence - 0.12)
        if self.possession_confidence <= 0.20:
            self.possession = Possession.UNKNOWN

    def _template_role(self, obs: MaddenObservation) -> None:
        name = obs.template_name or ""
        if obs.template_score is None or obs.template_score < self.vision.template_threshold:
            return
        if "offense" in name:
            self._set_possession(Possession.OFFENSE, 0.98)
        elif "defense" in name:
            self._set_possession(Possession.DEFENSE, 0.98)

    def _phase_from_observation(self, obs: MaddenObservation, now: float) -> MaddenPhase:
        name = obs.template_name or ""
        template_is_strong = (
            obs.template_score is not None and obs.template_score >= self.vision.template_threshold
        )
        if template_is_strong:
            if "playcall" in name or "play_call" in name:
                return MaddenPhase.PLAYCALL
            if "pre_snap" in name:
                return MaddenPhase.PRE_SNAP
            if "post_play" in name:
                return MaddenPhase.POST_PLAY
            if "kick" in name:
                return MaddenPhase.KICKING
            if "pause" in name:
                return MaddenPhase.PAUSED
            if "game_over" in name or name == "final":
                return MaddenPhase.GAME_OVER

        if obs.state == MaddenVisualState.PLAYCALL:
            return MaddenPhase.PLAYCALL
        if obs.state == MaddenVisualState.KICKING:
            return MaddenPhase.KICKING
        if obs.state == MaddenVisualState.PAUSED:
            return MaddenPhase.PAUSED
        if obs.state == MaddenVisualState.GAME_OVER:
            return MaddenPhase.GAME_OVER
        if obs.state == MaddenVisualState.LIVE_PLAY:
            self.last_live_seen_at = now
            return MaddenPhase.LIVE
        if obs.state == MaddenVisualState.FIELD_IDLE:
            if self.phase == MaddenPhase.LIVE or now - self.last_live_seen_at < 1.25:
                return MaddenPhase.POST_PLAY
            if self.phase == MaddenPhase.POST_PLAY and now - self.phase_since < 1.0:
                return MaddenPhase.POST_PLAY
            return MaddenPhase.PRE_SNAP
        if obs.state == MaddenVisualState.TRANSITION:
            return MaddenPhase.TRANSITION
        return MaddenPhase.MENU

    def _stabilize_phase(self, raw: MaddenPhase, obs: MaddenObservation, now: float) -> MaddenPhase:
        strong_template = (
            obs.template_score is not None and obs.template_score >= self.vision.template_threshold
        )
        if strong_template:
            if raw != self.phase:
                self._transition_phase(raw, now)
            self.candidate_phase = None
            return self.phase

        if self.phase == MaddenPhase.LIVE and raw == MaddenPhase.POST_PLAY:
            if now - self.last_live_seen_at < self.live_hold_seconds:
                return self.phase

        if raw == self.phase:
            self.candidate_phase = None
            return self.phase

        if self.candidate_phase != raw:
            self.candidate_phase = raw
            self.candidate_since = now
            return self.phase

        required = 0.10 if raw == MaddenPhase.LIVE else self.phase_stability
        if now - self.candidate_since >= required:
            self._transition_phase(raw, now)
            self.candidate_phase = None
        return self.phase

    def _transition_phase(self, new_phase: MaddenPhase, now: float) -> None:
        old = self.phase
        if old == new_phase:
            return

        if old == MaddenPhase.PLAYCALL and new_phase != MaddenPhase.PLAYCALL:
            self.queue.clear()
            self.playcall_armed = False

        if new_phase == MaddenPhase.LIVE:
            self.play_started_at = now
            self.live_action_count = 0
            self.pass_icons_requested = False
            self.pass_thrown = False
            self.catch_attempted = False
            self.plays_started += 1
            if now - self.last_snap_at <= self.snap_causality_seconds:
                self._set_possession(Possession.OFFENSE, 0.82)
            elif old == MaddenPhase.PRE_SNAP:
                self._set_possession(Possession.DEFENSE, 0.68)
            if old == MaddenPhase.KICKING:
                self._set_possession(Possession.DEFENSE, 0.90)
            self.next_action_at = now + 0.30

        if old == MaddenPhase.LIVE and new_phase == MaddenPhase.POST_PLAY:
            self.plays_completed += 1
            self.next_action_at = now + 0.50

        if new_phase == MaddenPhase.PRE_SNAP:
            self.snap_attempts = 0
            self.next_action_at = now + self.pre_snap_wait

        if new_phase == MaddenPhase.PLAYCALL:
            self.playcall_armed = False
            self.next_action_at = now

        if new_phase != MaddenPhase.KICKING:
            self.kick_armed = False

        self.phase = new_phase
        self.phase_since = now

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = self.vision.observe(ctx.frame, ctx.motion, ctx.template, ctx.previous_frame)
        self.last_observation = obs
        self._template_role(obs)
        raw_phase = self._phase_from_observation(obs, ctx.now)
        self._stabilize_phase(raw_phase, obs, ctx.now)
        return obs

    def _run_queue(self, controller: Controller, now: float) -> str | None:
        if self.queue and self.queue[0].due_at <= now:
            tap = self.queue.popleft()
            controller.tap(tap.action, tap.duration)
            self.current_action = f"queued {tap.action}"
            return self.current_action
        return None

    def _menu(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        controller.neutral_sticks()
        if now < self.next_action_at:
            return self.current_action
        if obs.state == MaddenVisualState.TRANSITION:
            controller.tap("confirm")
            action = "skip transition"
        else:
            roll = self.rng.random()
            if roll < 0.76:
                controller.tap("confirm")
                action = "menu confirm"
            elif roll < 0.96:
                controller.tap("down")
                action = "menu down"
            else:
                controller.tap("up")
                action = "menu up"
        self.next_action_at = now + self.menu_action_seconds
        self.current_action = action
        return action

    def _arm_playcall(self, now: float) -> None:
        self.queue.clear()
        if self.possession == Possession.DEFENSE and self.possession_confidence >= 0.55:
            self.planned_play = PlayIntent.DEFENSE
        else:
            self.planned_play = PlayIntent.PASS if self.rng.random() < 0.48 else PlayIntent.RUN

        t = now
        if self.rng.random() < 0.55:
            self.queue.append(QueuedTap(t, self.rng.choice(["up", "down"])))
            t += 0.18
        self.queue.append(QueuedTap(t, "cross"))
        t += 0.50
        if self.rng.random() < 0.70:
            self.queue.append(QueuedTap(t, self.rng.choice(["up", "down"])))
            t += 0.16
        self.queue.append(QueuedTap(t, self.rng.choice(["square", "cross", "circle"])))
        self.playcall_armed = True
        self.next_action_at = t + self.playcall_action_seconds
        self.current_action = f"playcall macro ({self.planned_play.value})"

    def _playcall(self, controller: Controller, now: float) -> str:
        controller.neutral_sticks()
        queued = self._run_queue(controller, now)
        if queued:
            return queued
        if not self.playcall_armed or (not self.queue and now >= self.next_action_at):
            self._arm_playcall(now)
            queued = self._run_queue(controller, now)
            if queued:
                return queued
        return self.current_action

    def _pre_snap(self, controller: Controller, now: float) -> str:
        controller.neutral_sticks()
        idle_for = now - self.phase_since
        if idle_for < self.pre_snap_wait or now < self.next_action_at:
            self.current_action = f"read line ({idle_for:.1f}s)"
            return self.current_action

        if self.possession == Possession.DEFENSE and self.possession_confidence >= 0.65:
            controller.tap("circle", 0.05)
            self.next_action_at = now + 3.0
            self.current_action = "defense: cycle defender / wait snap"
            return self.current_action

        controller.tap("cross", 0.06)
        self.last_snap_at = now
        self.snap_attempts += 1
        self.next_action_at = now + min(2.4, 1.45 + self.snap_attempts * 0.18)
        self.current_action = "snap probe" if self.possession == Possession.UNKNOWN else "snap ball"
        return self.current_action

    def _offense_live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        elapsed = max(0.0, now - self.play_started_at)
        center_correction = self._clamp(obs.field_center_x * 0.55, -0.45, 0.45)
        wander = math.sin(now * 1.25) * self.steering_wander * 0.55
        controller.set_left_stick(self._clamp(center_correction + wander, -0.72, 0.72), 1.0)

        if now < self.next_action_at:
            return self.current_action

        if self.planned_play == PlayIntent.PASS:
            if not self.pass_icons_requested and elapsed >= 0.55:
                controller.tap("cross", 0.06)
                self.pass_icons_requested = True
                self.live_action_count += 1
                self.next_action_at = now + self.rng.uniform(0.48, 0.78)
                self.current_action = "offense: open passing icons / sprint"
                return self.current_action
            if self.pass_icons_requested and not self.pass_thrown and elapsed >= 1.05:
                receiver = self.rng.choices(
                    ["circle", "square", "l1", "r1"], weights=[0.30, 0.26, 0.22, 0.22], k=1
                )[0]
                controller.tap(receiver, self.rng.uniform(0.07, 0.13))
                self.pass_thrown = True
                self.pass_attempts += 1
                self.live_action_count += 1
                self.next_action_at = now + 0.45
                self.current_action = f"offense: throw {receiver}"
                return self.current_action
            if self.pass_thrown and not self.catch_attempted:
                controller.tap("triangle", 0.06)
                self.catch_attempted = True
                self.next_action_at = now + 0.55
                self.current_action = "offense: catch attempt"
                return self.current_action
            controller.tap("circle", 0.06)
            self.next_action_at = now + self.rng.uniform(0.45, 0.75)
            self.current_action = "offense: receiver sprint / broken-play run"
            return self.current_action

        roll = self.rng.random()
        if roll < 0.54:
            controller.tap("cross", 0.07)
            action = "offense: speed burst"
        elif roll < 0.70:
            move = self.rng.choice(["l1", "r1"])
            controller.tap(move, 0.06)
            action = f"offense: juke {move}"
        elif roll < 0.82:
            controller.tap("circle", 0.06)
            action = "offense: spin"
        elif roll < 0.93:
            controller.tap("triangle", 0.06)
            action = "offense: protect ball"
        else:
            controller.tap("square", 0.07)
            action = "offense: dive for yards"
        self.live_action_count += 1
        self.next_action_at = now + self.rng.uniform(0.42, 0.72)
        self.current_action = action
        return action

    def _defense_live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        pursuit = obs.motion_center_x * 0.62 + obs.field_center_x * 0.22
        wander = math.sin(now * 1.05) * self.steering_wander * 0.20
        controller.set_left_stick(self._clamp(pursuit + wander, -0.86, 0.86), 1.0)

        if now < self.next_action_at:
            return self.current_action

        if self.live_action_count == 0:
            controller.tap("cross", 0.05)
            action = "defense: switch nearest ball"
        else:
            roll = self.rng.random()
            tackle_cutoff = 0.28 + self.aggression * 0.34
            if roll < tackle_cutoff:
                controller.tap("square", 0.07)
                self.tackle_attempts += 1
                action = "defense: dive tackle"
            elif roll < 0.70:
                controller.tap("circle", 0.07)
                action = "defense: sprint / shed"
            elif roll < 0.84:
                controller.tap("triangle", 0.06)
                action = "defense: intercept / hands up"
            else:
                move = self.rng.choice(["l1", "r1"])
                controller.tap(move, 0.06)
                action = f"defense: rush move {move}"
        self.live_action_count += 1
        self.next_action_at = now + self.rng.uniform(0.34, 0.60)
        self.current_action = action
        return action

    def _live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        if self.possession == Possession.OFFENSE and self.possession_confidence >= 0.48:
            return self._offense_live(controller, obs, now)
        if self.possession == Possession.DEFENSE and self.possession_confidence >= 0.48:
            return self._defense_live(controller, obs, now)

        controller.set_left_stick(obs.field_center_x * 0.35, 1.0)
        if now < self.next_action_at:
            return self.current_action
        controller.tap("cross", 0.06)
        self.next_action_at = now + 0.62
        self.current_action = "live: role unknown / X probe"
        return self.current_action

    def _post_play(self, controller: Controller, now: float) -> str:
        controller.neutral_sticks()
        if now < self.next_action_at:
            return self.current_action
        controller.tap("cross", 0.06)
        self.next_action_at = now + 1.15
        self.current_action = "post-play: skip cutscene"
        return self.current_action

    def _kicking(self, controller: Controller, now: float) -> str:
        controller.neutral_sticks()
        if not self.kick_armed:
            self.queue.clear()
            self.queue.append(QueuedTap(now, "cross"))
            self.queue.append(QueuedTap(now + self.kick_power_seconds, "cross"))
            self.queue.append(
                QueuedTap(now + self.kick_power_seconds + self.kick_accuracy_seconds, "cross")
            )
            self.kick_armed = True
            self.kicks += 1
            self.current_action = "kick meter armed"
        queued = self._run_queue(controller, now)
        return queued or self.current_action

    def _soft_stall_recovery(self, controller: Controller, now: float) -> str | None:
        age = now - self.phase_since
        limits = {
            MaddenPhase.MENU: 65.0,
            MaddenPhase.PLAYCALL: 28.0,
            MaddenPhase.PRE_SNAP: 24.0,
            MaddenPhase.POST_PLAY: 14.0,
        }
        limit = limits.get(self.phase)
        if limit is None or age < limit or now < self.next_action_at:
            return None
        controller.neutral_sticks()
        if self.phase == MaddenPhase.PLAYCALL:
            controller.tap("triangle", 0.06)
            action = "soft recover: back out playcall"
        else:
            controller.tap("cross", 0.07)
            action = f"soft recover: advance {self.phase.value}"
        self.next_action_at = now + 4.0
        self.phase_since = now
        self.current_action = action
        return action

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        obs = self._observe(ctx)

        queued = self._run_queue(controller, ctx.now)
        if queued:
            return queued

        soft_recovery = self._soft_stall_recovery(controller, ctx.now)
        if soft_recovery:
            return soft_recovery

        if self.phase == MaddenPhase.PLAYCALL:
            return self._playcall(controller, ctx.now)
        if self.phase == MaddenPhase.KICKING:
            return self._kicking(controller, ctx.now)
        if self.phase == MaddenPhase.PRE_SNAP:
            return self._pre_snap(controller, ctx.now)
        if self.phase == MaddenPhase.LIVE:
            return self._live(controller, obs, ctx.now)
        if self.phase == MaddenPhase.POST_PLAY:
            return self._post_play(controller, ctx.now)
        if self.phase == MaddenPhase.PAUSED:
            controller.neutral_sticks()
            if ctx.now >= self.next_action_at:
                controller.tap("start")
                self.next_action_at = ctx.now + 1.5
            self.current_action = "unpause"
            return self.current_action
        if self.phase == MaddenPhase.GAME_OVER:
            controller.neutral_sticks()
            if ctx.now >= self.next_action_at:
                controller.tap("confirm")
                self.next_action_at = ctx.now + 2.0
            self.current_action = "advance final"
            return self.current_action
        return self._menu(controller, obs, ctx.now)

    def telemetry(self, ctx: ProfileContext) -> dict:
        obs = self.last_observation
        if obs is None:
            obs = self.vision.observe(ctx.frame, ctx.motion, ctx.template, ctx.previous_frame)
        return {
            "game_state": obs.state.value,
            "phase": self.phase.value,
            "phase_age": round(max(0.0, ctx.now - self.phase_since), 1),
            "possession": self.possession.value,
            "possession_confidence": round(self.possession_confidence, 2),
            "play_intent": self.planned_play.value,
            "field_green": round(obs.green_ratio, 3),
            "field_center": round(obs.field_center_x, 3),
            "motion_target": round(obs.motion_center_x, 3),
            "brightness": round(obs.brightness, 3),
            "plays_started": self.plays_started,
            "plays_completed": self.plays_completed,
            "pass_attempts": self.pass_attempts,
            "tackle_attempts": self.tackle_attempts,
            "kicks": self.kicks,
        }

    def recover(self, controller: Controller) -> str:
        self.queue.clear()
        self.playcall_armed = False
        self.kick_armed = False
        self.recoveries += 1
        controller.release_all()
        controller.tap("start", 0.08)
        time.sleep(0.08)
        controller.tap("cross", 0.08)
        time.sleep(0.08)
        controller.tap("triangle", 0.08)
        self.current_action = "madden recovery"
        return self.current_action
