from __future__ import annotations

import math

from ps2_autopilot.controllers.base import Controller

from .base import ProfileContext
from .jak_and_daxter_v15 import VisualGoal
from .jak_and_daxter_v16 import JakAndDaxterV16Profile


class JakAndDaxterV17Profile(JakAndDaxterV16Profile):
    """Make reward pursuit persistent enough to reject false targets reliably.

    The first live V16 segment exposed a subtle tracking bug. The blocked-target
    resolver worked once, but later the same false Power Cell hypothesis jittered
    between nearby screen positions. V16 quantized x/y/area too finely, so those
    detector jitters looked like brand-new targets and repeatedly reset the pursuit
    timer. The global image could also keep moving from Jak/camera animation while
    forward translation was effectively useless, preventing the low-motion half of
    the stall gate from firing.

    V17 therefore treats target identity as a short-lived *track* rather than a
    per-frame component signature:

    * target signatures use coarse spatial bins and ignore unstable component area;
    * nearby same-kind detections are associated with the existing track across bin
      boundaries instead of resetting progress state;
    * progress uses a monotonic closeness metric (lower-screen approach + centering)
      rather than one-frame x/y/area changes;
    * a hard no-progress deadline triggers obstacle solving even if animation/camera
      motion remains high;
    * obstacle recovery is only declared successful when the target actually becomes
      closer (or disappears long enough to plausibly have been collected), never from
      motion alone;
    * an episode budget bounds attention. After repeated jump/bypass attempts without
      approach progress, the whole neighboring target region is temporarily banned.

    This is intentionally a policy-integrity pass: all V16 water, shoreline,
    platforming and specialist-mode safety remains above visual reward pursuit.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        self.target_signature_x_bin = max(
            0.18, min(0.70, float(cfg.get("target_signature_x_bin", 0.34)))
        )
        self.target_signature_y_bin = max(
            0.15, min(0.60, float(cfg.get("target_signature_y_bin", 0.30)))
        )
        self.target_association_radius = max(
            0.10, min(0.80, float(cfg.get("target_association_radius", 0.42)))
        )
        self.target_association_grace_seconds = max(
            0.25, float(cfg.get("target_association_grace_seconds", 1.25))
        )
        self.target_metric_progress_min = max(
            0.01, min(0.30, float(cfg.get("target_metric_progress_min", 0.055)))
        )
        self.target_hard_static_seconds = max(
            self.target_static_seconds,
            float(cfg.get("target_hard_static_seconds", 3.4)),
        )
        self.target_episode_timeout_seconds = max(
            self.target_hard_static_seconds + 1.0,
            float(cfg.get("target_episode_timeout_seconds", 9.0)),
        )
        self.target_resolution_progress_min = max(
            0.01, min(0.30, float(cfg.get("target_resolution_progress_min", 0.045)))
        )
        self.target_lost_success_seconds = max(
            0.25, float(cfg.get("target_lost_success_seconds", 0.75))
        )
        self.target_blacklist_neighbor_bins = max(
            0, min(3, int(cfg.get("target_blacklist_neighbor_bins", 1)))
        )

        self.target_track_last_seen_at = -1e9
        self.target_best_metric = -1e9
        self.target_resolution_start_metric = -1e9
        self.target_resolution_start_seen_at = -1e9

        self.target_association_merges = 0
        self.target_hard_stalls = 0
        self.target_episode_budget_hits = 0
        self.target_resolution_progresses = 0
        self.target_resolution_false_successes_prevented = 0
        self.target_disappear_successes = 0
        self.target_neighbor_blacklist_suppressions = 0

    def _goal_signature(self, goal: VisualGoal) -> tuple[str, int, int, int]:
        # The live false-cell detector oscillated roughly x=-0.33..-0.47 and
        # y=0.45..0.71 while staring at the same object. V16's 0.18/0.12 bins split
        # that one hypothesis into several identities. Coarse bins are deliberate;
        # actual target switches are handled by the continuity check below.
        return (
            goal.kind,
            int(round(goal.x / self.target_signature_x_bin)),
            int(round(goal.y / self.target_signature_y_bin)),
            0,
        )

    @staticmethod
    def _target_metric(goal: VisualGoal) -> float:
        if goal.kind == "none":
            return -1e9
        # Approaching a world-space collectible normally pushes it lower/larger on
        # screen; centering is also useful but should not by itself fake approach.
        return float(goal.y) + 0.18 * (1.0 - min(1.0, abs(float(goal.x))))

    def _same_target_track(self, goal: VisualGoal, now: float) -> bool:
        if self.target_signature is None or goal.kind != self.target_signature[0]:
            return False
        if now - self.target_track_last_seen_at > self.target_association_grace_seconds:
            return False
        distance = math.hypot(goal.x - self.target_last_x, goal.y - self.target_last_y)
        return distance <= self.target_association_radius

    def _neighbor_blacklisted(self, goal: VisualGoal) -> bool:
        signature = self._goal_signature(goal)
        radius = self.target_blacklist_neighbor_bins
        for key, until in self.target_blacklist.items():
            if getattr(self, "_v16_now", 0.0) >= until or key[0] != signature[0]:
                continue
            if abs(key[1] - signature[1]) <= radius and abs(key[2] - signature[2]) <= radius:
                return True
        return False

    def _raw_goal_candidates(self, frame):
        candidates = super()._raw_goal_candidates(frame)
        filtered = []
        for candidate in candidates:
            if self._neighbor_blacklisted(candidate):
                self.target_neighbor_blacklist_suppressions += 1
                continue
            filtered.append(candidate)
        return filtered

    def _reset_target_tracking(self) -> None:
        super()._reset_target_tracking()
        self.target_track_last_seen_at = -1e9
        self.target_best_metric = -1e9
        self.target_resolution_start_metric = -1e9
        self.target_resolution_start_seen_at = -1e9

    def _begin_target_track(self, goal: VisualGoal, ctx: ProfileContext) -> None:
        self.target_signature = self._goal_signature(goal)
        self.target_started_at = ctx.now
        self.target_last_progress_at = ctx.now
        self.target_track_last_seen_at = ctx.now
        self.target_last_x = goal.x
        self.target_last_y = goal.y
        self.target_last_area = goal.area
        self.target_best_metric = self._target_metric(goal)
        self.target_low_motion_since = None
        self.target_resolution_attempts = 0

    def _track_target(self, ctx: ProfileContext) -> None:
        if not self._visual_goal_actionable():
            # Keep a brief track through detector flicker. This is particularly useful
            # during jumps/camera corrections where the target can disappear for a few
            # frames without actually changing identity.
            if (
                self.target_signature is not None
                and ctx.now - self.target_track_last_seen_at
                <= self.target_association_grace_seconds
            ):
                return
            self._reset_target_tracking()
            return

        goal = self.visual_goal
        signature = self._goal_signature(goal)
        same = signature == self.target_signature or self._same_target_track(goal, ctx.now)
        if not same:
            self._begin_target_track(goal, ctx)
            return
        if signature != self.target_signature:
            self.target_association_merges += 1
            self.target_signature = signature

        metric = self._target_metric(goal)
        if metric >= self.target_best_metric + self.target_metric_progress_min:
            self.target_best_metric = metric
            self.target_last_progress_at = ctx.now
            self.target_progress_events += 1
            self.target_low_motion_since = None

        self.target_track_last_seen_at = ctx.now
        self.target_last_x = goal.x
        self.target_last_y = goal.y
        self.target_last_area = goal.area

        if float(ctx.motion) <= self.target_stall_motion_max:
            if self.target_low_motion_since is None:
                self.target_low_motion_since = ctx.now
        else:
            self.target_low_motion_since = None

    def _target_stalled(self, ctx: ProfileContext) -> bool:
        if not self._visual_goal_actionable() or self.target_signature is None:
            return False
        no_progress_age = ctx.now - self.target_last_progress_at
        episode_age = ctx.now - self.target_started_at
        low_motion = bool(
            self.target_low_motion_since is not None
            and ctx.now - self.target_low_motion_since >= self.target_static_seconds * 0.65
        )
        soft_stall = no_progress_age >= self.target_static_seconds and low_motion
        hard_stall = no_progress_age >= self.target_hard_static_seconds
        budget_stall = episode_age >= self.target_episode_timeout_seconds
        if hard_stall and not soft_stall:
            self.target_hard_stalls += 1
        if budget_stall:
            self.target_episode_budget_hits += 1
        return soft_stall or hard_stall or budget_stall

    def _start_target_resolution(self, controller: Controller, ctx: ProfileContext) -> str:
        episode_age = (
            ctx.now - self.target_started_at if self.target_signature is not None else 0.0
        )
        # Once the attention budget is exhausted and the bot has already tried real
        # geometry, stop spending another watchdog cycle on the same hypothesis.
        if (
            self.target_resolution_attempts >= self.target_max_resolution_attempts
            or (
                episode_age >= self.target_episode_timeout_seconds
                and self.target_resolution_attempts >= 2
            )
        ):
            self._blacklist_current_target(ctx)
            controller.set_left_stick(self.target_resolution_direction * 0.62, -0.42)
            controller.set_right_stick(-self.target_resolution_direction * 0.25, 0.0)
            self._neutralized = False
            self.current_action = "jak: V17 target budget exhausted -> blacklist/disengage"
            return self.current_action

        self.target_resolution_start_metric = self._target_metric(self.visual_goal)
        self.target_resolution_start_seen_at = self.target_track_last_seen_at
        return super()._start_target_resolution(controller, ctx)

    def _resolution_progressed(self) -> bool:
        if not self._visual_goal_actionable():
            return False
        current = self._target_metric(self.visual_goal)
        return current >= self.target_resolution_start_metric + self.target_resolution_progress_min

    def _service_target_resolution(self, controller: Controller, ctx: ProfileContext) -> str:
        if not self.target_resolution_active:
            return self.current_action
        if self.water_escape_active or self.shoreline_risk.active:
            self.target_resolution_active = False
            return (
                self._service_shoreline_guard(controller, ctx)
                if not self.water_escape_active
                else self._water_escape(controller, ctx)
            )

        # V16 returned before refreshing target perception while a resolution skill was
        # active, so it could only use generic frame motion as its success signal. Keep
        # looking at the target during the maneuver.
        self._refresh_visual_goal(ctx)
        if self._visual_goal_actionable():
            self._track_target(ctx)
        else:
            lost_age = ctx.now - self.target_track_last_seen_at
            if lost_age >= self.target_lost_success_seconds:
                self.target_resolution_active = False
                self.target_disappear_successes += 1
                self.current_action = "jak: V17 target disappeared after maneuver; resume search"
                self._reset_target_tracking()
                return self.current_action

        if ctx.now >= self.target_resolution_until and self._resolution_progressed():
            self.target_resolution_active = False
            self.target_last_progress_at = ctx.now
            self.target_best_metric = max(
                self.target_best_metric, self._target_metric(self.visual_goal)
            )
            self.target_low_motion_since = None
            self.target_resolution_progresses += 1
            self.current_action = "jak: V17 target geometry progress confirmed; resume pursuit"
            return self.current_action

        # Preserve V16's jump -> second jump -> alternating bypass mechanics, but do
        # not let ordinary character/camera animation masquerade as obstacle clearance.
        if (
            float(ctx.motion) >= self.mobility_success_motion
            and ctx.now >= self.target_resolution_until
            and not self._resolution_progressed()
        ):
            self.target_resolution_false_successes_prevented += 1

        if self.target_resolution_stage == "jump":
            target_x = self.visual_goal.x if self._visual_goal_actionable() else self.target_last_x
            controller.set_left_stick(
                self._clamp(target_x * 0.50, -0.35, 0.35),
                self.mobility_jump_forward,
            )
            self._neutralized = False
            if ctx.now < self.target_resolution_until:
                self.current_action = "jak: V17 target jump follow-through"
                return self.current_action
            self.target_resolution_active = False
            return self._start_target_resolution(controller, ctx)

        direction = self.target_resolution_direction
        controller.set_left_stick(
            direction * self.target_bypass_turn,
            self.target_bypass_forward,
        )
        controller.set_right_stick(-direction * 0.18, 0.0)
        self._neutralized = False
        if ctx.now < self.target_resolution_until:
            self.current_action = f"jak: V17 target bypass {'R' if direction > 0 else 'L'}"
            return self.current_action
        self.target_resolution_active = False
        return self._start_target_resolution(controller, ctx)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        metric = self._target_metric(self.visual_goal) if self._visual_goal_actionable() else None
        state.update(
            {
                "jak_policy_version": "v17",
                "jak_target_track_age": 0.0
                if self.target_signature is None
                else round(max(0.0, ctx.now - self.target_started_at), 2),
                "jak_target_no_progress_age": 0.0
                if self.target_signature is None
                else round(max(0.0, ctx.now - self.target_last_progress_at), 2),
                "jak_target_metric": None if metric is None else round(metric, 3),
                "jak_target_best_metric": None
                if self.target_best_metric <= -1e8
                else round(self.target_best_metric, 3),
                "jak_target_association_merges": self.target_association_merges,
                "jak_target_hard_stalls": self.target_hard_stalls,
                "jak_target_episode_budget_hits": self.target_episode_budget_hits,
                "jak_target_resolution_progresses": self.target_resolution_progresses,
                "jak_target_false_successes_prevented": self.target_resolution_false_successes_prevented,
                "jak_target_disappear_successes": self.target_disappear_successes,
                "jak_target_neighbor_blacklist_suppressions": self.target_neighbor_blacklist_suppressions,
            }
        )
        return state

    def _v16_stream_intent(self) -> str:
        # V16 telemetry calls this hook; overriding it upgrades the overlay without
        # duplicating the whole telemetry block.
        if self.target_resolution_active:
            return f"NAV · V17 SOLVE {self.visual_goal.kind.upper() or 'TARGET'}"
        if self.target_signature is not None and self._visual_goal_actionable():
            return f"TARGET · TRACK {self.visual_goal.kind.upper()}"
        return super()._v16_stream_intent()
