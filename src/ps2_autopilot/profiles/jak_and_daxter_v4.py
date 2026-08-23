from __future__ import annotations

import re

import cv2
import numpy as np

from .base import ProfileContext
from .jak_and_daxter_v3 import JakAndDaxterV3Profile


class JakAndDaxterV4Profile(JakAndDaxterV3Profile):
    """Production hardening for Jak's boot/menu boundary.

    V3 still depended too heavily on a perfect OCR read of the four-item main menu.
    V4 adds renderer-tolerant visual signatures for the lime-green NEW GAME row and
    save-file YES/NO prompts, while keeping every unrecognized state fail-closed.
    """

    # Normalized regions from the real 16:9 PCSX2 capture. They target the text rows,
    # not the moving background, so the selected lime NEW GAME row remains distinctive.
    MAIN_MENU_ROIS = {
        "new": (0.31, 0.50, 0.235, 0.315),
        "load": (0.30, 0.51, 0.365, 0.460),
        "options": (0.30, 0.51, 0.495, 0.585),
        "back": (0.34, 0.46, 0.630, 0.715),
    }

    # The first-run save prompt is horizontal near the bottom of the render. Jak uses
    # the same saturated lime highlight here as on the main menu. These bounds were
    # calibrated from a real 1024x576 capture and are normalized for other window sizes.
    SAVE_CHOICE_ROIS = {
        "yes": (0.33, 0.415, 0.72, 0.84),
        "no": (0.415, 0.50, 0.72, 0.84),
    }
    SAVE_DESTRUCTIVE_MARKERS = ("ERASE", "DELETE", "FORMAT")

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

        self.save_choice_green_threshold = max(
            0.01, min(0.75, float(cfg.get("save_choice_green_threshold", 0.06)))
        )
        self.save_choice_green_margin = max(
            0.0, min(0.50, float(cfg.get("save_choice_green_margin", 0.04)))
        )
        self.save_prompt_select_settle_seconds = max(
            0.15, float(cfg.get("save_prompt_select_settle_seconds", 0.30))
        )
        self.save_prompt_retry_seconds = max(
            1.5, float(cfg.get("save_prompt_retry_seconds", 4.0))
        )

        self.main_menu_detection_source = "none"
        self.main_menu_ocr_markers = 0
        self.main_menu_visual_green_ratio = 0.0
        self.main_menu_visual_competing_ratio = 0.0

        self.save_prompt_visible = False
        self.save_prompt_kind = "none"
        self.save_prompt_marker_count = 0
        self.save_yes_selected = False
        self.save_no_selected = False
        self.save_yes_green_ratio = 0.0
        self.save_no_green_ratio = 0.0
        self.save_target_requested_at: float | None = None
        self.next_save_prompt_action_at = 0.0
        self.save_prompt_selects = 0
        self.save_prompt_confirms = 0

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

    def _visual_save_choice_evidence(self, frame: np.ndarray) -> tuple[bool, bool, float, float]:
        yes_ratio = self._green_ratio(frame, self.SAVE_CHOICE_ROIS["yes"])
        no_ratio = self._green_ratio(frame, self.SAVE_CHOICE_ROIS["no"])
        yes_selected = (
            yes_ratio >= self.save_choice_green_threshold
            and yes_ratio >= no_ratio + self.save_choice_green_margin
        )
        no_selected = (
            no_ratio >= self.save_choice_green_threshold
            and no_ratio >= yes_ratio + self.save_choice_green_margin
        )
        return yes_selected, no_selected, yes_ratio, no_ratio

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

    @classmethod
    def _save_prompt_evidence(cls, text: str) -> tuple[bool, str, int]:
        raw = str(text).upper()
        words = re.sub(r"[^A-Z0-9]+", " ", raw)
        compact = cls._compact_text(raw)
        has_yes_no = bool(re.search(r"\bYES\b", words) and re.search(r"\bNO\b", words))
        destructive = any(marker in compact for marker in cls.SAVE_DESTRUCTIVE_MARKERS)

        markers = (
            "JAKANDDAXTER" in compact,
            "MEMORYCARD" in compact,
            "GAMEDATA" in compact,
            "SAVE" in compact,
            "CREATE" in compact,
            "OVERWRITE" in compact,
        )
        marker_count = sum(bool(value) for value in markers)
        identity = any(("JAKANDDAXTER" in compact, "MEMORYCARD" in compact, "GAMEDATA" in compact))
        save_action = any(("SAVE" in compact, "CREATE" in compact, "OVERWRITE" in compact))
        visible = bool(
            has_yes_no
            and not destructive
            and save_action
            and (identity or "OVERWRITE" in compact)
            and marker_count >= 2
        )
        if "CREATE" in compact:
            kind = "create"
        elif "OVERWRITE" in compact:
            kind = "overwrite"
        elif visible:
            kind = "save"
        else:
            kind = "none"
        return visible, kind, marker_count

    def _read_ocr_title_gate(self, ctx: ProfileContext) -> bool:
        snapshot = self.ocr.read(ctx.frame, ctx.now)
        self.last_ocr_text = snapshot.text
        self.last_ocr_confidence = snapshot.mean_confidence
        compact = self._compact_text(snapshot.text)

        title_visible = bool(snapshot.available and "PRESSSTART" in compact)
        marker_count = self._menu_marker_count(snapshot.text) if snapshot.available else 0
        visual_selected, visual_ratio, competing = self._visual_main_menu_evidence(ctx.frame)
        save_visible, save_kind, save_marker_count = self._save_prompt_evidence(snapshot.text)
        if not snapshot.available:
            save_visible = False
            save_kind = "none"
            save_marker_count = 0

        self.save_prompt_visible = save_visible
        self.save_prompt_kind = save_kind
        self.save_prompt_marker_count = save_marker_count
        if self.save_prompt_visible:
            (
                self.save_yes_selected,
                self.save_no_selected,
                self.save_yes_green_ratio,
                self.save_no_green_ratio,
            ) = self._visual_save_choice_evidence(ctx.frame)
        else:
            self.save_yes_selected = False
            self.save_no_selected = False
            self.save_yes_green_ratio = 0.0
            self.save_no_green_ratio = 0.0
            self.save_target_requested_at = None

        semantic_menu = bool(snapshot.available and marker_count >= self.main_menu_ocr_quorum)
        visual_fallback = bool(
            visual_selected
            and (marker_count >= 1 or float(ctx.motion) <= self.main_menu_visual_stable_motion)
        )
        self.main_menu_visible = bool(not self.save_prompt_visible and (semantic_menu or visual_fallback))
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
            self.title_gate_visible = bool(title_visible and not self.save_prompt_visible)

        return self.title_gate_visible or self.main_menu_visible or self.save_prompt_visible

    def _save_prompt_gate(self, controller, ctx: ProfileContext) -> str:
        # Semantic save evidence owns this narrow transaction. Destructive card actions
        # are explicitly excluded by _save_prompt_evidence, so LEFT only targets YES.
        self._neutral_once(controller)
        if ctx.now < self.next_save_prompt_action_at:
            remaining = max(0.0, self.next_save_prompt_action_at - ctx.now)
            self.current_action = (
                f"jak: {self.save_prompt_kind} save prompt; wait {remaining:.1f}s"
            )
            return self.current_action

        if self.save_yes_selected:
            controller.tap("cross", 0.08)
            self.save_prompt_confirms += 1
            self.save_target_requested_at = None
            self.next_save_prompt_action_at = ctx.now + self.save_prompt_retry_seconds
            self.current_action = (
                f"jak: {self.save_prompt_kind} save prompt; verified YES -> CROSS"
            )
            return self.current_action

        if self.save_no_selected:
            controller.tap("left", 0.08)
            self.save_prompt_selects += 1
            self.save_target_requested_at = ctx.now
            self.next_save_prompt_action_at = ctx.now + self.save_prompt_select_settle_seconds
            self.current_action = (
                f"jak: {self.save_prompt_kind} save prompt; NO selected -> LEFT toward YES"
            )
            return self.current_action

        # OCR can remain stable while the highlight animation is between frames. After
        # one bounded LEFT request, confirming is safe because the exact prompt has
        # already been semantically verified and LEFT is idempotent at the YES edge.
        if (
            self.save_target_requested_at is not None
            and ctx.now - self.save_target_requested_at >= self.save_prompt_select_settle_seconds
        ):
            controller.tap("cross", 0.08)
            self.save_prompt_confirms += 1
            self.save_target_requested_at = None
            self.next_save_prompt_action_at = ctx.now + self.save_prompt_retry_seconds
            self.current_action = (
                f"jak: {self.save_prompt_kind} save prompt; LEFT settled -> CROSS"
            )
            return self.current_action

        controller.tap("left", 0.08)
        self.save_prompt_selects += 1
        self.save_target_requested_at = ctx.now
        self.next_save_prompt_action_at = ctx.now + self.save_prompt_select_settle_seconds
        self.current_action = (
            f"jak: {self.save_prompt_kind} save prompt; highlight unclear -> LEFT toward YES"
        )
        return self.current_action

    def tick(self, controller, ctx: ProfileContext) -> str:
        action = super().tick(controller, ctx)
        if self.save_prompt_visible:
            return self._save_prompt_gate(controller, ctx)
        return action

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
                "jak_save_prompt_visible": self.save_prompt_visible,
                "jak_save_prompt_kind": self.save_prompt_kind,
                "jak_save_prompt_markers": self.save_prompt_marker_count,
                "jak_save_yes_selected": self.save_yes_selected,
                "jak_save_no_selected": self.save_no_selected,
                "jak_save_yes_green_ratio": round(self.save_yes_green_ratio, 4),
                "jak_save_no_green_ratio": round(self.save_no_green_ratio, 4),
                "jak_save_prompt_selects": self.save_prompt_selects,
                "jak_save_prompt_confirms": self.save_prompt_confirms,
            }
        )
        return state
