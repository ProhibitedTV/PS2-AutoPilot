from __future__ import annotations

import re

from .base import ProfileContext
from .jak_and_daxter_v4 import JakAndDaxterV4Profile


class JakAndDaxterV5Profile(JakAndDaxterV4Profile):
    """Extend V4's memory-card flow through the save-slot selector.

    After the first-run YES/NO prompt, Jak presents a dedicated ``SELECT FILE TO
    SAVE TO`` screen with one or more EMPTY slots plus ``CONTINUE WITHOUT SAVING``
    and ``BACK``. Entering this screen from the create-save transaction starts on
    the first file row, so a single bounded Cross is the safe transaction. We only
    grant that Cross when OCR establishes the dedicated selector and at least one
    EMPTY slot; destructive memory-card wording remains excluded.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.save_file_retry_seconds = max(
            1.5, float(cfg.get("save_file_retry_seconds", 4.0))
        )
        self.save_file_selector_visible = False
        self.save_file_empty_count = 0
        self.next_save_file_action_at = 0.0
        self.save_file_confirms = 0

    @classmethod
    def _save_file_selector_evidence(cls, text: str) -> tuple[bool, int]:
        raw = str(text).upper()
        words = re.sub(r"[^A-Z0-9]+", " ", raw)
        compact = cls._compact_text(raw)
        empty_count = len(re.findall(r"\bEMPTY\b", words))
        destructive = any(marker in compact for marker in cls.SAVE_DESTRUCTIVE_MARKERS)

        # Prefer the exact title, but tolerate OCR splitting/spacing differences.
        exact_title = "SELECTFILETOSAVETO" in compact
        tolerant_title = (
            "SELECTFILE" in compact
            and "SAVE" in compact
            and ("SAVETO" in compact or "TOSAVE" in compact)
        )
        visible = bool(not destructive and empty_count >= 1 and (exact_title or tolerant_title))
        return visible, empty_count

    def _read_ocr_title_gate(self, ctx: ProfileContext) -> bool:
        parent_visible = super()._read_ocr_title_gate(ctx)
        selector_visible, empty_count = self._save_file_selector_evidence(self.last_ocr_text)
        self.save_file_selector_visible = selector_visible
        self.save_file_empty_count = empty_count if selector_visible else 0

        # The selector is its own transaction and must not be mistaken for another
        # main-menu/title action. V4's save YES/NO detector cannot match this screen
        # because it requires both YES and NO, but keep ownership explicit here.
        if selector_visible:
            self.title_gate_visible = False
            self.main_menu_visible = False
            self.save_prompt_visible = False
        return parent_visible or selector_visible

    def _save_file_selector_gate(self, controller, ctx: ProfileContext) -> str:
        self._neutral_once(controller)
        if ctx.now < self.next_save_file_action_at:
            remaining = max(0.0, self.next_save_file_action_at - ctx.now)
            self.current_action = (
                f"jak: save-file selector; wait {remaining:.1f}s for slot transition"
            )
            return self.current_action

        controller.tap("cross", 0.08)
        self.save_file_confirms += 1
        self.next_save_file_action_at = ctx.now + self.save_file_retry_seconds
        self.current_action = (
            f"jak: save-file selector; {self.save_file_empty_count} EMPTY slot(s) -> CROSS first slot"
        )
        return self.current_action

    def tick(self, controller, ctx: ProfileContext) -> str:
        action = super().tick(controller, ctx)
        if self.save_file_selector_visible:
            return self._save_file_selector_gate(controller, ctx)
        return action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "jak_save_file_selector_visible": self.save_file_selector_visible,
                "jak_save_file_empty_count": self.save_file_empty_count,
                "jak_save_file_confirms": self.save_file_confirms,
            }
        )
        return state
