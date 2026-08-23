from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.semantic_ocr import OCRLine

from .base import ProfileContext
from .jak_and_daxter import JakAndDaxterProfile


class JakAndDaxterV2Profile(JakAndDaxterProfile):
    """Jak boot navigator with a visually verified NEW GAME transaction.

    V1 established a fail-closed calibration profile plus an OCR-verified PRESS START
    exception. V2 extends only that safe boot envelope: the real four-item main menu is
    recognized semantically and Cross is permitted only when the green NEW GAME
    highlight is visually present. Everything beyond that menu remains fail-closed in
    observation mode until separately calibrated.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.main_menu_retry_seconds = max(
            1.5, float(cfg.get("main_menu_retry_seconds", 3.0))
        )
        self.new_game_green_ratio_threshold = max(
            0.005, min(0.50, float(cfg.get("new_game_green_ratio_threshold", 0.04)))
        )
        self.new_game_green_margin = max(
            0.0, min(0.25, float(cfg.get("new_game_green_margin", 0.012)))
        )
        self.main_menu_visible = False
        self.new_game_selected = False
        self.new_game_green_ratio = 0.0
        self.next_main_menu_confirm_at = 0.0
        self.main_menu_confirms = 0

    @staticmethod
    def _line_compact(line: OCRLine) -> str:
        return JakAndDaxterProfile._compact_text(line.text)

    @staticmethod
    def _line_green_ratio(frame: np.ndarray, line: OCRLine) -> float:
        if frame is None or frame.size == 0:
            return 0.0
        height, width = frame.shape[:2]
        x0 = max(0, int(round((line.x - line.width * 0.55) * width)))
        x1 = min(width, int(round((line.x + line.width * 0.55) * width)))
        y0 = max(0, int(round((line.y - line.height * 0.65) * height)))
        y1 = min(height, int(round((line.y + line.height * 0.65) * height)))
        if x1 <= x0 or y1 <= y0:
            return 0.0
        roi = frame[y0:y1, x0:x1]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        # Jak's selected main-menu item is a saturated lime/green while inactive
        # items are pale gray. Keep the threshold broad enough for renderer variance.
        mask = cv2.inRange(
            hsv,
            np.array((28, 85, 85), dtype=np.uint8),
            np.array((100, 255, 255), dtype=np.uint8),
        )
        return float(np.count_nonzero(mask)) / float(max(mask.size, 1))

    def _measure_main_menu_highlight(
        self, frame: np.ndarray, lines: tuple[OCRLine, ...]
    ) -> tuple[bool, float]:
        new_ratios: list[float] = []
        inactive_ratios: list[float] = []
        for line in lines:
            compact = self._line_compact(line)
            ratio = self._line_green_ratio(frame, line)
            if compact == "NEW" or compact == "GAME" or "NEWGAME" in compact:
                if "LOADGAME" not in compact:
                    new_ratios.append(ratio)
            elif "LOADGAME" in compact or "OPTIONS" in compact or compact == "BACK":
                inactive_ratios.append(ratio)

        if not new_ratios:
            return False, 0.0
        new_ratio = max(new_ratios)
        competing = max(inactive_ratios, default=0.0)
        selected = (
            new_ratio >= self.new_game_green_ratio_threshold
            and new_ratio >= competing + self.new_game_green_margin
        )
        return selected, new_ratio

    def _read_ocr_title_gate(self, ctx: ProfileContext) -> bool:
        # Preserve the V1 title-gate semantics, then expand the same semantic read to
        # the known four-item Jak main menu.
        snapshot = self.ocr.read(ctx.frame, ctx.now)
        self.last_ocr_text = snapshot.text
        self.last_ocr_confidence = snapshot.mean_confidence
        compact = self._compact_text(snapshot.text)

        self.title_gate_visible = bool(snapshot.available and "PRESSSTART" in compact)
        self.main_menu_visible = bool(
            snapshot.available
            and "NEWGAME" in compact
            and "LOADGAME" in compact
            and "OPTIONS" in compact
            and "BACK" in compact
        )
        if self.main_menu_visible:
            self.new_game_selected, self.new_game_green_ratio = self._measure_main_menu_highlight(
                ctx.frame, snapshot.lines
            )
        else:
            self.new_game_selected = False
            self.new_game_green_ratio = 0.0

        # The parent uses this boolean only to promote an otherwise unknown frame to
        # a known MENU phase. Actual button ownership is still handled by title/main
        # gate methods below.
        return self.title_gate_visible or self.main_menu_visible

    def _main_menu_gate(self, controller: Controller, ctx: ProfileContext) -> str:
        self._neutral_once(controller)
        if not self.new_game_selected:
            self.current_action = (
                "jak: main menu recognized; NEW GAME highlight unverified; hold inputs"
            )
            return self.current_action

        if ctx.now >= self.next_main_menu_confirm_at:
            controller.tap("cross", 0.08)
            self.main_menu_confirms += 1
            self.next_main_menu_confirm_at = ctx.now + self.main_menu_retry_seconds
            self.current_action = "jak: verified NEW GAME highlight -> CROSS"
        else:
            remaining = max(0.0, self.next_main_menu_confirm_at - ctx.now)
            self.current_action = (
                f"jak: NEW GAME confirmed; wait {remaining:.1f}s for transition"
            )
        return self.current_action

    def _observe_only(self, controller: Controller, ctx: ProfileContext) -> str:
        if self.main_menu_visible:
            return self._main_menu_gate(controller, ctx)
        return super()._observe_only(controller, ctx)

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        # Ensure the verified main-menu transaction has priority in both modes. The
        # fallback to super() may perform a second low-cost OCR read, but the bounded
        # OCR worker returns its cached snapshot until the configured interval elapses.
        self._observe_phase(ctx)
        if self.main_menu_visible:
            return self._main_menu_gate(controller, ctx)
        return super().tick(controller, ctx)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "jak_main_menu_visible": self.main_menu_visible,
                "jak_new_game_selected": self.new_game_selected,
                "jak_new_game_green_ratio": round(self.new_game_green_ratio, 4),
                "jak_main_menu_confirms": self.main_menu_confirms,
            }
        )
        return state
