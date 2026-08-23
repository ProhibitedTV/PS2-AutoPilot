from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .observability import JsonlWriter


class VerboseRuntimeTrace:
    """Dense-but-bounded telemetry stream for live tuning and postmortems.

    The normal observer stays optimized around meaningful decisions. This companion
    trace records a full telemetry snapshot at a low fixed cadence plus a focused
    spatial stream. It is intentionally not frame-rate logging: a 24/7 process needs
    evidence, not millions of nearly identical rows.
    """

    def __init__(self, cfg: dict, root: Path) -> None:
        self.enabled = bool(cfg.get("enabled", True))
        self.verbose_console = bool(cfg.get("verbose_console", True))
        max_bytes = int(cfg.get("max_log_bytes", 8_000_000))
        self.verbose_writer = JsonlWriter(root / "verbose.jsonl", max_bytes)
        self.spatial_writer = JsonlWriter(root / "spatial.jsonl", max_bytes)
        self.verbose_seconds = max(0.25, float(cfg.get("verbose_log_seconds", 1.0)))
        self.spatial_seconds = max(0.20, float(cfg.get("spatial_log_seconds", 0.75)))
        self.console_seconds = max(0.5, float(cfg.get("spatial_console_seconds", 2.0)))
        self.last_verbose = -1e9
        self.last_spatial = -1e9
        self.last_console = -1e9

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    @staticmethod
    def _spatial_payload(state: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "phase",
            "game_state",
            "menu_screen",
            "possession",
            "possession_confidence",
            "play_intent",
            "action",
            "spatial_enabled",
            "spatial_reason",
            "spatial_fresh",
            "spatial_age",
            "spatial_players",
            "spatial_player_candidates",
            "spatial_controlled_x",
            "spatial_controlled_y",
            "spatial_controlled_confidence",
            "spatial_ball_x",
            "spatial_ball_y",
            "spatial_ball_confidence",
            "spatial_target_x",
            "spatial_target_y",
            "spatial_target_confidence",
            "spatial_open_x",
            "spatial_open_confidence",
            "spatial_processing_ms",
            "spatial_policy_mode",
            "spatial_policy_reason",
            "spatial_overrides",
            "motion",
            "motion_target",
            "motion_target_y",
            "field_green",
            "field_center",
        )
        return {key: state.get(key) for key in keys if key in state}

    def _console_line(self, state: dict[str, Any]) -> None:
        if not self.verbose_console:
            return
        phase = str(state.get("phase") or "?").upper()
        role = str(state.get("possession") or "unknown").upper()
        role_conf = float(state.get("possession_confidence") or 0.0)
        players = int(state.get("spatial_players") or 0)
        ball_conf = float(state.get("spatial_ball_confidence") or 0.0)
        target_conf = float(state.get("spatial_target_confidence") or 0.0)
        target_x = float(state.get("spatial_target_x") or 0.0)
        target_y = float(state.get("spatial_target_y") or 0.0)
        open_x = float(state.get("spatial_open_x") or 0.0)
        open_conf = float(state.get("spatial_open_confidence") or 0.0)
        cpu = float(state.get("spatial_processing_ms") or 0.0)
        mode = str(state.get("spatial_policy_mode") or "fallback")
        stamp = datetime.now().strftime("%H:%M:%S")
        print(
            f"[{stamp}] SPATIAL {phase:<9} role={role}:{role_conf:.2f} "
            f"players={players:02d} ball={ball_conf:.2f} "
            f"target=({target_x:+.2f},{target_y:+.2f})/{target_conf:.2f} "
            f"open={open_x:+.2f}/{open_conf:.2f} mode={mode} cpu={cpu:.1f}ms",
            flush=True,
        )

    def record(self, decision_id: int, state: dict[str, Any], now: float) -> None:
        if not self.enabled:
            return
        utc = self._utc_now()
        if now - self.last_verbose >= self.verbose_seconds:
            # Full state is deliberate here. This is the forensic trace users can
            # hand back after an unattended failure without guessing which fields
            # would have mattered ahead of time.
            self.verbose_writer.write(
                {
                    "utc": utc,
                    "kind": "verbose",
                    "decision_id": decision_id,
                    "state": state,
                }
            )
            self.last_verbose = now

        if state.get("spatial_enabled") and now - self.last_spatial >= self.spatial_seconds:
            self.spatial_writer.write(
                {
                    "utc": utc,
                    "kind": "spatial",
                    "decision_id": decision_id,
                    **self._spatial_payload(state),
                }
            )
            self.last_spatial = now

        if (
            self.verbose_console
            and state.get("spatial_enabled")
            and str(state.get("phase") or "") in {"pre_snap", "live", "kicking"}
            and now - self.last_console >= self.console_seconds
        ):
            self._console_line(state)
            self.last_console = now
