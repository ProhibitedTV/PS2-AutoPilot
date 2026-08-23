from __future__ import annotations

import cv2
import numpy as np

from .base import ProfileContext
from .jak_and_daxter_v5 import JakAndDaxterV5Profile


class JakAndDaxterV6Profile(JakAndDaxterV5Profile):
    """Recover the all-empty save-file selector when stylized OCR misses it.

    Live V5 evidence showed the exact ``SELECT FILE TO SAVE TO`` screen remaining
    UNKNOWN for 25 seconds: OCR did not produce enough semantic text to own the
    transaction, so the watchdog correctly stayed neutral. V6 adds a deliberately
    narrow visual fallback calibrated from that real 1920x1080 capture. It requires
    the stable title/menu layout plus all four EMPTY-row text bands before V5 is
    allowed to send its single bounded Cross.
    """

    # Normalized regions calibrated from the live 1920x1080 selector capture. The
    # background remains visible through the translucent menu, so the fallback does
    # not compare raw color. Instead it looks for the repeated bright/edged text
    # structure at the fixed title, four EMPTY rows, and CONTINUE WITHOUT SAVING row.
    SAVE_SELECTOR_TITLE_ROI = (0.23, 0.56, 0.025, 0.090)
    SAVE_SELECTOR_EMPTY_ROIS = (
        (0.34, 0.47, 0.160, 0.220),
        (0.34, 0.47, 0.340, 0.400),
        (0.34, 0.47, 0.520, 0.580),
        (0.34, 0.47, 0.700, 0.760),
    )
    SAVE_SELECTOR_CONTINUE_ROI = (0.18, 0.60, 0.830, 0.900)

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.save_selector_visual_stable_motion = max(
            0.0, min(0.10, float(cfg.get("save_selector_visual_stable_motion", 0.008)))
        )
        self.save_selector_visual_title_white = max(
            0.01, min(0.80, float(cfg.get("save_selector_visual_title_white", 0.16)))
        )
        self.save_selector_visual_title_edge = max(
            0.005, min(0.50, float(cfg.get("save_selector_visual_title_edge", 0.04)))
        )
        self.save_selector_visual_row_white = max(
            0.01, min(0.80, float(cfg.get("save_selector_visual_row_white", 0.08)))
        )
        self.save_selector_visual_row_edge = max(
            0.005, min(0.50, float(cfg.get("save_selector_visual_row_edge", 0.025)))
        )
        self.save_selector_visual_continue_white = max(
            0.01, min(0.80, float(cfg.get("save_selector_visual_continue_white", 0.04)))
        )
        self.save_selector_visual_continue_edge = max(
            0.005, min(0.50, float(cfg.get("save_selector_visual_continue_edge", 0.03)))
        )

        self.save_file_selector_source = "none"
        self.save_selector_visual_row_hits = 0
        self.save_selector_visual_title_white_ratio = 0.0
        self.save_selector_visual_title_edge_ratio = 0.0
        self.save_selector_visual_continue_white_ratio = 0.0
        self.save_selector_visual_continue_edge_ratio = 0.0

    @staticmethod
    def _roi_text_metrics(
        frame: np.ndarray, bounds: tuple[float, float, float, float]
    ) -> tuple[float, float]:
        if frame is None or frame.size == 0:
            return 0.0, 0.0
        height, width = frame.shape[:2]
        x0, x1, y0, y1 = bounds
        roi = frame[
            max(0, int(round(y0 * height))):min(height, int(round(y1 * height))),
            max(0, int(round(x0 * width))):min(width, int(round(x1 * width))),
        ]
        if roi.size == 0:
            return 0.0, 0.0

        # Jak's menu glyphs are bright and close to neutral gray/white even when the
        # world behind the translucent panel is highly saturated orange/green.
        channel_max = roi.max(axis=2)
        channel_min = roi.min(axis=2)
        bright_neutral = (channel_max >= 150) & ((channel_max - channel_min) <= 70)
        white_ratio = float(np.count_nonzero(bright_neutral)) / float(bright_neutral.size)

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 80, 160)
        edge_ratio = float(np.count_nonzero(edges)) / float(max(edges.size, 1))
        return white_ratio, edge_ratio

    def _visual_four_empty_selector(self, ctx: ProfileContext) -> bool:
        title_white, title_edge = self._roi_text_metrics(
            ctx.frame, self.SAVE_SELECTOR_TITLE_ROI
        )
        row_metrics = [
            self._roi_text_metrics(ctx.frame, roi) for roi in self.SAVE_SELECTOR_EMPTY_ROIS
        ]
        continue_white, continue_edge = self._roi_text_metrics(
            ctx.frame, self.SAVE_SELECTOR_CONTINUE_ROI
        )

        self.save_selector_visual_title_white_ratio = title_white
        self.save_selector_visual_title_edge_ratio = title_edge
        self.save_selector_visual_continue_white_ratio = continue_white
        self.save_selector_visual_continue_edge_ratio = continue_edge
        self.save_selector_visual_row_hits = sum(
            1
            for white_ratio, edge_ratio in row_metrics
            if white_ratio >= self.save_selector_visual_row_white
            and edge_ratio >= self.save_selector_visual_row_edge
        )

        compact = self._compact_text(self.last_ocr_text)
        destructive = any(marker in compact for marker in self.SAVE_DESTRUCTIVE_MARKERS)
        return bool(
            not destructive
            and float(ctx.motion) <= self.save_selector_visual_stable_motion
            and title_white >= self.save_selector_visual_title_white
            and title_edge >= self.save_selector_visual_title_edge
            and self.save_selector_visual_row_hits == 4
            and continue_white >= self.save_selector_visual_continue_white
            and continue_edge >= self.save_selector_visual_continue_edge
        )

    def _read_ocr_title_gate(self, ctx: ProfileContext) -> bool:
        parent_visible = super()._read_ocr_title_gate(ctx)
        if self.save_file_selector_visible:
            self.save_file_selector_source = "ocr"
            return True

        visual_visible = self._visual_four_empty_selector(ctx)
        if visual_visible:
            self.save_file_selector_visible = True
            self.save_file_empty_count = 4
            self.save_file_selector_source = "visual-four-empty"
            self.title_gate_visible = False
            self.main_menu_visible = False
            self.save_prompt_visible = False
            return True

        self.save_file_selector_source = "none"
        return parent_visible

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "jak_save_file_selector_source": self.save_file_selector_source,
                "jak_save_selector_visual_row_hits": self.save_selector_visual_row_hits,
                "jak_save_selector_visual_title_white_ratio": round(
                    self.save_selector_visual_title_white_ratio, 4
                ),
                "jak_save_selector_visual_title_edge_ratio": round(
                    self.save_selector_visual_title_edge_ratio, 4
                ),
                "jak_save_selector_visual_continue_white_ratio": round(
                    self.save_selector_visual_continue_white_ratio, 4
                ),
                "jak_save_selector_visual_continue_edge_ratio": round(
                    self.save_selector_visual_continue_edge_ratio, 4
                ),
            }
        )
        return state
