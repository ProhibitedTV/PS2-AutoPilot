from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True)
class RuntimeDirective:
    level: int
    reason: str
    stalled_seconds: float


class MaddenRuntimeMonitor:
    """Persistence, progress monitoring, and failure evidence for 24/7 Madden.

    This deliberately watches *semantic progress*, not just moving pixels. Madden's
    attract mode can move forever without making progress, while a legitimate menu
    can be almost static. Unknown screens are captured locally so unattended runs
    automatically build a calibration/debug corpus without committing game imagery.
    """

    GAMEPLAY_PHASES = {"playcall", "pre_snap", "live", "post_play", "kicking"}
    SAFE_PROGRESS_PHASES = {"live"}

    def __init__(self, cfg: dict, runtime_root: Path | None = None) -> None:
        self.cfg = dict(cfg)
        self.root = runtime_root or (Path.cwd() / "runtime")
        self.root.mkdir(parents=True, exist_ok=True)
        self.unknown_dir = self.root / "unknown"
        self.unknown_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "madden-events.jsonl"
        self.session_path = self.root / "session.json"

        self.unknown_after = float(cfg.get("unknown_capture_seconds", 3.0))
        self.unknown_cooldown = float(cfg.get("unknown_capture_cooldown_seconds", 20.0))
        self.menu_stall_seconds = float(cfg.get("menu_progress_timeout_seconds", 18.0))
        self.paused_stall_seconds = float(cfg.get("paused_progress_timeout_seconds", 45.0))
        self.playcall_stall_seconds = float(cfg.get("playcall_progress_timeout_seconds", 24.0))
        self.pre_snap_stall_seconds = float(cfg.get("pre_snap_progress_timeout_seconds", 22.0))
        self.post_play_stall_seconds = float(cfg.get("post_play_progress_timeout_seconds", 18.0))
        self.game_over_stall_seconds = float(cfg.get("game_over_progress_timeout_seconds", 30.0))
        self.recovery_cooldown = float(cfg.get("progress_recovery_cooldown_seconds", 8.0))
        self.max_unknown_captures = max(10, int(cfg.get("max_unknown_captures", 120)))
        self.max_event_log_bytes = max(256_000, int(cfg.get("max_event_log_bytes", 5_000_000)))

        now = time.monotonic()
        self.last_signature: tuple[Any, ...] | None = None
        self.last_progress_at = now
        self.last_event_signature: tuple[Any, ...] | None = None
        self.recovery_level = 0
        self.next_recovery_at = 0.0
        self.unknown_since: float | None = None
        self.last_unknown_capture_at = -1e9
        self.recent_unknown_hashes: deque[str] = deque(maxlen=24)

        self.session_write_retries = 0
        self.session_write_failures = 0
        self.session_write_last_error: str | None = None

        session = self._load_session()
        self.session_started_utc = session.get("session_started_utc") or self._utc_now()
        self.games_started = int(session.get("games_started", 0))
        self.games_completed = int(session.get("games_completed", 0))
        self.hard_recoveries = int(session.get("hard_recoveries", 0))
        self.progress_recoveries = int(session.get("progress_recoveries", 0))
        self.unknown_captures = int(session.get("unknown_captures", 0))
        self.active_game = bool(session.get("active_game", False))
        self._final_seen = False

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _load_session(self) -> dict:
        try:
            return json.loads(self.session_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _record_session_write_failure(self, exc: OSError) -> None:
        self.session_write_failures += 1
        self.session_write_last_error = f"{type(exc).__name__}: {exc}"
        if self.session_write_failures == 1 or self.session_write_failures % 25 == 0:
            print(
                "[madden-runtime] session.json checkpoint skipped after transient file error; "
                "in-memory gameplay state remains authoritative "
                f"(failures={self.session_write_failures}, error={self.session_write_last_error})",
                flush=True,
            )

    def _save_session(self, telemetry: dict | None = None) -> bool:
        payload = {
            "session_started_utc": self.session_started_utc,
            "last_heartbeat_utc": self._utc_now(),
            "games_started": self.games_started,
            "games_completed": self.games_completed,
            "hard_recoveries": self.hard_recoveries,
            "progress_recoveries": self.progress_recoveries,
            "unknown_captures": self.unknown_captures,
            "active_game": self.active_game,
        }
        if telemetry:
            payload.update(
                {
                    "last_phase": telemetry.get("phase"),
                    "last_menu_screen": telemetry.get("menu_screen"),
                    "last_action": telemetry.get("action"),
                    "plays_started": telemetry.get("plays_started"),
                    "plays_completed": telemetry.get("plays_completed"),
                }
            )

        # Windows may temporarily deny replacing a JSON file while an editor,
        # antivirus scanner, OBS helper, or another reader has a handle open. A
        # telemetry checkpoint must never be able to terminate autonomous gameplay.
        # Unique temp names also avoid collisions with stale temp files from a prior
        # process or concurrent diagnostic reader.
        tmp = self.root / f".session-{os.getpid()}-{threading.get_ident()}.tmp"
        try:
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            for attempt in range(3):
                try:
                    tmp.replace(self.session_path)
                    self.session_write_last_error = None
                    return True
                except PermissionError as exc:
                    self.session_write_retries += 1
                    self.session_write_last_error = f"{type(exc).__name__}: {exc}"
                    if attempt < 2:
                        time.sleep(0.01 * (attempt + 1))
                        continue
                    self._record_session_write_failure(exc)
                    return False
        except OSError as exc:
            self._record_session_write_failure(exc)
            return False
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def _rotate_log_if_needed(self) -> None:
        try:
            if not self.events_path.exists() or self.events_path.stat().st_size < self.max_event_log_bytes:
                return
            old = self.events_path.with_suffix(".jsonl.1")
            old.unlink(missing_ok=True)
            self.events_path.replace(old)
        except OSError:
            pass

    def _event(self, kind: str, telemetry: dict, **extra: Any) -> None:
        self._rotate_log_if_needed()
        row = {
            "utc": self._utc_now(),
            "kind": kind,
            "phase": telemetry.get("phase"),
            "menu_screen": telemetry.get("menu_screen"),
            "action": telemetry.get("action"),
            "ocr": str(telemetry.get("ocr_text") or "")[:240],
            "plays_started": telemetry.get("plays_started"),
            "plays_completed": telemetry.get("plays_completed"),
            **extra,
        }
        try:
            with self.events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
        except OSError:
            pass

    @staticmethod
    def _dhash(frame: np.ndarray) -> str:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        tiny = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
        diff = tiny[:, 1:] > tiny[:, :-1]
        value = 0
        for bit in diff.flatten():
            value = (value << 1) | int(bool(bit))
        return f"{value:016x}"

    def _prune_unknowns(self) -> None:
        pngs = sorted(self.unknown_dir.glob("*.png"), key=lambda p: p.stat().st_mtime)
        while len(pngs) > self.max_unknown_captures:
            victim = pngs.pop(0)
            meta = victim.with_suffix(".json")
            victim.unlink(missing_ok=True)
            meta.unlink(missing_ok=True)

    def _capture_unknown(self, frame: np.ndarray, telemetry: dict, now: float) -> None:
        digest = self._dhash(frame)
        if digest in self.recent_unknown_hashes:
            self.last_unknown_capture_at = now
            return

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = self.unknown_dir / f"{stamp}-{digest[:8]}"
        image_path = base.with_suffix(".png")
        meta_path = base.with_suffix(".json")
        try:
            cv2.imwrite(str(image_path), frame)
            metadata = {
                "captured_utc": self._utc_now(),
                "hash": digest,
                "phase": telemetry.get("phase"),
                "menu_screen": telemetry.get("menu_screen"),
                "menu_confidence": telemetry.get("menu_confidence"),
                "menu_reason": telemetry.get("menu_reason"),
                "action": telemetry.get("action"),
                "ocr_text": telemetry.get("ocr_text"),
                "game_state": telemetry.get("game_state"),
                "field_green": telemetry.get("field_green"),
                "motion": telemetry.get("motion"),
            }
            meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
            self.recent_unknown_hashes.append(digest)
            self.last_unknown_capture_at = now
            self.unknown_captures += 1
            self._event("unknown_capture", telemetry, path=str(image_path), hash=digest)
            self._prune_unknowns()
        except OSError:
            self.last_unknown_capture_at = now

    @staticmethod
    def _semantic_signature(telemetry: dict) -> tuple[Any, ...]:
        return (
            telemetry.get("phase"),
            telemetry.get("menu_screen"),
            telemetry.get("plays_started"),
            telemetry.get("plays_completed"),
            telemetry.get("quarter"),
            telemetry.get("down"),
        )

    def _stall_limit(self, telemetry: dict) -> float | None:
        phase = str(telemetry.get("phase") or "")
        screen = str(telemetry.get("menu_screen") or "")
        if phase in self.SAFE_PROGRESS_PHASES:
            return None
        if phase == "playcall":
            return self.playcall_stall_seconds
        if phase == "pre_snap":
            return self.pre_snap_stall_seconds
        if phase == "post_play":
            return self.post_play_stall_seconds
        if phase == "game_over" or screen == "final":
            return self.game_over_stall_seconds
        if phase == "paused":
            # Pause recovery intentionally walks a menu conservatively. A longer
            # budget prevents forensic bundles from firing while the bot is
            # visibly moving toward RESUME GAME one safe row at a time.
            return self.paused_stall_seconds
        if phase in {"menu", "transition"}:
            return self.menu_stall_seconds
        return self.menu_stall_seconds * 1.4

    def _track_game_lifecycle(self, telemetry: dict) -> None:
        phase = str(telemetry.get("phase") or "")
        screen = str(telemetry.get("menu_screen") or "")
        if phase in self.GAMEPLAY_PHASES and not self.active_game:
            self.active_game = True
            self.games_started += 1
            self._event("game_started", telemetry, games_started=self.games_started)

        final_now = phase == "game_over" or screen == "final"
        if final_now and not self._final_seen:
            self._final_seen = True
            if self.active_game:
                self.games_completed += 1
                self.active_game = False
                self._event("game_completed", telemetry, games_completed=self.games_completed)
        elif not final_now:
            self._final_seen = False

        # If we are back at the title/main menu without seeing a final, call it an
        # abandoned game rather than incorrectly counting a completion.
        if screen in {"title", "main_menu"} and self.active_game and phase == "menu":
            self.active_game = False
            self._event("game_abandoned", telemetry)

    def update(
        self,
        frame: np.ndarray,
        telemetry: dict,
        action: str,
        now: float,
    ) -> RuntimeDirective | None:
        telemetry = dict(telemetry)
        telemetry["action"] = action
        signature = self._semantic_signature(telemetry)
        phase = str(telemetry.get("phase") or "")
        screen = str(telemetry.get("menu_screen") or "")

        self._track_game_lifecycle(telemetry)

        if signature != self.last_signature:
            old = self.last_signature
            self.last_signature = signature
            self.last_progress_at = now
            self.recovery_level = 0
            self.next_recovery_at = 0.0
            self._event("transition", telemetry, previous_signature=old, signature=signature)
            self._save_session(telemetry)
        elif phase == "live":
            # A live play can legitimately hold the same semantic label while the
            # ball is moving. The existing motion watchdog remains the right guard.
            self.last_progress_at = now

        if screen == "unknown" and phase in {"menu", "transition"}:
            if self.unknown_since is None:
                self.unknown_since = now
            if (
                now - self.unknown_since >= self.unknown_after
                and now - self.last_unknown_capture_at >= self.unknown_cooldown
            ):
                self._capture_unknown(frame, telemetry, now)
        else:
            self.unknown_since = None

        event_signature = (signature, action)
        if event_signature != self.last_event_signature:
            self.last_event_signature = event_signature
            self._event("action", telemetry)

        limit = self._stall_limit(telemetry)
        if limit is None:
            return None
        stalled = max(0.0, now - self.last_progress_at)
        if stalled < limit or now < self.next_recovery_at:
            return None

        self.recovery_level = min(3, self.recovery_level + 1)
        self.progress_recoveries += 1
        self.next_recovery_at = now + self.recovery_cooldown * self.recovery_level
        reason = f"no semantic progress in {phase or screen or 'unknown'}"
        self._event(
            "progress_recovery_requested",
            telemetry,
            level=self.recovery_level,
            stalled_seconds=round(stalled, 1),
            reason=reason,
        )
        self._save_session(telemetry)
        return RuntimeDirective(self.recovery_level, reason, stalled)

    def note_hard_recovery(self, telemetry: dict | None = None) -> None:
        self.hard_recoveries += 1
        self._save_session(telemetry)

    def telemetry(self, now: float) -> dict:
        return {
            "runtime_hours": round(max(0.0, now - self.last_progress_at) * 0.0 + (time.time() - 0) * 0.0, 1),
            "session_games_started": self.games_started,
            "session_games_completed": self.games_completed,
            "session_progress_recoveries": self.progress_recoveries,
            "session_unknown_captures": self.unknown_captures,
            "semantic_still_seconds": round(max(0.0, now - self.last_progress_at), 1),
            "progress_recovery_level": self.recovery_level,
            "session_write_retries": self.session_write_retries,
            "session_write_failures": self.session_write_failures,
            "session_write_last_error": self.session_write_last_error,
        }
