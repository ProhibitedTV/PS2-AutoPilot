from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


LANE_NAMES = ("green", "red", "yellow", "blue", "orange")
LANE_ACTIONS = ("l2", "l1", "r1", "r2", "cross")


@dataclass(frozen=True)
class GuitarHeroObservation:
    gameplay_confidence: float
    receptor_confidence: float
    receptor_centers: tuple[tuple[float, float] | None, ...]
    hit_strengths: tuple[float, ...]
    sustains: tuple[bool, ...]
    save_prompt_score: float
    main_menu_score: float
    setlist_score: float
    difficulty_score: float
    title_score: float
    selected_main_index: int | None
    selected_difficulty_index: int | None
    frame_signature: np.ndarray

    def telemetry(self) -> dict[str, Any]:
        return {
            "gh_gameplay_confidence": round(self.gameplay_confidence, 3),
            "gh_receptor_confidence": round(self.receptor_confidence, 3),
            "gh_hit_strengths": [round(value, 3) for value in self.hit_strengths],
            "gh_sustains": list(self.sustains),
            "gh_save_prompt_score": round(self.save_prompt_score, 3),
            "gh_main_menu_score": round(self.main_menu_score, 3),
            "gh_setlist_score": round(self.setlist_score, 3),
            "gh_difficulty_score": round(self.difficulty_score, 3),
            "gh_title_score": round(self.title_score, 3),
            "gh_selected_main_index": self.selected_main_index,
            "gh_selected_difficulty_index": self.selected_difficulty_index,
        }


class GuitarHeroVision:
    """Low-latency normalized vision for the original PS2 Guitar Hero.

    The note highway is detected from the five colored fret receptors rather than
    fixed desktop pixels. That makes the hit detector tolerant of PCSX2 window size,
    4:3/16:9 presentation differences, and most stage-camera changes. Menu heuristics
    are deliberately conservative; named templates remain the authoritative path.
    """

    def __init__(self, cfg: dict) -> None:
        self.width = max(320, int(cfg.get("vision_width", 640)))
        self.height = max(240, int(cfg.get("vision_height", 480)))
        self.receptor_y_min = float(cfg.get("receptor_y_min", 0.66))
        self.receptor_y_max = float(cfg.get("receptor_y_max", 0.95))
        self.receptor_x_min = float(cfg.get("receptor_x_min", 0.34))
        self.receptor_x_max = float(cfg.get("receptor_x_max", 0.98))
        self.hit_gap_top = float(cfg.get("hit_gap_top", 0.095))
        self.hit_gap_bottom = float(cfg.get("hit_gap_bottom", 0.040))
        self.hit_half_width = float(cfg.get("hit_half_width", 0.020))
        self.sustain_height = float(cfg.get("sustain_height", 0.20))
        self.sustain_half_width = float(cfg.get("sustain_half_width", 0.010))
        self.sustain_min_run = float(cfg.get("sustain_min_run", 0.28))

    @staticmethod
    def _mask(hsv: np.ndarray, lane: str) -> np.ndarray:
        # OpenCV hue is 0..179. High saturation keeps stage lighting and white HUD
        # text from overwhelming the tiny note/receptor regions.
        if lane == "green":
            return cv2.inRange(hsv, (34, 105, 70), (92, 255, 255))
        if lane == "red":
            lo = cv2.inRange(hsv, (0, 125, 75), (8, 255, 255))
            hi = cv2.inRange(hsv, (170, 125, 75), (179, 255, 255))
            return cv2.bitwise_or(lo, hi)
        if lane == "yellow":
            return cv2.inRange(hsv, (19, 105, 90), (38, 255, 255))
        if lane == "blue":
            return cv2.inRange(hsv, (92, 105, 65), (138, 255, 255))
        if lane == "orange":
            return cv2.inRange(hsv, (8, 125, 85), (20, 255, 255))
        raise KeyError(lane)

    @staticmethod
    def _ratio(mask: np.ndarray) -> float:
        if mask.size == 0:
            return 0.0
        return float(np.count_nonzero(mask) / mask.size)

    @staticmethod
    def _frame_signature(frame: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return cv2.resize(gray, (32, 18), interpolation=cv2.INTER_AREA)

    def _find_receptor(
        self,
        mask: np.ndarray,
        *,
        lane_index: int,
    ) -> tuple[float, float, float] | None:
        h, w = mask.shape
        x0 = int(w * self.receptor_x_min)
        x1 = int(w * self.receptor_x_max)
        y0 = int(h * self.receptor_y_min)
        y1 = int(h * self.receptor_y_max)
        roi = mask[y0:y1, x0:x1]
        if roi.size == 0:
            return None

        kernel = np.ones((3, 3), dtype=np.uint8)
        work = cv2.morphologyEx(roi, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(work, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates: list[tuple[float, float, float, float]] = []
        expected_x = 0.54 + lane_index * 0.085
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < 12.0 or area > 2600.0:
                continue
            rx, ry, rw, rh = cv2.boundingRect(contour)
            cx = x0 + rx + rw / 2.0
            cy = y0 + ry + rh / 2.0
            nx = cx / w
            ny = cy / h
            if not (self.receptor_x_min <= nx <= self.receptor_x_max):
                continue
            if not (self.receptor_y_min <= ny <= self.receptor_y_max):
                continue
            if rw < 3 or rh < 3:
                continue
            aspect = rw / max(rh, 1)
            if not (0.28 <= aspect <= 3.6):
                continue
            # Prefer the expected lane neighborhood and lower components; stage
            # lights usually sit higher and are much larger/less receptor-like.
            distance = abs(nx - expected_x) * 2.2 + abs(ny - 0.84) * 0.7
            shape_bonus = min(1.0, area / 80.0)
            score = max(0.0, 1.0 - distance) * (0.55 + 0.45 * shape_bonus)
            candidates.append((score, nx, ny, area))
        if not candidates:
            return None
        _, nx, ny, area = max(candidates, key=lambda item: item[0])
        return nx, ny, area

    @staticmethod
    def _interpolate_centers(
        centers: list[tuple[float, float] | None],
    ) -> list[tuple[float, float] | None]:
        known = [(index, center) for index, center in enumerate(centers) if center is not None]
        if len(known) < 3:
            return centers
        xs = np.array([index for index, _ in known], dtype=np.float32)
        xvals = np.array([center[0] for _, center in known], dtype=np.float32)
        yvals = np.array([center[1] for _, center in known], dtype=np.float32)
        xcoef = np.polyfit(xs, xvals, 1)
        ycoef = np.polyfit(xs, yvals, 1)
        for index, center in enumerate(centers):
            if center is None:
                centers[index] = (
                    float(np.polyval(xcoef, index)),
                    float(np.polyval(ycoef, index)),
                )
        return centers

    def _layout(
        self, masks: dict[str, np.ndarray]
    ) -> tuple[tuple[tuple[float, float] | None, ...], float]:
        found: list[tuple[float, float] | None] = []
        raw_count = 0
        for index, lane in enumerate(LANE_NAMES):
            hit = self._find_receptor(masks[lane], lane_index=index)
            if hit is None:
                found.append(None)
            else:
                raw_count += 1
                found.append((hit[0], hit[1]))

        known = [center for center in found if center is not None]
        if len(known) < 3:
            return tuple(found), raw_count / 5.0

        xvals = [center[0] for center in known]
        yvals = [center[1] for center in known]
        y_spread = float(np.std(yvals))
        span = max(xvals) - min(xvals)
        ordered = all(
            found[index] is None
            or found[index + 1] is None
            or found[index][0] < found[index + 1][0]
            for index in range(4)
        )
        span_score = max(0.0, 1.0 - abs(span - 0.34) / 0.30)
        y_score = max(0.0, 1.0 - y_spread / 0.075)
        order_score = 1.0 if ordered else 0.15
        confidence = min(
            1.0,
            (raw_count / 5.0) * 0.55 + span_score * 0.20 + y_score * 0.15 + order_score * 0.10,
        )
        found = self._interpolate_centers(found)
        # Notes can visually touch a receptor at the strike line and merge into one
        # color contour, pulling that lane's raw centroid upward. Receptors share a
        # common strike row, so pin all recovered lane Y coordinates to the median.
        common_y = float(np.median([center[1] for center in found if center is not None]))
        found = [None if center is None else (center[0], common_y) for center in found]
        return tuple(found), confidence

    def _hit_strength(
        self,
        mask: np.ndarray,
        center: tuple[float, float] | None,
    ) -> float:
        if center is None:
            return 0.0
        h, w = mask.shape
        cx = int(center[0] * w)
        cy = int(center[1] * h)
        xhalf = max(4, int(w * self.hit_half_width))
        y_top = max(0, cy - int(h * self.hit_gap_top))
        y_bottom = max(y_top + 1, cy - int(h * self.hit_gap_bottom))
        x0 = max(0, cx - xhalf)
        x1 = min(w, cx + xhalf + 1)
        roi = mask[y_top:y_bottom, x0:x1]
        if roi.size == 0:
            return 0.0

        # Weight the lower half more heavily. A future gem at the top of the band
        # should not fire as early as one entering the actual strike window.
        split = max(1, roi.shape[0] // 2)
        upper = self._ratio(roi[:split])
        lower = self._ratio(roi[split:])
        return min(1.0, upper * 0.30 + lower * 0.95)

    def _sustain(
        self,
        mask: np.ndarray,
        center: tuple[float, float] | None,
    ) -> bool:
        if center is None:
            return False
        h, w = mask.shape
        cx = int(center[0] * w)
        cy = int(center[1] * h)
        half = max(2, int(w * self.sustain_half_width))
        y_bottom = max(1, cy - int(h * self.hit_gap_top * 0.65))
        y_top = max(0, y_bottom - int(h * self.sustain_height))
        x0 = max(0, cx - half)
        x1 = min(w, cx + half + 1)
        roi = mask[y_top:y_bottom, x0:x1]
        if roi.size == 0:
            return False

        # A sustain is a vertical connected run, not merely another round note head.
        column = np.max((roi > 0).astype(np.uint8), axis=1)
        best = run = 0
        for value in column:
            if value:
                run += 1
                best = max(best, run)
            else:
                run = 0
        return best / max(len(column), 1) >= self.sustain_min_run

    @staticmethod
    def _text_rows(frame: np.ndarray) -> tuple[list[float], float | None]:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        h, w = hsv.shape[:2]
        x0, x1 = int(w * 0.18), int(w * 0.82)
        y0, y1 = int(h * 0.30), int(h * 0.82)
        roi = hsv[y0:y1, x0:x1]
        white = cv2.inRange(roi, (0, 0, 135), (179, 105, 255))
        yellow = cv2.inRange(roi, (17, 85, 95), (43, 255, 255))
        ink = cv2.bitwise_or(white, yellow)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (19, 3))
        merged = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, kernel)
        merged = cv2.dilate(merged, np.ones((2, 5), dtype=np.uint8), iterations=1)
        contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        rows: list[float] = []
        for contour in contours:
            rx, ry, rw, rh = cv2.boundingRect(contour)
            if rw < int(w * 0.09) or rh < 5 or rh > int(h * 0.095):
                continue
            cy = (y0 + ry + rh / 2.0) / h
            if 0.30 <= cy <= 0.82:
                rows.append(float(cy))
        rows.sort()
        collapsed: list[float] = []
        for value in rows:
            if not collapsed or abs(value - collapsed[-1]) > 0.028:
                collapsed.append(value)
            else:
                collapsed[-1] = (collapsed[-1] + value) / 2.0

        ys, _ = np.nonzero(yellow)
        selected_y = None
        if len(ys) >= 25:
            selected_y = (float(np.median(ys)) + y0) / h
        return collapsed, selected_y

    @classmethod
    def _selected_index_for_rows(
        cls, frame: np.ndarray, expected_rows: int
    ) -> tuple[int | None, float]:
        rows, selected_y = cls._text_rows(frame)
        if selected_y is None:
            return None, 0.0
        if len(rows) == expected_rows:
            distances = [abs(selected_y - row) for row in rows]
            index = int(np.argmin(distances))
            if distances[index] <= 0.05:
                return index, 1.0
        # Fallback to broad known GH1 row spacing if morphology merged logo text.
        if expected_rows == 5:
            centers = (0.50, 0.555, 0.61, 0.665, 0.72)
        else:
            centers = (0.43, 0.50, 0.57, 0.64)
        distances = [abs(selected_y - center) for center in centers]
        index = int(np.argmin(distances))
        return (index, 0.55) if distances[index] <= 0.06 else (None, 0.0)

    def _menu_scores(
        self, frame: np.ndarray
    ) -> tuple[float, float, float, float, float, int | None, int | None]:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        h, w = hsv.shape[:2]

        # Save prompt: large dark poster plus centered green YES in the lower-middle.
        save_roi = hsv[int(h * 0.67) : int(h * 0.91), int(w * 0.38) : int(w * 0.72)]
        save_green = cv2.inRange(save_roi, (35, 90, 70), (95, 255, 255))
        save_green_ratio = self._ratio(save_green)
        central = hsv[int(h * 0.15) : int(h * 0.88), int(w * 0.16) : int(w * 0.84)]
        central_dark = float(np.mean(central[:, :, 2] < 95))
        save_score = min(1.0, save_green_ratio * 11.0 + central_dark * 0.22)

        # Main menu: white text stack with one yellow highlighted row.
        menu_roi = hsv[int(h * 0.38) : int(h * 0.80), int(w * 0.18) : int(w * 0.82)]
        white = cv2.inRange(menu_roi, (0, 0, 155), (179, 90, 255))
        yellow = cv2.inRange(menu_roi, (18, 90, 100), (42, 255, 255))
        white_ratio = self._ratio(white)
        yellow_ratio = self._ratio(yellow)
        selected_main, main_row_conf = self._selected_index_for_rows(frame, 5)
        menu_score = min(1.0, white_ratio * 3.4 + yellow_ratio * 12.0 + main_row_conf * 0.28)
        if selected_main is None:
            menu_score *= 0.68

        # Difficulty is another four-row highlighted list. The row-count evidence is
        # what distinguishes it from the five-row main menu without OCR.
        selected_diff, diff_row_conf = self._selected_index_for_rows(frame, 4)
        difficulty_score = min(1.0, white_ratio * 2.3 + yellow_ratio * 10.0 + diff_row_conf * 0.42)
        if selected_diff is None:
            difficulty_score *= 0.55

        # Setlist: notebook paper plus blue selected ink and red margin/doodle ink.
        paper_roi = hsv[int(h * 0.12) : int(h * 0.88), int(w * 0.04) : int(w * 0.94)]
        paper = cv2.inRange(paper_roi, (0, 0, 135), (179, 105, 255))
        blue = cv2.inRange(paper_roi, (95, 80, 55), (145, 255, 255))
        red_lo = cv2.inRange(paper_roi, (0, 90, 55), (10, 255, 255))
        red_hi = cv2.inRange(paper_roi, (170, 90, 55), (179, 255, 255))
        red = cv2.bitwise_or(red_lo, red_hi)
        paper_ratio = self._ratio(paper)
        blue_ratio = self._ratio(blue)
        red_ratio = self._ratio(red)
        setlist_score = min(1.0, paper_ratio * 1.7 + blue_ratio * 10.0 + red_ratio * 2.0)

        # Title: enormous white logo and the white "press any button" line, but no
        # highlighted yellow menu row. Save/main/setlist checks remain authoritative.
        logo_roi = hsv[int(h * 0.12) : int(h * 0.73), int(w * 0.16) : int(w * 0.84)]
        prompt_roi = hsv[int(h * 0.72) : int(h * 0.88), int(w * 0.18) : int(w * 0.82)]
        logo_white = cv2.inRange(logo_roi, (0, 0, 145), (179, 90, 255))
        prompt_white = cv2.inRange(prompt_roi, (0, 0, 145), (179, 100, 255))
        prompt_yellow = cv2.inRange(prompt_roi, (17, 90, 100), (43, 255, 255))
        title_score = min(
            1.0,
            self._ratio(logo_white) * 2.1
            + self._ratio(prompt_white) * 3.0
            - self._ratio(prompt_yellow) * 5.0,
        )
        title_score = max(0.0, title_score)
        return (
            save_score,
            menu_score,
            setlist_score,
            difficulty_score,
            title_score,
            selected_main,
            selected_diff,
        )

    def analyze(self, frame: np.ndarray) -> GuitarHeroObservation:
        resized = cv2.resize(frame, (self.width, self.height), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
        masks = {lane: self._mask(hsv, lane) for lane in LANE_NAMES}
        centers, receptor_confidence = self._layout(masks)
        strengths = tuple(
            self._hit_strength(masks[lane], centers[index])
            for index, lane in enumerate(LANE_NAMES)
        )
        sustains = tuple(
            self._sustain(masks[lane], centers[index])
            for index, lane in enumerate(LANE_NAMES)
        )
        active_lane_bonus = min(0.25, sum(value > 0.025 for value in strengths) * 0.05)
        gameplay_confidence = min(1.0, receptor_confidence * 0.88 + active_lane_bonus)
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
            hit_strengths=strengths,
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


def signature_delta(previous: np.ndarray | None, current: np.ndarray) -> float:
    if previous is None:
        return 1.0
    if previous.shape != current.shape:
        return 1.0
    return float(np.mean(cv2.absdiff(previous, current)) / 255.0)
