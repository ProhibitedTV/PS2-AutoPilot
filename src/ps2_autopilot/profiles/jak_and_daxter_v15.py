from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.jak_objectives import GeyserObjective

from .base import ProfileContext
from .jak_and_daxter_v10 import GameplayCue
from .jak_and_daxter_v14 import JakAndDaxterV14Profile


@dataclass(frozen=True)
class VisualGoal:
    kind: str = "none"
    x: float = 0.0
    y: float = 0.0
    area: float = 0.0
    confidence: float = 0.0
    score: float = 0.0


@dataclass(frozen=True)
class LedgeCue:
    confidence: float = 0.0
    y: float = 0.0
    row_coverage: float = 0.0
    open_above: float = 0.0


class JakAndDaxterV15Profile(JakAndDaxterV14Profile):
    """Turn tutorial wandering into goal-seeking platformer behavior.

    V14 supplied a useful objective vocabulary but still behaved mostly like an
    explorer: its sequential goal filter could suppress real progress opportunities,
    objective stalls repeatedly triggered generic left/right openness scans, and
    simple ledges were frequently interpreted as walls.

    V15 adds three missing pieces:

    * an opportunistic visual reward layer. Power Cells, Precursor Orbs, Scout Fly
      boxes and Blue Eco are all positive navigation signals. The active curriculum
      objective changes their priority, but never makes legitimate progress invisible;
    * route scans are reward-aware. A view containing a plausible collectible is
      preferred over an equally open empty corridor, turning level collectibles into
      the breadcrumbs they were designed to be;
    * a bounded ledge-hop skill recognizes strong horizontal step/ledge structure in
      the lower center of the playfield and performs a forward single/double jump
      before the generic wall recovery can throw the route away.

    Water/cutscene/menu ownership remains above all of these behaviors. Exact PINE
    state, when calibrated later, can replace the visual approximations without
    changing the planner/skill interface introduced here.
    """

    GOAL_ROI = (0.06, 0.94, 0.18, 0.94)
    LEDGE_ROI = (0.30, 0.70, 0.34, 0.88)

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        # Visual goal perception / target lock.
        self.goal_refresh_seconds = max(0.05, float(cfg.get("goal_refresh_seconds", 0.12)))
        self.goal_lock_seconds = max(0.25, float(cfg.get("goal_lock_seconds", 1.10)))
        self.goal_stable_frames_required = max(
            1, min(8, int(cfg.get("goal_stable_frames_required", 2)))
        )
        self.goal_min_score = max(0.2, float(cfg.get("goal_min_score", 0.72)))
        self.goal_pursuit_forward = max(
            0.25, min(0.90, float(cfg.get("goal_pursuit_forward", 0.64)))
        )
        self.goal_turn_gain = max(0.25, min(1.8, float(cfg.get("goal_turn_gain", 1.02))))
        self.goal_camera_gain = max(
            0.05, min(0.60, float(cfg.get("goal_camera_gain", 0.18)))
        )
        self.goal_scan_salience_weight = max(
            0.0, min(2.5, float(cfg.get("goal_scan_salience_weight", 0.85)))
        )
        self.goal_high_jump_y = max(
            0.20, min(0.70, float(cfg.get("goal_high_jump_y", 0.50)))
        )
        self.goal_jump_center_x = max(
            0.10, min(0.80, float(cfg.get("goal_jump_center_x", 0.42)))
        )
        self.goal_jump_cooldown_seconds = max(
            0.5, float(cfg.get("goal_jump_cooldown_seconds", 1.35))
        )
        self.scout_attack_close_y = max(
            0.35, min(0.85, float(cfg.get("scout_attack_close_y", 0.52)))
        )
        self.scout_attack_center_x = max(
            0.10, min(0.75, float(cfg.get("scout_attack_center_x", 0.40)))
        )

        # Yellow/gold reward detectors. These are intentionally small-component
        # detectors: giant yellow terrain/sky patches are not collectibles.
        self.orb_hue_min = int(cfg.get("orb_hue_min", 7))
        self.orb_hue_max = int(cfg.get("orb_hue_max", 35))
        self.orb_sat_min = int(cfg.get("orb_sat_min", 120))
        self.orb_value_min = int(cfg.get("orb_value_min", 135))
        self.orb_min_area = max(0.00005, float(cfg.get("orb_min_area", 0.00012)))
        self.orb_max_area = min(0.035, float(cfg.get("orb_max_area", 0.014)))
        self.orb_cue_min_confidence = max(
            0.10, min(1.0, float(cfg.get("orb_cue_min_confidence", 0.44)))
        )

        self.cell_hue_min = int(cfg.get("cell_hue_min", 18))
        self.cell_hue_max = int(cfg.get("cell_hue_max", 48))
        self.cell_sat_min = int(cfg.get("cell_sat_min", 90))
        self.cell_value_min = int(cfg.get("cell_value_min", 145))
        self.cell_white_value_min = int(cfg.get("cell_white_value_min", 190))
        self.cell_white_sat_max = int(cfg.get("cell_white_sat_max", 95))
        self.cell_min_area = max(0.00015, float(cfg.get("cell_min_area", 0.00055)))
        self.cell_max_area = min(0.10, float(cfg.get("cell_max_area", 0.050)))
        self.cell_white_neighbor_min = max(
            0.001, min(0.50, float(cfg.get("cell_white_neighbor_min", 0.010)))
        )
        self.cell_cue_min_confidence = max(
            0.10, min(1.0, float(cfg.get("cell_cue_min_confidence", 0.46)))
        )

        self.next_goal_refresh_at = 0.0
        self.visual_goal = VisualGoal()
        self.visual_goal_last_seen_at = -1e9
        self.visual_goal_stable_frames = 0
        self.visual_goal_last_kind = "none"
        self.visual_goal_last_x = 0.0
        self.next_goal_jump_at = 0.0
        self.visual_goal_acquisitions = 0
        self.visual_goal_switches = 0
        self.visual_goal_pursuit_ticks = 0
        self.visual_goal_lost = 0
        self.orb_goal_cues = 0
        self.cell_goal_cues = 0
        self.opportunistic_scout_goals = 0
        self.opportunistic_eco_goals = 0
        self.goal_scan_biases = 0

        # Ledge / small platform traversal.
        self.ledge_edge_threshold = max(
            12.0, min(180.0, float(cfg.get("ledge_edge_threshold", 44.0)))
        )
        self.ledge_row_min = max(
            0.03, min(0.80, float(cfg.get("ledge_row_min", 0.13)))
        )
        self.ledge_confidence_min = max(
            0.15, min(1.0, float(cfg.get("ledge_confidence_min", 0.46)))
        )
        self.ledge_stable_frames_required = max(
            1, min(8, int(cfg.get("ledge_stable_frames_required", 2)))
        )
        self.ledge_stability_y = max(
            0.02, min(0.30, float(cfg.get("ledge_stability_y", 0.10)))
        )
        self.ledge_jump_forward = max(
            0.35, min(0.95, float(cfg.get("ledge_jump_forward", 0.76)))
        )
        self.ledge_jump_turn = max(
            0.05, min(0.55, float(cfg.get("ledge_jump_turn", 0.30)))
        )
        self.ledge_first_air_seconds = max(
            0.10, float(cfg.get("ledge_first_air_seconds", 0.22))
        )
        self.ledge_second_air_seconds = max(
            0.20, float(cfg.get("ledge_second_air_seconds", 0.44))
        )
        self.ledge_settle_seconds = max(
            0.15, float(cfg.get("ledge_settle_seconds", 0.34))
        )
        self.ledge_double_confidence = max(
            self.ledge_confidence_min,
            min(1.0, float(cfg.get("ledge_double_confidence", 0.68))),
        )
        self.ledge_jump_cooldown_seconds = max(
            0.6, float(cfg.get("ledge_jump_cooldown_seconds", 1.55))
        )
        self.ledge_success_motion = max(
            0.002, min(0.20, float(cfg.get("ledge_success_motion", 0.009)))
        )

        self.ledge_cue = LedgeCue()
        self.ledge_stable_frames = 0
        self.ledge_last_y = 0.0
        self.next_ledge_jump_at = 0.0
        self.ledge_jump_double = False
        self.ledge_jump_attempts = 0
        self.ledge_jump_double_attempts = 0
        self.ledge_jump_successes = 0
        self.ledge_jump_failures = 0

        # A stalled objective should cause orientation, not camera-scanning every few
        # seconds forever. V15 commits longer after an orientation choice.
        self.goal_route_commit_seconds = max(
            6.0, float(cfg.get("goal_route_commit_seconds", 11.0))
        )
        self.goal_replan_min_gap_seconds = max(
            8.0, float(cfg.get("goal_replan_min_gap_seconds", 16.0))
        )
        self.executed_objective_replans = 0

    def _goal_crop(self, frame: np.ndarray) -> tuple[np.ndarray, int, int, int, int]:
        if frame is None or frame.size == 0:
            return np.zeros((0, 0, 3), dtype=np.uint8), 0, 0, 0, 0
        h, w = frame.shape[:2]
        x0, x1, y0, y1 = self.GOAL_ROI
        xa = max(0, int(round(x0 * w)))
        xb = min(w, int(round(x1 * w)))
        ya = max(0, int(round(y0 * h)))
        yb = min(h, int(round(y1 * h)))
        return frame[ya:yb, xa:xb], xa, xb, ya, yb

    def _detect_orb(self, frame: np.ndarray) -> GameplayCue:
        roi, xa, _xb, ya, _yb = self._goal_crop(frame)
        if roi.size == 0:
            return GameplayCue()
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        mask = (
            (hue >= self.orb_hue_min)
            & (hue <= self.orb_hue_max)
            & (sat >= self.orb_sat_min)
            & (val >= self.orb_value_min)
        ).astype(np.uint8)
        mask = cv2.morphologyEx(
            mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)),
        )
        frame_area = float(max(frame.shape[0] * frame.shape[1], 1))
        best = GameplayCue()
        for _x, _y, w, h, area, center in self._component_candidates(mask):
            area_ratio = float(area) / frame_area
            if area_ratio < self.orb_min_area or area_ratio > self.orb_max_area:
                continue
            aspect = float(w) / float(max(h, 1))
            if aspect < 0.28 or aspect > 2.2:
                continue
            cx = (xa + float(center[0])) / float(max(frame.shape[1], 1))
            cy = (ya + float(center[1])) / float(max(frame.shape[0], 1))
            nx = self._clamp((cx - 0.5) / 0.5, -1.0, 1.0)
            center_score = 1.0 - min(1.0, abs(nx) * 0.70)
            lower_score = self._clamp((cy - 0.20) / 0.68, 0.0, 1.0)
            size_score = self._clamp(
                area_ratio / max(self.orb_min_area * 5.0, 1e-6), 0.0, 1.0
            )
            confidence = self._clamp(
                0.38 * size_score + 0.34 * center_score + 0.28 * lower_score,
                0.0,
                1.0,
            )
            if confidence > best.confidence:
                best = GameplayCue("orb", nx, cy, area_ratio, confidence)
        return best

    def _detect_power_cell(self, frame: np.ndarray) -> GameplayCue:
        roi, xa, _xb, ya, _yb = self._goal_crop(frame)
        if roi.size == 0:
            return GameplayCue()
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        hue, sat, val = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
        gold = (
            (hue >= self.cell_hue_min)
            & (hue <= self.cell_hue_max)
            & (sat >= self.cell_sat_min)
            & (val >= self.cell_value_min)
        ).astype(np.uint8)
        white = (
            (sat <= self.cell_white_sat_max)
            & (val >= self.cell_white_value_min)
        ).astype(np.uint8)
        gold = cv2.morphologyEx(
            gold,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
        )
        rh, rw = roi.shape[:2]
        frame_area = float(max(frame.shape[0] * frame.shape[1], 1))
        best = GameplayCue()
        for x, y, w, h, area, center in self._component_candidates(gold):
            area_ratio = float(area) / frame_area
            if area_ratio < self.cell_min_area or area_ratio > self.cell_max_area:
                continue
            aspect = float(w) / float(max(h, 1))
            if aspect < 0.22 or aspect > 3.2:
                continue
            pad_x = max(4, int(round(w * 0.85)))
            pad_y = max(4, int(round(h * 0.85)))
            x0, x1 = max(0, x - pad_x), min(rw, x + w + pad_x)
            y0, y1 = max(0, y - pad_y), min(rh, y + h + pad_y)
            white_ratio = (
                float(np.mean(white[y0:y1, x0:x1] > 0)) if x1 > x0 and y1 > y0 else 0.0
            )
            if white_ratio < self.cell_white_neighbor_min:
                continue
            cx = (xa + float(center[0])) / float(max(frame.shape[1], 1))
            cy = (ya + float(center[1])) / float(max(frame.shape[0], 1))
            nx = self._clamp((cx - 0.5) / 0.5, -1.0, 1.0)
            center_score = 1.0 - min(1.0, abs(nx) * 0.65)
            lower_score = self._clamp((cy - 0.18) / 0.68, 0.0, 1.0)
            size_score = self._clamp(
                area_ratio / max(self.cell_min_area * 6.0, 1e-6), 0.0, 1.0
            )
            confidence = self._clamp(
                0.30 * size_score
                + 0.28 * center_score
                + 0.22 * lower_score
                + 0.20 * self._clamp(white_ratio / 0.10, 0.0, 1.0),
                0.0,
                1.0,
            )
            if confidence > best.confidence:
                best = GameplayCue("power_cell", nx, cy, area_ratio, confidence)
        return best

    def _goal_kind_weight(self, kind: str) -> float:
        # Every legitimate collectible is useful even when the sequential curriculum
        # does not currently expect it. That is the key difference from V14.
        base = {
            "power_cell": 1.22,
            "scout_box": 1.00,
            "orb": 0.88,
            "blue_eco": 0.72,
        }.get(kind, 0.0)
        stage = self.objective.stage
        bonus = 0.0
        if kind == "power_cell" and stage in {
            GeyserObjective.FIRST_CELL,
            GeyserObjective.CLIFF_CELL,
        }:
            bonus = 0.40
        elif kind == "scout_box" and stage == GeyserObjective.SCOUT_FLIES:
            bonus = 0.38
        elif kind == "blue_eco" and stage == GeyserObjective.BLUE_ECO_DOOR:
            bonus = 0.42
        elif kind == "orb":
            bonus = 0.08  # useful breadcrumb in every tutorial objective
        return base + bonus

    def _score_goal(self, cue: GameplayCue) -> VisualGoal:
        if cue.kind == "none" or cue.confidence <= 0.0:
            return VisualGoal()
        center = 1.0 - min(1.0, abs(cue.x))
        closeness = self._clamp((cue.y - 0.18) / 0.72, 0.0, 1.0)
        score = (
            self._goal_kind_weight(cue.kind)
            + 0.48 * cue.confidence
            + 0.10 * center
            + 0.06 * closeness
        )
        return VisualGoal(cue.kind, cue.x, cue.y, cue.area, cue.confidence, score)

    def _raw_goal_candidates(self, frame: np.ndarray) -> list[VisualGoal]:
        candidates: list[VisualGoal] = []
        cell = self._detect_power_cell(frame)
        if cell.confidence >= self.cell_cue_min_confidence:
            candidates.append(self._score_goal(cell))
        orb = self._detect_orb(frame)
        if orb.confidence >= self.orb_cue_min_confidence:
            candidates.append(self._score_goal(orb))
        scout = self._detect_scout_box(frame)
        if scout.confidence >= max(self.scout_cue_min_confidence, 0.48):
            candidates.append(self._score_goal(scout))
        eco = self._detect_blue_eco(frame)
        if (
            eco.confidence >= max(self.blue_eco_cue_min_confidence, 0.44)
            and self.water_surface_bottom_ratio < self.water_surface_bottom_min * 0.65
        ):
            candidates.append(self._score_goal(eco))
        return candidates

    def _best_visual_goal(self, frame: np.ndarray) -> VisualGoal:
        candidates = self._raw_goal_candidates(frame)
        return max(candidates, key=lambda item: item.score, default=VisualGoal())

    def _refresh_visual_goal(self, ctx: ProfileContext) -> None:
        if ctx.now < self.next_goal_refresh_at:
            return
        self.next_goal_refresh_at = ctx.now + self.goal_refresh_seconds
        best = self._best_visual_goal(ctx.frame)
        if best.kind != "none" and best.score >= self.goal_min_score:
            same = bool(
                best.kind == self.visual_goal_last_kind
                and abs(best.x - self.visual_goal_last_x) <= 0.34
            )
            self.visual_goal_stable_frames = self.visual_goal_stable_frames + 1 if same else 1
            if best.kind != self.visual_goal_last_kind:
                if self.visual_goal_last_kind != "none":
                    self.visual_goal_switches += 1
                self.visual_goal_acquisitions += 1
            self.visual_goal_last_kind = best.kind
            self.visual_goal_last_x = best.x
            self.visual_goal_last_seen_at = ctx.now
            self.visual_goal = best
            if best.kind == "orb":
                self.orb_goal_cues += 1
            elif best.kind == "power_cell":
                self.cell_goal_cues += 1
            elif best.kind == "scout_box" and self.objective.stage != GeyserObjective.SCOUT_FLIES:
                self.opportunistic_scout_goals += 1
            elif best.kind == "blue_eco" and self.objective.stage != GeyserObjective.BLUE_ECO_DOOR:
                self.opportunistic_eco_goals += 1
            # A real target is more useful than another generic objective-stall scan.
            self.next_objective_replan_scan_at = max(
                self.next_objective_replan_scan_at,
                ctx.now + self.goal_replan_min_gap_seconds,
            )
            return

        if (
            self.visual_goal.kind != "none"
            and ctx.now - self.visual_goal_last_seen_at <= self.goal_lock_seconds
        ):
            return
        if self.visual_goal.kind != "none":
            self.visual_goal_lost += 1
        self.visual_goal = VisualGoal()
        self.visual_goal_last_kind = "none"
        self.visual_goal_stable_frames = 0

    def _visual_goal_actionable(self) -> bool:
        return bool(
            self.visual_goal.kind != "none"
            and self.visual_goal.score >= self.goal_min_score
            and self.visual_goal_stable_frames >= self.goal_stable_frames_required
        )

    def _visual_interest_score(self, frame: np.ndarray) -> float:
        best = self._best_visual_goal(frame)
        if best.kind == "none":
            return 0.0
        return self._clamp(best.score / 2.0, 0.0, 1.0)

    def _land_openness_score(self, frame: np.ndarray) -> float:
        base = super()._land_openness_score(frame)
        interest = self._visual_interest_score(frame)
        if interest > 0.0:
            self.goal_scan_biases += 1
        return base + self.goal_scan_salience_weight * interest

    def _ledge_from_frame(self, frame: np.ndarray) -> LedgeCue:
        if frame is None or frame.size == 0:
            return LedgeCue()
        h, w = frame.shape[:2]
        x0, x1, y0, y1 = self.LEDGE_ROI
        xa, xb = int(round(x0 * w)), int(round(x1 * w))
        ya, yb = int(round(y0 * h)), int(round(y1 * h))
        roi = frame[max(0, ya):min(h, yb), max(0, xa):min(w, xb)]
        if roi.size == 0 or roi.shape[0] < 12 or roi.shape[1] < 12:
            return LedgeCue()

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        sobel_y = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        edge = np.abs(sobel_y) >= self.ledge_edge_threshold
        row_coverage = np.mean(edge, axis=1)
        if row_coverage.size < 5:
            return LedgeCue()
        smooth = np.convolve(row_coverage, np.ones(5, dtype=np.float32) / 5.0, mode="same")
        lo = max(1, int(round(0.22 * smooth.size)))
        hi = min(smooth.size - 1, int(round(0.84 * smooth.size)))
        if hi <= lo:
            return LedgeCue()
        idx = lo + int(np.argmax(smooth[lo:hi]))
        coverage = float(smooth[idx])
        if coverage < self.ledge_row_min:
            return LedgeCue(row_coverage=coverage)

        band = max(3, int(round(0.13 * roi.shape[0])))
        above = gray[max(0, idx - band):max(1, idx - 2), :]
        if above.size:
            above_value = float(np.mean(above)) / 255.0
            above_edges = cv2.Canny(above, 55, 135)
            above_edge_density = float(np.mean(above_edges > 0))
            open_above = self._clamp(
                0.65 * above_value + 0.35 * (1.0 - min(1.0, above_edge_density * 4.0)),
                0.0,
                1.0,
            )
        else:
            open_above = 0.0

        coverage_score = self._clamp(
            (coverage - self.ledge_row_min) / max(0.40 - self.ledge_row_min, 0.05),
            0.0,
            1.0,
        )
        # Ledges near the lower-middle of the image are the ones Jak is about to hit.
        local_y = float(idx) / float(max(roi.shape[0] - 1, 1))
        proximity = self._clamp((local_y - 0.18) / 0.66, 0.0, 1.0)
        confidence = self._clamp(
            0.52 * coverage_score + 0.28 * open_above + 0.20 * proximity,
            0.0,
            1.0,
        )
        screen_y = (max(0, ya) + float(idx)) / float(max(h, 1))
        return LedgeCue(confidence, screen_y, coverage, open_above)

    def _refresh_ledge_cue(self, ctx: ProfileContext) -> None:
        cue = self._ledge_from_frame(ctx.frame)
        if cue.confidence >= self.ledge_confidence_min:
            stable = bool(
                self.ledge_stable_frames > 0
                and abs(cue.y - self.ledge_last_y) <= self.ledge_stability_y
            )
            self.ledge_stable_frames = self.ledge_stable_frames + 1 if stable else 1
            self.ledge_last_y = cue.y
            self.ledge_cue = cue
        else:
            self.ledge_stable_frames = 0
            self.ledge_cue = cue

    def _ledge_actionable(self, ctx: ProfileContext) -> bool:
        target_help = bool(
            self._visual_goal_actionable()
            and abs(self.visual_goal.x) <= 0.58
            and self.visual_goal.y <= self.goal_high_jump_y + 0.08
        )
        threshold = self.ledge_confidence_min - (0.08 if target_help else 0.0)
        return bool(
            ctx.now >= self.next_ledge_jump_at
            and self.ledge_cue.confidence >= threshold
            and self.ledge_stable_frames >= self.ledge_stable_frames_required
            and not self.water_escape_active
            and not self.water_geometry_confirmed
            and not self.land_scan_active
        )

    def _cancel_local_stuck_for_ledge(self) -> None:
        self.local_stuck_active = False
        self.local_stuck_stage = "none"
        self.local_stuck_stage_until = 0.0
        self.local_stuck_armed_at = None
        self.local_stuck_low_motion_since = None
        self.local_stuck_success_since = None

    def _start_ledge_jump(self, controller: Controller, ctx: ProfileContext) -> str:
        self._cancel_local_stuck_for_ledge()
        self.skill_active = True
        self.skill_name = "v15_ledge_jump"
        self.skill_stage = "first-air"
        self.skill_until = ctx.now + self.ledge_first_air_seconds
        self.skill_heading = self._clamp(
            self.visual_goal.x * 0.42 if self._visual_goal_actionable() else self.route_bias,
            -self.ledge_jump_turn,
            self.ledge_jump_turn,
        )
        self.ledge_jump_double = bool(
            self.ledge_cue.confidence >= self.ledge_double_confidence
            or (
                self._visual_goal_actionable()
                and self.visual_goal.y <= self.goal_high_jump_y - 0.06
            )
        )
        self.ledge_jump_attempts += 1
        if self.ledge_jump_double:
            self.ledge_jump_double_attempts += 1
        self.next_ledge_jump_at = ctx.now + self.ledge_jump_cooldown_seconds
        controller.set_left_stick(self.skill_heading, self.ledge_jump_forward)
        controller.set_right_stick(-self.skill_heading * 0.10, 0.0)
        controller.tap("cross", 0.07)
        self._neutralized = False
        self.current_action = (
            f"jak: V15 ledge-hop launch conf={self.ledge_cue.confidence:.2f}"
            f"{' double' if self.ledge_jump_double else ''}"
        )
        return self.current_action

    def _service_v15_ledge_jump(self, controller: Controller, ctx: ProfileContext) -> str:
        heading = self.skill_heading
        controller.set_left_stick(heading, self.ledge_jump_forward)
        controller.set_right_stick(-heading * 0.08, 0.0)
        self._neutralized = False

        if self.skill_stage == "first-air":
            if ctx.now >= self.skill_until:
                if self.ledge_jump_double:
                    controller.tap("cross", 0.07)
                    self.skill_stage = "second-air"
                    self.skill_until = ctx.now + self.ledge_second_air_seconds
                    self.current_action = "jak: V15 ledge-hop second jump"
                    return self.current_action
                self.skill_stage = "settle"
                self.skill_until = ctx.now + self.ledge_settle_seconds
            self.current_action = "jak: V15 ledge-hop first air"
            return self.current_action

        if self.skill_stage == "second-air":
            if ctx.now < self.skill_until:
                self.current_action = "jak: V15 ledge-hop climbing"
                return self.current_action
            self.skill_stage = "settle"
            self.skill_until = ctx.now + self.ledge_settle_seconds

        if ctx.now < self.skill_until:
            self.current_action = "jak: V15 ledge-hop landing drive"
            return self.current_action

        motion = max(
            float(ctx.motion),
            float(self.scene_metrics.center_motion),
            float(self.scene_metrics.lower_motion),
        )
        if motion >= self.ledge_success_motion:
            self.ledge_jump_successes += 1
        else:
            self.ledge_jump_failures += 1
        self._finish_skill(ctx)
        self.current_action = (
            f"jak: V15 ledge-hop complete motion={motion:.3f}"
        )
        return self.current_action

    def _service_skill(self, controller: Controller, ctx: ProfileContext) -> str:
        if self.skill_name == "v15_ledge_jump":
            return self._service_v15_ledge_jump(controller, ctx)
        return super()._service_skill(controller, ctx)

    def _pursue_visual_goal(self, controller: Controller, ctx: ProfileContext) -> str:
        goal = self.visual_goal
        heading = self._clamp(goal.x * self.goal_turn_gain, -0.58, 0.58)

        # A stable nearby Scout box is an immediate legitimate progress transaction,
        # regardless of which curriculum milestone happens to be primary.
        if (
            goal.kind == "scout_box"
            and abs(goal.x) <= self.scout_attack_center_x
            and goal.y >= self.scout_attack_close_y
            and ctx.now >= self.scout_retry_cooldown_until
        ):
            self.gameplay_cue = GameplayCue(
                "scout_box", goal.x, goal.y, goal.area, goal.confidence
            )
            self._start_scout_dive(ctx)
            return self._service_skill(controller, ctx)

        if goal.kind == "blue_eco":
            self.gameplay_cue = GameplayCue(
                "blue_eco", goal.x, goal.y, goal.area, goal.confidence
            )
            self.visual_goal_pursuit_ticks += 1
            return self._seek_blue_eco(controller, ctx)

        forward = self.goal_pursuit_forward
        if abs(goal.x) > 0.45:
            forward *= 0.72  # orient before charging off-screen toward the target
        controller.set_left_stick(heading, forward)
        controller.set_right_stick(-heading * self.goal_camera_gain, 0.0)
        self._arm_local_stuck(ctx)
        self._neutralized = False
        self.visual_goal_pursuit_ticks += 1
        self.next_production_action_at = ctx.now + 0.22

        jump_suffix = ""
        if (
            goal.kind in {"orb", "power_cell"}
            and goal.y <= self.goal_high_jump_y
            and abs(goal.x) <= self.goal_jump_center_x
            and ctx.now >= self.next_goal_jump_at
        ):
            controller.tap("cross", 0.07)
            self.next_goal_jump_at = ctx.now + self.goal_jump_cooldown_seconds
            jump_suffix = " + jump-to-reward"

        self.current_action = (
            f"jak: V15 pursue {goal.kind} x={goal.x:+.2f} y={goal.y:.2f} "
            f"score={goal.score:.2f}{jump_suffix}"
        )
        return self.current_action

    def _objective_replan_due(self, ctx: ProfileContext) -> bool:
        if self._visual_goal_actionable():
            return False
        return super()._objective_replan_due(ctx)

    def _service_land_scan(self, controller: Controller, ctx: ProfileContext) -> str:
        was_active = self.land_scan_active
        reason = self.land_scan_reason
        action = super()._service_land_scan(controller, ctx)
        if was_active and not self.land_scan_active:
            # Commit long enough to learn whether the chosen corridor actually goes
            # somewhere. V14's seven-second re-scan cadence caused circles by design.
            self.next_route_bias_at = max(
                self.next_route_bias_at, ctx.now + self.goal_route_commit_seconds
            )
            self.next_objective_replan_scan_at = max(
                self.next_objective_replan_scan_at,
                ctx.now + self.goal_replan_min_gap_seconds,
            )
            if reason.startswith("objective-stall"):
                self.executed_objective_replans += 1
        return action

    def _on_foot(self, controller: Controller, ctx: ProfileContext) -> str:
        # Establish the safety state before any goal attraction. Real water always wins.
        if not self.land_scan_active:
            self._refresh_water_state(ctx)
            if self.water_escape_active:
                return super()._on_foot(controller, ctx)

        if self.skill_active:
            return self._service_skill(controller, ctx)

        self._refresh_visual_goal(ctx)
        self._refresh_ledge_cue(ctx)

        # A strong ledge cue can explain apparent local-stuck behavior. Try the actual
        # platformer move before backing away from a perfectly climbable step.
        if self._ledge_actionable(ctx):
            return self._start_ledge_jump(controller, ctx)

        if self._visual_goal_actionable() and not self.local_stuck_active:
            return self._pursue_visual_goal(controller, ctx)

        action = super()._on_foot(controller, ctx)
        # Any successful goal-aware corridor decision gets a longer commitment window.
        return action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "jak_policy_version": "v15",
                "jak_visual_goal": self.visual_goal.kind,
                "jak_visual_goal_x": round(self.visual_goal.x, 3),
                "jak_visual_goal_y": round(self.visual_goal.y, 3),
                "jak_visual_goal_confidence": round(self.visual_goal.confidence, 3),
                "jak_visual_goal_score": round(self.visual_goal.score, 3),
                "jak_visual_goal_stable_frames": self.visual_goal_stable_frames,
                "jak_visual_goal_actionable": self._visual_goal_actionable(),
                "jak_visual_goal_acquisitions": self.visual_goal_acquisitions,
                "jak_visual_goal_switches": self.visual_goal_switches,
                "jak_visual_goal_lost": self.visual_goal_lost,
                "jak_visual_goal_pursuit_ticks": self.visual_goal_pursuit_ticks,
                "jak_orb_goal_cues": self.orb_goal_cues,
                "jak_cell_goal_cues": self.cell_goal_cues,
                "jak_opportunistic_scout_goals": self.opportunistic_scout_goals,
                "jak_opportunistic_eco_goals": self.opportunistic_eco_goals,
                "jak_goal_scan_biases": self.goal_scan_biases,
                "jak_ledge_confidence": round(self.ledge_cue.confidence, 3),
                "jak_ledge_y": round(self.ledge_cue.y, 3),
                "jak_ledge_row_coverage": round(self.ledge_cue.row_coverage, 3),
                "jak_ledge_open_above": round(self.ledge_cue.open_above, 3),
                "jak_ledge_stable_frames": self.ledge_stable_frames,
                "jak_ledge_jump_attempts": self.ledge_jump_attempts,
                "jak_ledge_jump_double_attempts": self.ledge_jump_double_attempts,
                "jak_ledge_jump_successes": self.ledge_jump_successes,
                "jak_ledge_jump_failures": self.ledge_jump_failures,
                "jak_executed_objective_replans": self.executed_objective_replans,
                "jak_stream_intent": (
                    f"{self.objective.goal} · "
                    + (
                        f"PURSUE {self.visual_goal.kind.upper()}"
                        if self._visual_goal_actionable()
                        else self.objective.subgoal
                    )
                ),
            }
        )
        return state
