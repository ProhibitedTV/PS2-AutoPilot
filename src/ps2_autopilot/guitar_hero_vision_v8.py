from __future__ import annotations

from itertools import product

import cv2
import numpy as np

from ps2_autopilot.guitar_hero_vision import (
    LANE_NAMES,
    GuitarHeroObservation,
    GuitarHeroVision,
)


class GuitarHeroVisionV8(GuitarHeroVision):
    """Highway vision that solves the five fret receptors as one geometric object.

    The original detector chose the best green/red/yellow/blue/orange contour
    independently.  On a live Guitar Hero stage that is much too permissive: lights,
    clothes and scenery can provide five individually plausible colored blobs and the
    policy starts pressing random fret buttons.

    V8 instead searches for a jointly ordered, roughly equally-spaced, common-row
    five-lane layout.  Once that layout is stable for a few frames it is lightly
    locked and can survive a note head temporarily occluding a receptor.  Note hits
    are then generated only when a color component enters a narrow arrival zone
    immediately above its locked receptor; distant colored stage pixels never become
    fret presses.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.receptor_candidate_limit = max(2, int(cfg.get("receptor_candidate_limit", 6)))
        self.receptor_joint_min_span = float(cfg.get("receptor_joint_min_span", 0.18))
        self.receptor_joint_max_span = float(cfg.get("receptor_joint_max_span", 0.58))
        self.receptor_joint_max_y_spread = float(cfg.get("receptor_joint_max_y_spread", 0.055))
        self.receptor_lock_threshold = float(cfg.get("receptor_lock_threshold", 0.80))
        self.receptor_lock_frames = max(2, int(cfg.get("receptor_lock_frames", 3)))
        self.receptor_lock_match_radius = float(cfg.get("receptor_lock_match_radius", 0.045))
        self.note_lookahead = float(cfg.get("note_lookahead", 0.18))
        self.note_trigger_gap = float(cfg.get("note_trigger_gap", 0.030))
        self.note_receptor_exclusion = float(cfg.get("note_receptor_exclusion", 0.008))
        self.note_lane_half_width = float(cfg.get("note_lane_half_width", 0.032))
        self.note_min_area = float(cfg.get("note_min_area", 7.0))
        self.note_max_area = float(cfg.get("note_max_area", 1700.0))

        self._candidate_streak = 0
        self._candidate_centers: tuple[tuple[float, float], ...] | None = None
        self._locked_centers: tuple[tuple[float, float], ...] | None = None
        self.layout_locked = False
        self.lock_support = 0
        self.note_gaps: tuple[float | None, ...] = (None,) * 5

    def _lane_candidates(self, mask: np.ndarray) -> list[tuple[float, float, float, float]]:
        """Return (quality, x, y, area) candidates from the receptor band."""

        h, w = mask.shape
        x0 = int(w * self.receptor_x_min)
        x1 = int(w * self.receptor_x_max)
        y0 = int(h * self.receptor_y_min)
        y1 = int(h * self.receptor_y_max)
        roi = mask[y0:y1, x0:x1]
        if roi.size == 0:
            return []

        work = cv2.morphologyEx(roi, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8))
        contours, _ = cv2.findContours(work, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates: list[tuple[float, float, float, float]] = []
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < 10.0 or area > 2200.0:
                continue
            rx, ry, rw, rh = cv2.boundingRect(contour)
            if rw < 3 or rh < 3:
                continue
            aspect = rw / max(rh, 1)
            if not 0.25 <= aspect <= 4.0:
                continue
            nx = (x0 + rx + rw / 2.0) / w
            ny = (y0 + ry + rh / 2.0) / h
            # Receptors are compact and low on the highway.  This score intentionally
            # has no lane-specific expected X; X geometry is solved jointly below.
            compact = min(1.0, area / 90.0)
            low = max(0.0, 1.0 - abs(ny - 0.84) / 0.22)
            aspect_score = max(0.0, 1.0 - abs(np.log(max(aspect, 1e-3))) / 1.4)
            quality = compact * 0.45 + low * 0.35 + aspect_score * 0.20
            candidates.append((quality, float(nx), float(ny), area))

        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[: self.receptor_candidate_limit]

    def _joint_layout(
        self, masks: dict[str, np.ndarray]
    ) -> tuple[tuple[tuple[float, float] | None, ...], float]:
        lanes = [self._lane_candidates(masks[name]) for name in LANE_NAMES]
        if any(not lane for lane in lanes):
            return (None, None, None, None, None), 0.0

        best_score = -1.0
        best: tuple[tuple[float, float], ...] | None = None
        for combo in product(*lanes):
            xs = np.array([item[1] for item in combo], dtype=np.float32)
            ys = np.array([item[2] for item in combo], dtype=np.float32)
            if not np.all(np.diff(xs) > 0.020):
                continue
            span = float(xs[-1] - xs[0])
            if not self.receptor_joint_min_span <= span <= self.receptor_joint_max_span:
                continue
            y_spread = float(np.std(ys))
            if y_spread > self.receptor_joint_max_y_spread:
                continue
            gaps = np.diff(xs)
            mean_gap = float(np.mean(gaps))
            if mean_gap <= 0.0:
                continue
            gap_cv = float(np.std(gaps) / mean_gap)
            if gap_cv > 0.65:
                continue

            candidate_quality = float(np.mean([item[0] for item in combo]))
            y_score = max(0.0, 1.0 - y_spread / self.receptor_joint_max_y_spread)
            spacing_score = max(0.0, 1.0 - gap_cv / 0.65)
            # Broad preferred span only; this is deliberately much less brittle than
            # the old hard-coded per-lane X locations.
            span_score = max(0.0, 1.0 - abs(span - 0.34) / 0.24)
            score = (
                candidate_quality * 0.42
                + y_score * 0.24
                + spacing_score * 0.24
                + span_score * 0.10
            )
            if score > best_score:
                common_y = float(np.median(ys))
                best = tuple((float(x), common_y) for x in xs)
                best_score = score

        if best is None:
            return (None, None, None, None, None), 0.0
        return best, min(1.0, max(0.0, best_score))

    @staticmethod
    def _center_distance(
        a: tuple[tuple[float, float], ...], b: tuple[tuple[float, float], ...]
    ) -> float:
        return float(
            np.mean(
                [
                    ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5
                    for (ax, ay), (bx, by) in zip(a, b, strict=True)
                ]
            )
        )

    def _lock_or_current(
        self,
        masks: dict[str, np.ndarray],
        centers: tuple[tuple[float, float] | None, ...],
        confidence: float,
    ) -> tuple[tuple[tuple[float, float] | None, ...], float]:
        complete = all(center is not None for center in centers)
        if complete and confidence >= self.receptor_lock_threshold:
            concrete = tuple(center for center in centers if center is not None)
            if self._candidate_centers is not None and self._center_distance(
                concrete, self._candidate_centers
            ) <= 0.025:
                self._candidate_streak += 1
            else:
                self._candidate_centers = concrete
                self._candidate_streak = 1
            if self._candidate_streak >= self.receptor_lock_frames:
                if self._locked_centers is None:
                    self._locked_centers = concrete
                else:
                    # Slow update follows small capture/aspect drift without letting a
                    # single noisy frame teleport the strike line.
                    self._locked_centers = tuple(
                        (
                            old[0] * 0.88 + new[0] * 0.12,
                            old[1] * 0.88 + new[1] * 0.12,
                        )
                        for old, new in zip(self._locked_centers, concrete, strict=True)
                    )
        elif confidence < 0.45:
            self._candidate_streak = 0
            self._candidate_centers = None

        self.layout_locked = self._locked_centers is not None
        if self._locked_centers is None:
            self.lock_support = 0
            return centers, confidence

        # A lock is usable only while the current frame still supports the highway in
        # at least three lanes.  This prevents the remembered receptor row from making
        # menus/results look like gameplay after a song ends.
        support = 0
        for lane_name, locked in zip(LANE_NAMES, self._locked_centers, strict=True):
            for _, x, y, _ in self._lane_candidates(masks[lane_name]):
                if abs(x - locked[0]) <= self.receptor_lock_match_radius and abs(y - locked[1]) <= 0.055:
                    support += 1
                    break
        self.lock_support = support
        if support < 3:
            return centers, confidence

        locked_confidence = min(1.0, 0.64 + support * 0.07)
        return self._locked_centers, max(confidence, locked_confidence)

    def _arrival_strength(
        self,
        mask: np.ndarray,
        center: tuple[float, float] | None,
    ) -> tuple[float, float | None]:
        if center is None:
            return 0.0, None
        h, w = mask.shape
        cx = int(center[0] * w)
        cy = int(center[1] * h)
        xhalf = max(5, int(w * self.note_lane_half_width))
        y0 = max(0, cy - int(h * self.note_lookahead))
        y1 = max(y0 + 1, cy - int(h * self.note_receptor_exclusion))
        x0 = max(0, cx - xhalf)
        x1 = min(w, cx + xhalf + 1)
        roi = mask[y0:y1, x0:x1]
        if roi.size == 0:
            return 0.0, None

        work = cv2.morphologyEx(roi, cv2.MORPH_CLOSE, np.ones((3, 3), dtype=np.uint8))
        contours, _ = cv2.findContours(work, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        closest_gap: float | None = None
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < self.note_min_area or area > self.note_max_area:
                continue
            rx, ry, rw, rh = cv2.boundingRect(contour)
            component_cx = x0 + rx + rw / 2.0
            if abs(component_cx - cx) / w > self.note_lane_half_width:
                continue
            bottom_y = y0 + ry + rh
            gap = max(0.0, (cy - bottom_y) / h)
            if closest_gap is None or gap < closest_gap:
                closest_gap = gap

        if closest_gap is None:
            return 0.0, None
        # Binary arrival ownership is intentional.  The old wide density strip began
        # firing tens of pixels early; V8 waits until the note head is immediately
        # above the receptor and lets the runtime's existing arm/rearm logic debounce it.
        return (1.0 if closest_gap <= self.note_trigger_gap else 0.0), closest_gap

    def analyze(self, frame: np.ndarray) -> GuitarHeroObservation:
        resized = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
        masks = {lane: self._mask(hsv, lane) for lane in LANE_NAMES}

        centers, receptor_confidence = self._joint_layout(masks)
        centers, receptor_confidence = self._lock_or_current(
            masks, centers, receptor_confidence
        )

        arrivals: list[float] = []
        gaps: list[float | None] = []
        for index, lane in enumerate(LANE_NAMES):
            strength, gap = self._arrival_strength(masks[lane], centers[index])
            arrivals.append(strength)
            gaps.append(gap)
        self.note_gaps = tuple(gaps)

        sustains = tuple(
            self._sustain(masks[lane], centers[index])
            for index, lane in enumerate(LANE_NAMES)
        )
        active_lane_bonus = min(0.12, sum(value > 0.5 for value in arrivals) * 0.024)
        gameplay_confidence = min(1.0, receptor_confidence * 0.94 + active_lane_bonus)
        (
            save_score,
            main_score,
            setlist_score,
            difficulty_score,
            title_score,
            selected_main,
            selected_diff,
        ) = self._menu_scores(resized)

        return GuitarHeroObservation(
            gameplay_confidence=gameplay_confidence,
            receptor_confidence=receptor_confidence,
            receptor_centers=centers,
            hit_strengths=tuple(arrivals),
            sustains=sustains,
            save_prompt_score=save_score,
            main_menu_score=main_score,
            setlist_score=setlist_score,
            difficulty_score=difficulty_score,
            title_score=title_score,
            selected_main_index=selected_main,
            selected_difficulty_index=selected_diff,
            frame_signature=self._frame_signature(resized),
        )
