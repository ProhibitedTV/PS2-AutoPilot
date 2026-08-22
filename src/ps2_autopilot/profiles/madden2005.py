from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import random
import time

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_vision import MaddenObservation, MaddenVision, MaddenVisualState

from .base import GameProfile, ProfileContext


@dataclass(frozen=True)
class QueuedTap:
    due_at: float
    action: str
    duration: float = 0.07


class Madden2005Profile(GameProfile):
    """Autonomous Madden 2005 baseline using possession-agnostic controls."""

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
        self.last_state: MaddenVisualState | None = None
        self.state_since = 0.0
        self.next_action_at = 0.0
        self.live_started_at = 0.0
        self.queue: deque[QueuedTap] = deque()
        self.kick_armed = False
        self.current_action = "boot"
        self.rng = random.Random()

    def _transition(self, state: MaddenVisualState, now: float) -> None:
        if state == self.last_state:
            return
        self.last_state = state
        self.state_since = now
        if state == MaddenVisualState.LIVE_PLAY:
            self.live_started_at = now
        if state != MaddenVisualState.KICKING:
            self.kick_armed = False

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
        if self.rng.random() < 0.68:
            controller.tap("confirm")
            action = "menu confirm"
        else:
            direction = self.rng.choice(["up", "down", "left", "right"])
            controller.tap(direction)
            action = f"menu {direction}"
        if obs.state == MaddenVisualState.TRANSITION:
            action = "transition / skip"
        self.next_action_at = now + self.menu_action_seconds
        self.current_action = action
        return action

    def _playcall(self, controller: Controller, now: float) -> str:
        controller.neutral_sticks()
        if now < self.next_action_at:
            return self.current_action
        if self.rng.random() < 0.45:
            controller.tap(self.rng.choice(["up", "down", "left", "right"]))
        receiver_button = self.rng.choice(["square", "cross", "circle"])
        controller.tap(receiver_button)
        self.next_action_at = now + self.playcall_action_seconds
        self.current_action = f"call play ({receiver_button})"
        return self.current_action

    def _field_idle(self, controller: Controller, now: float) -> str:
        controller.neutral_sticks()
        idle_for = now - self.state_since
        if idle_for < self.pre_snap_wait or now < self.next_action_at:
            self.current_action = f"read line ({idle_for:.1f}s)"
            return self.current_action
        controller.tap("cross", 0.06)
        self.next_action_at = now + 1.65
        self.current_action = "snap / advance"
        return self.current_action

    def _live(self, controller: Controller, now: float) -> str:
        elapsed = max(0.0, now - self.live_started_at)
        x = math.sin(now * 1.35) * self.steering_wander
        controller.set_left_stick(x, 1.0)
        if now < self.next_action_at:
            return self.current_action
        roll = self.rng.random()
        if elapsed < 0.85:
            controller.tap("cross", 0.07)
            action = "burst / nearest ball"
        elif roll < 0.42:
            controller.tap("cross", 0.07)
            action = "sprint / receiver X / switch"
        elif roll < 0.70:
            controller.tap("circle", 0.07)
            action = "spin / receiver O / defensive sprint"
        elif roll < 0.86:
            controller.tap(self.rng.choice(["l1", "r1"]), 0.06)
            action = "juke / receiver / pass-rush move"
        elif roll < 0.95 and self.aggression > 0.35:
            controller.tap("square", 0.09)
            action = "dive / tackle"
        else:
            controller.tap("triangle", 0.06)
            action = "protect / catch / intercept"
        self.next_action_at = now + self.rng.uniform(0.34, 0.62)
        self.current_action = action
        return action

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
            self.current_action = "kick meter armed"
        queued = self._run_queue(controller, now)
        return queued or self.current_action

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        obs = self.vision.observe(ctx.frame, ctx.motion, ctx.template)
        self._transition(obs.state, ctx.now)
        queued = self._run_queue(controller, ctx.now)
        if queued:
            return queued
        if obs.state == MaddenVisualState.PLAYCALL:
            return self._playcall(controller, ctx.now)
        if obs.state == MaddenVisualState.KICKING:
            return self._kicking(controller, ctx.now)
        if obs.state == MaddenVisualState.FIELD_IDLE:
            return self._field_idle(controller, ctx.now)
        if obs.state == MaddenVisualState.LIVE_PLAY:
            return self._live(controller, ctx.now)
        if obs.state == MaddenVisualState.PAUSED:
            controller.neutral_sticks()
            if ctx.now >= self.next_action_at:
                controller.tap("start")
                self.next_action_at = ctx.now + 1.5
            self.current_action = "unpause"
            return self.current_action
        if obs.state == MaddenVisualState.GAME_OVER:
            controller.neutral_sticks()
            if ctx.now >= self.next_action_at:
                controller.tap("confirm")
                self.next_action_at = ctx.now + 2.0
            self.current_action = "advance final"
            return self.current_action
        return self._menu(controller, obs, ctx.now)

    def telemetry(self, ctx: ProfileContext) -> dict:
        obs = self.vision.observe(ctx.frame, ctx.motion, ctx.template)
        return {
            "game_state": obs.state.value,
            "field_green": round(obs.green_ratio, 3),
            "brightness": round(obs.brightness, 3),
        }

    def recover(self, controller: Controller) -> str:
        self.queue.clear()
        controller.release_all()
        controller.tap("start", 0.08)
        time.sleep(0.08)
        controller.tap("cross", 0.08)
        time.sleep(0.08)
        controller.tap("triangle", 0.08)
        self.current_action = "madden recovery"
        return self.current_action
