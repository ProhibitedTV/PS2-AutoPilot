from __future__ import annotations

import cv2
import numpy as np

from .base import ProfileContext
from .jak_and_daxter import JakPhase
from .jak_and_daxter_v3 import JakAndDaxterV3Profile


class JakAndDaxterV4Profile(JakAndDaxterV3Profile):
    """Production hardening for Jak's boot/menu boundary.

    V3 still depended too heavily on a perfect OCR read of the four-item main menu.
    V4 adds a renderer-tolerant visual signature for the lime-green NEW GAME row,
    tolerates partial OCR, and tells the global motion watchdog when a known static
    title/menu/cutscene is intentionally safe to leave alone.
    """

    # Normalized regions from the real 16:9 PCSX2 capture. They target the text rows,
    # not the moving background, so the selected lime NEW GAME row remains distinctive.
    MAIN_MENU_ROIS = {
        "new": (0.31, 0.50, 0.235, 0.315),
        "load": (0.30, 0.51, 0.365, 0.460),
        "options": (0.30, 0.51, 0.495, 0.585),
        "back": (0.34, 0.46, 0.630, 0.715),
    }

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.main_menu_visual_green_threshold = max(
            0.02, min(0.75, float(cfg.get("main_menu_visual_green_threshold", 0.12)))
        )
        self.main_menu_visual_competing_max = max(
            0.0, min(0.25, float(cfg.get("main_menu_visual_competing_max", 0.035)))
        )
        self.main_menu_visual_margin = max(
            0.0, min(0.50, float(cfg.get("main_menu_visual_margin", 0.08)))
        )
        self.main_menu_visual_stable_motion = max(
            0.0, min(0.20, float(cfg.get("main_menu_visual_stable_motion", 0.02)))
        )
        self.main_menu_ocr_quorum = max(2, min(4, int(cfg.get("main_menu_ocr_quorum", 3))))

        self.main_menu_detection_source = "none"
        self.main_menu_ocr_markers = 0
        self.main_menu_visual_green_ratio = 0.0
        self.main_menu_visual_competing_ratio = 0.0
        self.motion_watchdog_suppressions = 0

    @staticmethod
    def _green_ratio(frame: np.ndarray, bounds: tuple[float, float, float, float]) -> float:
        if frame is None or frame.size == 0:
            return 0.0
        height, width = frame.shape[:2]
        x0, x1, y0, y1 = bounds
        roi = frame[
            max(0, int(round(y0 * height))):min(height, int(round(y1 * height))),
            max(0, int(round(x0 * width))):min(width, int(round(x1 * width))),
        ]
        if roi.size == 0:
            return 0.0
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(
            hsv,
            np.array((28, 85, 85), dtype=np.uint8),
            np.array((100, 255, 255), dtype=np.uint8),
        )
        return float(np.count_nonzero(mask)) / float(max(mask.size, 1))

    def _visual_main_menu_evidence(self, frame: np.ndarray) -> tuple[bool, float, float]:
        ratios = {name: self._green_ratio(frame, roi) for name, roi in self.MAIN_MENU_ROIS.items()}
        selected = ratios["new"]
        competing = max(ratios["load"], ratios["options"], ratios["back"])
        verified = (
            selected >= self.main_menu_visual_green_threshold
            and competing <= self.main_menu_visual_competing_max
            and selected >= competing + self.main_menu_visual_margin
        )
        return verified, selected, competing

    @classmethod
    def _menu_marker_count(cls, text: str) -> int:
        compact = cls._compact_text(text)
        markers = (
            "NEWGAME" in compact,
            "LOADGAME" in compact,
            "OPTION" in compact,
            "BACK" in compact,
        )
        return sum(bool(value) for value in markers)

    def _read_ocr_title_gate(self, ctx: ProfileContext) -> bool:
        snapshot = self.ocr.read(ctx.frame, ctx.now)
        self.last_ocr_text = snapshot.text
        self.last_ocr_confidence = snapshot.mean_confidence
        compact = self._compact_text(snapshot.text)

        title_visible = bool(snapshot.available and "PRESSSTART" in compact)
        marker_count = self._menu_marker_count(snapshot.text) if snapshot.available else 0
        visual_selected, visual_ratio, competing = self._visual_main_menu_evidence(ctx.frame)

        semantic_menu = bool(snapshot.available and marker_count >= self.main_menu_ocr_quorum)
        visual_fallback = bool(
            visual_selected
            and (marker_count >= 1 or float(ctx.motion) <= self.main_menu_visual_stable_motion)
        )
        self.main_menu_visible = semantic_menu or visual_fallback
        self.main_menu_ocr_markers = marker_count
        self.main_menu_visual_green_ratio = visual_ratio
        self.main_menu_visual_competing_ratio = competing

        if self.main_menu_visible:
            line_selected, line_ratio = self._measure_main_menu_highlight(ctx.frame, snapshot.lines)
            self.new_game_selected = bool(visual_selected or line_selected)
            self.new_game_green_ratio = max(visual_ratio, line_ratio)
            if semantic_menu and visual_fallback:
                self.main_menu_detection_source = "ocr+visual"
            elif semantic_menu:
                self.main_menu_detection_source = "ocr-quorum"
            else:
                self.main_menu_detection_source = "visual-fallback"
            self.title_gate_visible = False
        else:
            self.new_game_selected = False
            self.new_game_green_ratio = 0.0
            self.main_menu_detection_source = "none"
            self.title_gate_visible = title_visible

        return self.title_gate_visible or self.main_menu_visible

    def suppress_motion_watchdog(self, ctx: ProfileContext) -> bool:
        del ctx
        suppress = bool(
            self.title_gate_visible
            or self.main_menu_visible
            or self.phase == JakPhase.CUTSCENE
        )
        if suppress:
            self.motion_watchdog_suppressions += 1
        return suppress

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "jak_main_menu_detection_source": self.main_menu_detection_source,
                "jak_main_menu_ocr_markers": self.main_menu_ocr_markers,
                "jak_main_menu_visual_green_ratio": round(self.main_menu_visual_green_ratio, 4),
                "jak_main_menu_visual_competing_ratio": round(
                    self.main_menu_visual_competing_ratio, 4
                ),
                "jak_motion_watchdog_suppressions": self.motion_watchdog_suppressions,
            }
        )
        return state
