from __future__ import annotations

import cv2
import numpy as np

from .base import ProfileContext
from .jak_and_daxter_v5 import JakAndDaxterV5Profile


class JakAndDaxterV6Profile(JakAndDaxterV5Profile):
    """Recover stylized Jak save transactions when OCR is incomplete.

    V6 originally added a narrow visual fallback for the all-empty save-file selector.
    Live PCSX2 2.6.3 testing later exposed the inverse boundary: if AutoPilot is
    restarted while already sitting on the overwrite YES/NO confirmation, a fresh OCR
    instance may fail to recover enough stylized text to identify the prompt. The
    production policy then correctly fails closed, but cannot make progress.

    Keep semantic menu evidence strongest, retain the existing four-empty selector
    fallback, and add a second layout-specific visual signature for the save
    confirmation. It requires the distinctive four message bands plus a lime YES/NO
    choice in the calibrated bottom row, so it does not turn arbitrary UNKNOWN screens
    into generic confirmation presses.
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

    # Live overwrite-confirmation layout from PCSX2 2.6.3 at 1024x576. These bands
    # cover the three centered explanatory rows and the separate "DO YOU WISH..." row.
    # The YES/NO choices reuse V4's independently calibrated SAVE_CHOICE_ROIS.
    SAVE_CONFIRM_MESSAGE_ROIS = (
        (0.09, 0.82, 0.285, 0.355),
        (0.12, 0.84, 0.355, 0.425),
        (0.30, 0.66, 0.425, 0.495),
        (0.12, 0.67, 0.655, 0.735),
    )

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

        # The overwrite layout is intentionally more tolerant than OCR but still asks
        # for every fixed message band plus a verified highlighted choice. The moving
        # translucent world background is not used as a stability requirement.
        self.save_confirm_visual_message_white = max(
            0.005, min(0.50, float(cfg.get("save_confirm_visual_message_white", 0.018)))
        )
        self.save_confirm_visual_message_edge = max(
            0.003, min(0.40, float(cfg.get("save_confirm_visual_message_edge", 0.012)))
        )
        self.save_confirm_visual_required_hits = max(
            3,
            min(
                len(self.SAVE_CONFIRM_MESSAGE_ROIS),
                int(cfg.get("save_confirm_visual_required_hits", 4)),
            ),
        )

        self.save_file_selector_source = "none"
        self.save_selector_visual_row_hits = 0
        self.save_selector_visual_title_white_ratio = 0.0
        self.save_selector_visual_title_edge_ratio = 0.0
        self.save_selector_visual_continue_white_ratio = 0.0
        self.save_selector_visual_continue_edge_ratio = 0.0

        self.save_prompt_visual_message_hits = 0
        self.save_prompt_visual_choice_visible = False
        self.save_prompt_visual_fallbacks = 0

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

    def _visual_save_confirmation(self, ctx: ProfileContext) -> bool:
        metrics = [
            self._roi_text_metrics(ctx.frame, roi) for roi in self.SAVE_CONFIRM_MESSAGE_ROIS
        ]
        self.save_prompt_visual_message_hits = sum(
            1
            for white_ratio, edge_ratio in metrics
            if white_ratio >= self.save_confirm_visual_message_white
            and edge_ratio >= self.save_confirm_visual_message_edge
        )

        (
            yes_selected,
            no_selected,
            yes_ratio,
            no_ratio,
        ) = self._visual_save_choice_evidence(ctx.frame)
        self.save_prompt_visual_choice_visible = bool(yes_selected or no_selected)

        compact = self._compact_text(self.last_ocr_text)
        destructive = any(marker in compact for marker in self.SAVE_DESTRUCTIVE_MARKERS)
        visible = bool(
            not destructive
            and self.save_prompt_visual_choice_visible
            and self.save_prompt_visual_message_hits >= self.save_confirm_visual_required_hits
        )
        if not visible:
            return False

        # Populate V4's ordinary save-prompt state so its bounded LEFT->YES->CROSS
        # transaction remains the only code allowed to act on the confirmation.
        self.save_prompt_visible = True
        self.save_prompt_kind = "overwrite"
        self.save_prompt_marker_count = max(self.save_prompt_marker_count, 1)
        self.save_yes_selected = yes_selected
        self.save_no_selected = no_selected
        self.save_yes_green_ratio = yes_ratio
        self.save_no_green_ratio = no_ratio
        self.title_gate_visible = False
        self.main_menu_visible = False
        self.save_file_selector_visible = False
        self.save_file_empty_count = 0
        self.save_file_selector_source = "suppressed-by-visual-save-confirmation"
        return True

    def _read_ocr_title_gate(self, ctx: ProfileContext) -> bool:
        parent_visible = super()._read_ocr_title_gate(ctx)

        # A semantic title/main-menu/save prompt is stronger evidence than V6's
        # geometry-only selector fallback. Live PCSX2 2.6.3 exposed the failure mode:
        # the OVERWRITE YES/NO screen happened to satisfy the old four-row text
        # geometry and was repeatedly reclassified as the slot selector. Never let a
        # weak visual fallback erase an already-verified parent transaction.
        if self.save_prompt_visible or self.main_menu_visible or self.title_gate_visible:
            self.save_file_selector_visible = False
            self.save_file_empty_count = 0
            self.save_file_selector_source = "suppressed-by-stronger-menu"
            return True

        # On a fresh AutoPilot restart there may be no useful OCR history at all. The
        # live overwrite screen has a distinctive fixed message/choice layout, so use
        # that signature before trusting possibly-stale selector OCR or the weaker
        # four-empty geometry fallback.
        if self._visual_save_confirmation(ctx):
            self.save_prompt_visual_fallbacks += 1
            return True

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
                "jak_save_prompt_visual_message_hits": self.save_prompt_visual_message_hits,
                "jak_save_prompt_visual_choice_visible": self.save_prompt_visual_choice_visible,
                "jak_save_prompt_visual_fallbacks": self.save_prompt_visual_fallbacks,
            }
        )
        return state
