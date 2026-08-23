from __future__ import annotations

import re

from ps2_autopilot.madden_runtime import MaddenRuntimeMonitor


_GAMEPLAY_PHASES = {"playcall", "pre_snap", "live", "post_play", "kicking"}


def compact_ocr(text: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def semantic_context(telemetry: dict) -> str | None:
    """Return a known non-menu context for otherwise UNKNOWN screen telemetry.

    `menu_screen=unknown` is expected during ordinary field gameplay because the
    menu classifier is intentionally menu-centric. It must not be treated as an
    unresolved navigation screen when stronger gameplay/presentation evidence is
    available.
    """

    phase = str(telemetry.get("phase") or "").lower()
    game_state = str(telemetry.get("game_state") or "").lower()
    compact = compact_ocr(telemetry.get("ocr_text"))

    if "PICKAPLAY" in compact:
        return "playcall"

    quarter_break = (
        "HALFTIME" in compact
        or "ENDOFHALF" in compact
        or (
            "ENDOF" in compact
            and any(token in compact for token in ("QUARTER", "QUAR", "QTR"))
        )
    )
    if quarter_break:
        return "presentation"

    try:
        field_green = float(telemetry.get("field_green") or 0.0)
    except (TypeError, ValueError):
        field_green = 0.0

    if game_state == "live_play" and field_green >= 0.30:
        return "field"
    if phase in _GAMEPLAY_PHASES:
        return "field"
    return None


class ContextAwareMaddenRuntimeMonitor(MaddenRuntimeMonitor):
    """Runtime monitor that distinguishes unknown menus from known game contexts."""

    def _contextualize(self, telemetry: dict) -> dict:
        state = dict(telemetry)
        screen = str(state.get("menu_screen") or "").lower()
        phase = str(state.get("phase") or "").lower()
        if screen != "unknown" or phase not in {"menu", "transition"}:
            return state

        context = semantic_context(state)
        if context is None:
            return state

        state["raw_menu_screen"] = "unknown"
        state["menu_screen"] = context
        state["menu_reason"] = f"known {context} context; suppress navigation recovery"
        state["navigation_unknown_suppressed"] = True
        return state

    def _stall_limit(self, telemetry: dict) -> float | None:
        if str(telemetry.get("menu_screen") or "").lower() in {
            "field",
            "presentation",
            "playcall",
        }:
            return None
        return super()._stall_limit(telemetry)

    def update(self, frame, telemetry: dict, action: str, now: float):
        return super().update(frame, self._contextualize(telemetry), action, now)
