from __future__ import annotations

from enum import Enum

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .nfs_hot_pursuit_2 import NfsHotPursuit2V1Profile, NfsPhase


class NfsRoute(str, Enum):
    WORLD_RACING_QUICK_RACE = "world_racing_quick_race"
    CHAMPIONSHIP = "championship"
    HOT_PURSUIT_QUICK_RACE = "hot_pursuit_quick_race"
    ULTIMATE_RACER = "ultimate_racer"
    BE_THE_COP = "be_the_cop"


class NfsScreen(str, Enum):
    UNKNOWN = "unknown"
    TITLE = "title"
    MAIN_GENERIC = "main_generic"
    MAIN_HOT_PURSUIT = "main_hot_pursuit"
    MAIN_WORLD_RACING = "main_world_racing"
    MAIN_OPTIONS = "main_options"
    WORLD_GENERIC = "world_generic"
    WORLD_QUICK_RACE = "world_quick_race"
    WORLD_CHALLENGE = "world_challenge"
    WORLD_CHAMPIONSHIP = "world_championship"
    HOT_GENERIC = "hot_generic"
    HOT_QUICK_RACE = "hot_quick_race"
    HOT_COP = "hot_cop"
    HOT_CHALLENGE = "hot_challenge"
    HOT_ULTIMATE = "hot_ultimate"
    EVENT_SELECT = "event_select"
    CAR_SELECT = "car_select"
    RACE_SETUP = "race_setup"
    LOADING = "loading"
    COUNTDOWN = "countdown"
    RACING = "racing"
    COP_RACING = "cop_racing"
    WRONG_WAY = "wrong_way"
    RESULTS = "results"
    REPLAY = "replay"
    PAUSED = "paused"
    SAVING = "saving"
    BUSTED = "busted"


class NfsHotPursuit2V2Profile(NfsHotPursuit2V1Profile):
    """Research-driven PS2 policy layered on the V1 road follower.

    V2 knows the PS2 menu topology and control differences but still requires
    templates before it presses directional menu inputs. Generic menu templates
    only claim ownership and fail closed. The default autonomous route is World
    Racing -> Quick Race because it is the simplest police-free randomized loop.
    """

    name = "nfs_hot_pursuit_2"

    ROOT_INDEX = {
        NfsScreen.MAIN_HOT_PURSUIT: 0,
        NfsScreen.MAIN_WORLD_RACING: 1,
        NfsScreen.MAIN_OPTIONS: 2,
    }
    WORLD_INDEX = {
        NfsScreen.WORLD_QUICK_RACE: 0,
        NfsScreen.WORLD_CHALLENGE: 1,
        NfsScreen.WORLD_CHAMPIONSHIP: 2,
    }
    HOT_INDEX = {
        NfsScreen.HOT_QUICK_RACE: 0,
        NfsScreen.HOT_COP: 1,
        NfsScreen.HOT_CHALLENGE: 2,
        NfsScreen.HOT_ULTIMATE: 3,
    }

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        try:
            self.route = NfsRoute(str(cfg.get("menu_route", NfsRoute.WORLD_RACING_QUICK_RACE.value)))
        except ValueError:
            self.route = NfsRoute.WORLD_RACING_QUICK_RACE

        self.screen = NfsScreen.UNKNOWN
        self.drive_mode = "racer"
        self.menu_route_actions = 0
        self.fail_closed_menu_ticks = 0

        self.replay_hold_seconds = max(0.0, float(cfg.get("replay_hold_seconds", 8.0)))
        self.pause_resume_seconds = max(0.5, float(cfg.get("pause_resume_seconds", 1.5)))
        self.replay_visible_since: float | None = None
        self.pause_visible_since: float | None = None

        self.handbrake_action = str(cfg.get("handbrake_action", "r1"))
        self.handbrake_enabled = bool(cfg.get("handbrake_enabled", False))
        self.handbrake_threshold = max(0.65, min(1.0, float(cfg.get("handbrake_threshold", 0.93))))
        self.handbrake_curvature = max(0.08, float(cfg.get("handbrake_curvature", 0.20)))
        self.handbrake_interval_seconds = max(0.5, float(cfg.get("handbrake_interval_seconds", 1.25)))
        self.last_handbrake_at = -1e9
        self.handbrake_taps = 0

        self.oscillation_delta = max(0.20, float(cfg.get("oscillation_delta", 0.55)))
        self.oscillation_damping = max(0.20, min(1.0, float(cfg.get("oscillation_damping", 0.55))))
        self.oscillation_damps = 0
        self.stall_motion_threshold = max(0.0, float(cfg.get("stall_motion_threshold", 0.0025)))
        self.stall_seconds = max(0.75, float(cfg.get("stall_seconds", 1.8)))
        self.low_motion_since: float | None = None
        self.stall_recoveries = 0

        self.cop_siren_action = str(cfg.get("cop_siren_action", "circle"))
        self.cop_boost_action = str(cfg.get("cop_boost_action", "r3"))
        self.cop_roadblock_action = str(cfg.get("cop_roadblock_action", "r2"))
        self.cop_helicopter_action = str(cfg.get("cop_helicopter_action", "l2"))
        self.cop_boost_interval_seconds = max(1.5, float(cfg.get("cop_boost_interval_seconds", 4.0)))
        self.cop_support_enabled = bool(cfg.get("cop_support_enabled", False))
        self.cop_support_interval_seconds = max(8.0, float(cfg.get("cop_support_interval_seconds", 18.0)))
        self.cop_siren_sent = False
        self.last_cop_boost_at = -1e9
        self.last_cop_support_at = -1e9
        self.cop_boosts = 0
        self.cop_support_calls = 0
        self._next_support_is_roadblock = True

    @staticmethod
    def _norm(name: str | None) -> str:
        return str(name or "").strip().lower().replace("-", "_").replace(" ", "_")

    @classmethod
    def _screen_from_template(cls, name: str | None) -> NfsScreen:
        n = cls._norm(name)
        if not n:
            return NfsScreen.UNKNOWN

        selections = (
            ("main_menu_hot_pursuit_selected", NfsScreen.MAIN_HOT_PURSUIT),
            ("main_menu_world_racing_selected", NfsScreen.MAIN_WORLD_RACING),
            ("main_menu_options_selected", NfsScreen.MAIN_OPTIONS),
            ("world_racing_quick_race_selected", NfsScreen.WORLD_QUICK_RACE),
            ("world_racing_challenge_selected", NfsScreen.WORLD_CHALLENGE),
            ("world_racing_championship_selected", NfsScreen.WORLD_CHAMPIONSHIP),
            ("hot_pursuit_quick_race_selected", NfsScreen.HOT_QUICK_RACE),
            ("hot_pursuit_cop_selected", NfsScreen.HOT_COP),
            ("youre_the_cop_selected", NfsScreen.HOT_COP),
            ("hot_pursuit_challenge_selected", NfsScreen.HOT_CHALLENGE),
            ("hot_pursuit_ultimate_racer_selected", NfsScreen.HOT_ULTIMATE),
        )
        for marker, screen in selections:
            if marker in n:
                return screen

        markers = (
            ("wrong_way", NfsScreen.WRONG_WAY),
            ("cop_hud", NfsScreen.COP_RACING),
            ("youre_the_cop_hud", NfsScreen.COP_RACING),
            ("race_hud", NfsScreen.RACING),
            ("gameplay", NfsScreen.RACING),
            ("busted", NfsScreen.BUSTED),
            ("replay", NfsScreen.REPLAY),
            ("pause", NfsScreen.PAUSED),
            ("autosave", NfsScreen.SAVING),
            ("saving", NfsScreen.SAVING),
            ("results", NfsScreen.RESULTS),
            ("race_finish", NfsScreen.RESULTS),
            ("countdown", NfsScreen.COUNTDOWN),
            ("loading", NfsScreen.LOADING),
            ("event_select", NfsScreen.EVENT_SELECT),
            ("event_tree", NfsScreen.EVENT_SELECT),
            ("event_ready", NfsScreen.EVENT_SELECT),
            ("car_select", NfsScreen.CAR_SELECT),
            ("race_setup", NfsScreen.RACE_SETUP),
            ("world_racing_menu", NfsScreen.WORLD_GENERIC),
            ("hot_pursuit_menu", NfsScreen.HOT_GENERIC),
            ("main_menu", NfsScreen.MAIN_GENERIC),
            ("press_start", NfsScreen.TITLE),
            ("title", NfsScreen.TITLE),
        )
        for marker, screen in markers:
            if marker in n:
                return screen
        if n.endswith("_go"):
            return NfsScreen.COUNTDOWN
        return NfsScreen.UNKNOWN

    def _recognized_screen(self, ctx: ProfileContext) -> NfsScreen:
        template = ctx.template
        if template is None or template.score < self.template_threshold:
            self.last_template = None
            self.last_template_score = 0.0
            self.screen = NfsScreen.UNKNOWN
        else:
            self.last_template = template.name
            self.last_template_score = float(template.score)
            self.screen = self._screen_from_template(template.name)
        return self.screen

    @staticmethod
    def _move_toward(current: int, target: int) -> str:
        if current < target:
            return "down"
        if current > target:
            return "up"
        return "confirm"

    def _route_menu_action(self, screen: NfsScreen) -> str | None:
        world_route = self.route in {NfsRoute.WORLD_RACING_QUICK_RACE, NfsRoute.CHAMPIONSHIP}
        if screen in self.ROOT_INDEX:
            return self._move_toward(self.ROOT_INDEX[screen], 1 if world_route else 0)
        if world_route and screen in self.WORLD_INDEX:
            target = 0 if self.route is NfsRoute.WORLD_RACING_QUICK_RACE else 2
            return self._move_toward(self.WORLD_INDEX[screen], target)
        if not world_route and screen in self.HOT_INDEX:
            targets = {
                NfsRoute.HOT_PURSUIT_QUICK_RACE: 0,
                NfsRoute.BE_THE_COP: 1,
                NfsRoute.ULTIMATE_RACER: 3,
            }
            return self._move_toward(self.HOT_INDEX[screen], targets[self.route])
        if screen in {NfsScreen.EVENT_SELECT, NfsScreen.CAR_SELECT, NfsScreen.RACE_SETUP}:
            return "confirm"
        return None

    def _menu_tick(self, controller: Controller, ctx: ProfileContext, screen: NfsScreen) -> str:
        action = self._route_menu_action(screen)
        if action is None:
            controller.release_all()
            controller.neutral_sticks()
            self.fail_closed_menu_ticks += 1
            return f"menu fail-closed: screen={screen.value} route={self.route.value}"
        ready = ctx.now >= self.next_menu_action_at
        result = self._safe_menu_tap(controller, action, ctx)
        if ready:
            self.menu_route_actions += 1
        return f"route {self.route.value}: {result}"

    def _special_screen_tick(self, controller: Controller, ctx: ProfileContext, screen: NfsScreen) -> str | None:
        if screen is NfsScreen.REPLAY:
            controller.release_all()
            controller.neutral_sticks()
            if self.replay_visible_since is None:
                self.replay_visible_since = ctx.now
            if ctx.now - self.replay_visible_since >= self.replay_hold_seconds:
                return self._safe_menu_tap(controller, "start", ctx)
            return "replay: preserve broadcast"

        if screen is NfsScreen.PAUSED:
            controller.release_all()
            controller.neutral_sticks()
            if self.pause_visible_since is None:
                self.pause_visible_since = ctx.now
            if ctx.now - self.pause_visible_since >= self.pause_resume_seconds:
                return self._safe_menu_tap(controller, "start", ctx)
            return "pause: bounded hold before resume"

        if screen in {NfsScreen.SAVING, NfsScreen.BUSTED}:
            controller.release_all()
            controller.neutral_sticks()
            return f"{screen.value}: preserve/fail-closed"
        return None

    def _maybe_cop_actions(self, controller: Controller, ctx: ProfileContext, steer: float) -> list[str]:
        if self.drive_mode != "cop":
            return []
        tags: list[str] = []
        if not self.cop_siren_sent:
            controller.tap(self.cop_siren_action, 0.05)
            self.cop_siren_sent = True
            tags.append("siren/target")
        if (
            ctx.now - self.last_cop_boost_at >= self.cop_boost_interval_seconds
            and self.road.confidence >= 0.50
            and abs(steer) <= 0.72
        ):
            controller.tap(self.cop_boost_action, 0.05)
            self.last_cop_boost_at = ctx.now
            self.cop_boosts += 1
            tags.append("boost")
        if (
            self.cop_support_enabled
            and ctx.now - self.last_cop_support_at >= self.cop_support_interval_seconds
            and self.road.confidence >= 0.50
        ):
            action = self.cop_roadblock_action if self._next_support_is_roadblock else self.cop_helicopter_action
            controller.tap(action, 0.06)
            self._next_support_is_roadblock = not self._next_support_is_roadblock
            self.last_cop_support_at = ctx.now
            self.cop_support_calls += 1
            tags.append("roadblock" if action == self.cop_roadblock_action else "helicopter")
        return tags

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
        raw = self._clamp(
            self.steering_gain * self.road.center_x + self.curvature_gain * self.road.curvature,
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
        controller.hold(self.accelerate_action)
        controller.set_left_stick(steer, 0.0)

        tags: list[str] = []
        turn_demand = max(abs(steer), min(1.0, abs(self.road.curvature) * 1.6))
        if (
            turn_demand >= self.corner_brake_threshold
            and ctx.now - self.last_brake_at >= self.corner_brake_interval_seconds
        ):
            controller.release(self.accelerate_action)
            controller.tap(self.brake_action, 0.055)
            controller.hold(self.accelerate_action)
            self.last_brake_at = ctx.now
            tags.append("brake")

        if (
            self.handbrake_enabled
            and turn_demand >= self.handbrake_threshold
            and abs(self.road.curvature) >= self.handbrake_curvature
            and self.road.confidence >= 0.55
            and ctx.now - self.last_handbrake_at >= self.handbrake_interval_seconds
        ):
            controller.tap(self.handbrake_action, 0.045)
            self.last_handbrake_at = ctx.now
            self.handbrake_taps += 1
            tags.append("handbrake")

        tags.extend(self._maybe_cop_actions(controller, ctx, steer))
        suffix = " " + ",".join(tags) if tags else ""
        return f"{self.drive_mode} racing: steer={steer:+.2f} road={self.road.confidence:.2f}{suffix}"

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        if self.phase_since == 0.0:
            self.phase_since = ctx.now

        screen = self._recognized_screen(ctx)
        self._observe_road(ctx)

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
            new_mode = "cop" if screen is NfsScreen.COP_RACING else "racer"
            if new_mode != self.drive_mode:
                self.cop_siren_sent = False
            self.drive_mode = new_mode
            if self.phase is not NfsPhase.RACING:
                self.race_entries += 1
            self._set_phase(NfsPhase.RACING, ctx.now)
        elif screen is NfsScreen.RESULTS:
            self._set_phase(NfsPhase.RESULTS, ctx.now)
        elif screen is NfsScreen.LOADING:
            self._set_phase(NfsPhase.LOADING, ctx.now)
        elif screen is NfsScreen.COUNTDOWN:
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
            self._set_phase(NfsPhase.TITLE, ctx.now)
        elif self.race_evidence_frames >= self.race_enter_frames:
            if self.phase is not NfsPhase.RACING:
                self.race_entries += 1
            self.drive_mode = "racer"
            self._set_phase(NfsPhase.RACING, ctx.now)

        if self.phase is NfsPhase.RACING:
            self.replay_visible_since = None
            self.pause_visible_since = None
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
            if ctx.now - self.results_visible_since >= self.results_hold_seconds:
                self.last_action = self._safe_menu_tap(controller, "confirm", ctx)
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
        state.update(
            {
                "nfs_policy_version": 2,
                "nfs_screen": self.screen.value,
                "nfs_route": self.route.value,
                "nfs_drive_mode": self.drive_mode,
                "nfs_menu_route_actions": self.menu_route_actions,
                "nfs_fail_closed_menu_ticks": self.fail_closed_menu_ticks,
                "nfs_handbrake_taps": self.handbrake_taps,
                "nfs_oscillation_damps": self.oscillation_damps,
                "nfs_stall_recoveries": self.stall_recoveries,
                "nfs_cop_siren_sent": self.cop_siren_sent,
                "nfs_cop_boosts": self.cop_boosts,
                "nfs_cop_support_calls": self.cop_support_calls,
            }
        )
        return state
