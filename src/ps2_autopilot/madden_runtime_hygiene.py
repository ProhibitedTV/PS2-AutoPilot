from __future__ import annotations

import re

from ps2_autopilot.madden_runtime import MaddenRuntimeMonitor


_GAMEPLAY_PHASES = {"playcall", "pre_snap", "live", "post_play", "kicking"}


def compact_ocr(text: str | None) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def semantic_context(telemetry: dict) -> str | None:
    """Return a known non-menu context for otherwise UNKNOWN screen telemetry.

    `menu_screen=unknown` is expected during ordinary field gameplay and Madden's
    broadcast presentation because the menu classifier is intentionally menu-centric.
    It must not be treated as unresolved navigation when stronger gameplay,
    presentation, or statistics evidence is available.
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

    # Stats pages are known navigation states, not mystery screens. Keep them
    # separate from passive presentation because the policy should deliberately
    # back out with Triangle until it reaches the parent pause/postgame menu.
    stats_menu_hits = sum(
        marker in compact
        for marker in ("STATSINFO", "INDIVIDUAL", "SCORING", "DRIVESUMMARY")
    )
    if "STATSINFO" in compact and stats_menu_hits >= 3:
        return "stats"

    stats_table_hits = sum(
        marker in compact
        for marker in (
            "GAMESTATS",
            "TOTALOFFENSE",
            "RUSHINGYARDS",
            "PASSINGYARDS",
            "FIRSTDOWNS",
            "PRYARDS",
            "KRYARDS",
            "TOTALYARDS",
            "GIVEAWAYS",
            "TAKEAWAYS",
        )
    )
    if ("GAMESTATS" in compact and stats_table_hits >= 3) or stats_table_hits >= 5:
        return "stats"

    # Live calibration produced normal Madden presentation screens such as
    # Current Drive and the Instant Replay control overlay. These are broadcast
    # content, not navigation failures. Avoid treating the pause menu's single
    # INSTANT REPLAY option as an active replay by requiring companion markers.
    if any(marker in compact for marker in ("CURRENTDRIVE", "TIMEOFPOSSESSION", "HIDECONTROLS")):
        return "presentation"
    replay_hits = sum(
        marker in compact
        for marker in ("INSTANTREPLAY", "REWIND", "FORWARD", "BACKWARD", "HIDECONTROLS")
    )
    if replay_hits >= 2:
        return "presentation"

    try:
        field_green = float(telemetry.get("field_green") or 0.0)
    except (TypeError, ValueError):
        field_green = 0.0

    # Formation/overhead field shots can arrive while the semantic phase is still
    # MENU/TRANSITION. A real score bug plus visible turf is enough to suppress
    # destructive menu backout while the phase catches up.
    scorebug_hits = sum(marker in compact for marker in ("DOWN", "TOGO", "QTR", "CLOCK", "MPH"))
    if field_green >= 0.18 and scorebug_hits >= 3:
        return "field"

    try:
        spatial_players = int(telemetry.get("spatial_players") or 0)
    except (TypeError, ValueError):
        spatial_players = 0
    spatial_fresh = bool(telemetry.get("spatial_fresh"))
    if field_green >= 0.15 and spatial_fresh and spatial_players >= 4:
        return "field"

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
            "stats",
        }:
            return None
        return super()._stall_limit(telemetry)

    def update(self, frame, telemetry: dict, action: str, now: float):
        return super().update(frame, self._contextualize(telemetry), action, now)
