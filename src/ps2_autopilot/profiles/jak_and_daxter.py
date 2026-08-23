from __future__ import annotations

from enum import Enum
import random
import re

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.semantic_ocr import SemanticOCR

from .base import GameProfile, ProfileContext


class JakPhase(str, Enum):
    UNKNOWN = "unknown"
    MENU = "menu"
    GAMEPLAY = "gameplay"
    CUTSCENE = "cutscene"
    PAUSED = "paused"
    DEATH = "death"


class JakAndDaxterProfile(GameProfile):
    """Conservative second-game foundation for Jak and Daxter: The Precursor Legacy.

    Madden's policy is intentionally not reused here. A 3D platformer needs continuous
    analog locomotion, camera steering, jump timing, hazard/enemy perception and
    checkpoint recovery rather than football/menu assumptions.

    Observation mode remains fail-closed for uncalibrated game states, with one narrow
    exception: an OCR-verified ``PRESS START`` title gate may press START. That boot
    action is explicit, bounded, and safe enough to let unattended calibration proceed
    past the title screen without turning UNKNOWN into a generic button-mashing state.
    """

    name = "jak_and_daxter"

    MENU_MARKERS = ("title", "main_menu", "new_game", "load_game", "menu")
    GAMEPLAY_MARKERS = ("gameplay", "world", "explore", "hud")
    CUTSCENE_MARKERS = ("cutscene", "cinematic", "dialog", "dialogue")
    PAUSE_MARKERS = ("pause", "paused")
    DEATH_MARKERS = ("death", "game_over", "restart", "checkpoint_retry")

    def __init__(self, cfg: dict) -> None:
        self.mode = str(cfg.get("mode", "observe")).strip().lower()
        if self.mode not in {"observe", "explore"}:
            raise ValueError("Jak profile mode must be 'observe' or 'explore'")

        self.template_threshold = max(0.50, min(0.99, float(cfg.get("template_threshold", 0.84))))
        self.menu_action_seconds = max(0.5, float(cfg.get("menu_action_seconds", 1.4)))
        self.death_action_seconds = max(0.5, float(cfg.get("death_action_seconds", 1.5)))
        self.explore_burst_min_seconds = max(0.20, float(cfg.get("explore_burst_min_seconds", 0.8)))
        self.explore_burst_max_seconds = max(
            self.explore_burst_min_seconds,
            float(cfg.get("explore_burst_max_seconds", 1.8)),
        )
        self.explore_forward = max(0.1, min(1.0, float(cfg.get("explore_forward", 0.82))))
        self.explore_turn = max(0.0, min(0.8, float(cfg.get("explore_turn", 0.28))))
        self.camera_turn = max(0.0, min(0.7, float(cfg.get("camera_turn", 0.18))))
        self.jump_probability = max(0.0, min(0.5, float(cfg.get("jump_probability", 0.08))))
        self.random = random.Random(int(cfg.get("random_seed", 2001)))

        # Shared low-frequency semantic OCR. This is primarily for explicit boot/menu
        # text gates such as PRESS START; it does not grant generic UNKNOWN states
        # permission to act.
        self.ocr = SemanticOCR(
            interval_seconds=float(cfg.get("ocr_interval_seconds", 0.85)),
            min_width=int(cfg.get("ocr_min_width", 960)),
            max_width=int(cfg.get("ocr_max_width", 1280)),
            min_confidence=float(cfg.get("ocr_min_confidence", 0.42)),
            enabled=bool(cfg.get("ocr_enabled", True)),
            intra_op_num_threads=int(cfg.get("ocr_intra_threads", 2)),
            inter_op_num_threads=int(cfg.get("ocr_inter_threads", 1)),
            use_orientation_classifier=False,
            async_enabled=True,
            bootstrap_sync=True,
        )
        self.title_start_retry_seconds = max(
            1.5, float(cfg.get("title_start_retry_seconds", 3.0))
        )

        self.phase = JakPhase.UNKNOWN
        self.current_action = "jak: boot / awaiting calibrated state"
        self.next_action_at = 0.0
        self.last_template_name: str | None = None
        self.last_template_score = 0.0
        self.last_ocr_text = ""
        self.last_ocr_confidence = 0.0
        self.title_gate_visible = False
        self.next_title_start_at = 0.0
        self.title_start_presses = 0
        self.phase_changes = 0
        self.explore_bursts = 0
        self.jump_attempts = 0
        self.menu_confirms = 0
        self.death_confirms = 0
        self._neutralized = False

    @classmethod
    def phase_for_template(cls, name: str | None) -> JakPhase:
        value = str(name or "").lower()
        if any(marker in value for marker in cls.DEATH_MARKERS):
            return JakPhase.DEATH
        if any(marker in value for marker in cls.PAUSE_MARKERS):
            return JakPhase.PAUSED
        if any(marker in value for marker in cls.CUTSCENE_MARKERS):
            return JakPhase.CUTSCENE
        if any(marker in value for marker in cls.GAMEPLAY_MARKERS):
            return JakPhase.GAMEPLAY
        if any(marker in value for marker in cls.MENU_MARKERS):
            return JakPhase.MENU
        return JakPhase.UNKNOWN

    @staticmethod
    def _compact_text(text: str) -> str:
        return re.sub(r"[^A-Z0-9]+", "", str(text).upper())

    def _read_ocr_title_gate(self, ctx: ProfileContext) -> bool:
        snapshot = self.ocr.read(ctx.frame, ctx.now)
        self.last_ocr_text = snapshot.text
        self.last_ocr_confidence = snapshot.mean_confidence
        compact = self._compact_text(snapshot.text)
        self.title_gate_visible = bool(snapshot.available and "PRESSSTART" in compact)
        return self.title_gate_visible

    def _set_phase(self, phase: JakPhase) -> JakPhase:
        if phase != self.phase:
            self.phase = phase
            self.phase_changes += 1
            self._neutralized = False
        return phase

    def _observe_phase(self, ctx: ProfileContext) -> JakPhase:
        match = ctx.template
        self.last_template_name = None if match is None else match.name
        self.last_template_score = 0.0 if match is None else float(match.score)

        phase = JakPhase.UNKNOWN
        if match is not None and match.score >= self.template_threshold:
            phase = self.phase_for_template(match.name)

        # Only use OCR as a narrow semantic fallback when templates have not already
        # established a stronger state. PRESS START is a known boot menu, not generic
        # evidence that Cross/Start should be tried elsewhere.
        if phase == JakPhase.UNKNOWN and self._read_ocr_title_gate(ctx):
            phase = JakPhase.MENU
        elif phase != JakPhase.UNKNOWN:
            self.title_gate_visible = False

        return self._set_phase(phase)

    def _neutral_once(self, controller: Controller) -> None:
        if self._neutralized:
            return
        controller.release_all()
        controller.neutral_sticks()
        self._neutralized = True

    def _title_gate(self, controller: Controller, ctx: ProfileContext) -> str:
        self._neutral_once(controller)
        if ctx.now >= self.next_title_start_at:
            controller.tap("start", 0.08)
            self.title_start_presses += 1
            self.next_title_start_at = ctx.now + self.title_start_retry_seconds
            self.current_action = "jak: OCR verified PRESS START -> START"
        else:
            remaining = max(0.0, self.next_title_start_at - ctx.now)
            self.current_action = f"jak: PRESS START gate; wait {remaining:.1f}s for transition"
        return self.current_action

    def _observe_only(self, controller: Controller, ctx: ProfileContext) -> str:
        if self.title_gate_visible:
            return self._title_gate(controller, ctx)
        self._neutral_once(controller)
        template = self.last_template_name or "none"
        self.current_action = (
            f"jak: observe {self.phase.value}; template={template} "
            f"score={self.last_template_score:.2f}; hold inputs"
        )
        return self.current_action

    def _explore_gameplay(self, controller: Controller, ctx: ProfileContext) -> str:
        if ctx.now < self.next_action_at:
            return self.current_action

        heading_x = self.random.uniform(-self.explore_turn, self.explore_turn)
        camera_x = self.random.uniform(-self.camera_turn, self.camera_turn)
        controller.set_left_stick(heading_x, self.explore_forward)
        controller.set_right_stick(camera_x, 0.0)
        self._neutralized = False
        self.explore_bursts += 1

        jumped = False
        if self.random.random() < self.jump_probability:
            controller.tap("cross", 0.08)
            self.jump_attempts += 1
            jumped = True

        duration = self.random.uniform(self.explore_burst_min_seconds, self.explore_burst_max_seconds)
        self.next_action_at = ctx.now + duration
        suffix = " + jump" if jumped else ""
        self.current_action = (
            f"jak: calibrated exploration forward x={heading_x:+.2f} "
            f"camera={camera_x:+.2f}{suffix}"
        )
        return self.current_action

    def tick(self, controller: Controller, ctx: ProfileContext) -> str:
        phase = self._observe_phase(ctx)

        # PRESS START remains safe in both observe/explore modes because it requires
        # explicit OCR evidence and has its own bounded retry timer.
        if self.title_gate_visible:
            return self._title_gate(controller, ctx)

        if self.mode == "observe":
            return self._observe_only(controller, ctx)

        if phase in {JakPhase.UNKNOWN, JakPhase.CUTSCENE}:
            self._neutral_once(controller)
            self.current_action = f"jak: {phase.value}; fail closed / hold inputs"
            return self.current_action

        if phase == JakPhase.GAMEPLAY:
            return self._explore_gameplay(controller, ctx)

        self._neutral_once(controller)
        if ctx.now < self.next_action_at:
            return self.current_action

        if phase == JakPhase.MENU:
            controller.tap("cross", 0.08)
            self.menu_confirms += 1
            self.next_action_at = ctx.now + self.menu_action_seconds
            self.current_action = "jak: calibrated menu -> CROSS"
        elif phase == JakPhase.PAUSED:
            controller.tap("start", 0.08)
            self.next_action_at = ctx.now + self.menu_action_seconds
            self.current_action = "jak: calibrated pause -> START"
        elif phase == JakPhase.DEATH:
            controller.tap("cross", 0.08)
            self.death_confirms += 1
            self.next_action_at = ctx.now + self.death_action_seconds
            self.current_action = "jak: calibrated death/checkpoint -> CROSS"
        return self.current_action

    def recover(self, controller: Controller) -> str:
        # Until Jak-specific stuck/death geometry is calibrated, the raw-motion
        # watchdog must never inject Madden/generic menu sequences into the game.
        controller.release_all()
        controller.neutral_sticks()
        self._neutralized = True
        self.current_action = "jak: watchdog neutral hold; await calibrated recovery state"
        return self.current_action

    def telemetry(self, ctx: ProfileContext) -> dict:
        ocr_telemetry = self.ocr.telemetry(ctx.now)
        return {
            "game_phase": self.phase.value,
            "jak_phase": self.phase.value,
            "jak_mode": self.mode,
            "jak_template": self.last_template_name,
            "jak_template_score": round(self.last_template_score, 3),
            "jak_motion": round(float(ctx.motion), 4),
            "jak_ocr_text": self.last_ocr_text,
            "jak_ocr_confidence": round(self.last_ocr_confidence, 3),
            "jak_title_gate_visible": self.title_gate_visible,
            "jak_title_start_presses": self.title_start_presses,
            "jak_phase_changes": self.phase_changes,
            "jak_explore_bursts": self.explore_bursts,
            "jak_jump_attempts": self.jump_attempts,
            "jak_menu_confirms": self.menu_confirms,
            "jak_death_confirms": self.death_confirms,
            **ocr_telemetry,
        }
