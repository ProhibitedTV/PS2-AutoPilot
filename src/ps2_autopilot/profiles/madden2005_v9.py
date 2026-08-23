from __future__ import annotations

from dataclasses import replace
import re

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_menu import MaddenScreen, MenuAssessment
from ps2_autopilot.madden_ocr import MaddenOCR
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005 import MaddenPhase, Possession
from .madden2005_v8 import Madden2005V8Profile


_NFL_TEAMS = {
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET",
    "GB", "HOU", "IND", "JAX", "KC", "MIA", "MIN", "NE", "NO", "NYG", "NYJ", "OAK",
    "PHI", "PIT", "SD", "SEA", "SF", "STL", "TB", "TEN", "WAS",
}
_SCORE_RE = re.compile(r"^\d{1,2}$")
_BARE_QUARTER_RE = re.compile(r"^[1-4](?:ST|ND|RD|TH)$", re.IGNORECASE)


class Madden2005V9Profile(Madden2005V8Profile):
    """Broadcast/performance hardening with play-clock-aware pre-snap behavior.

    Long-run traces showed stale defensive possession evidence surviving into an
    offensive pre-snap. The old policy then waited for the opponent to snap until
    the semantic watchdog intervened roughly twenty seconds later. v0.6.1 treats
    play-call role evidence as per-play evidence and uses safe Cross probes before
    the play clock can expire. On defense, Cross merely changes the selected player;
    on offense, the same probe snaps the ball.
    """

    name = "madden2005"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)

        self.ocr = MaddenOCR(
            enabled=bool(cfg.get("ocr_enabled", True)),
            interval_seconds=float(cfg.get("ocr_interval_seconds", 0.85)),
            min_width=int(cfg.get("ocr_min_width", 960)),
            max_width=int(cfg.get("ocr_max_width", 1280)),
            min_confidence=float(cfg.get("ocr_min_confidence", 0.42)),
            intra_op_num_threads=int(cfg.get("ocr_intra_op_num_threads", 2)),
            inter_op_num_threads=int(cfg.get("ocr_inter_op_num_threads", 1)),
            use_orientation_classifier=bool(cfg.get("ocr_use_orientation_classifier", False)),
        )

        self.play_clock_urgent_seconds = max(2, int(cfg.get("play_clock_urgent_seconds", 8)))
        self.pre_snap_failsafe_seconds = max(
            self.pre_snap_wait + 0.5,
            float(cfg.get("pre_snap_failsafe_seconds", 5.0)),
        )
        self.pre_snap_probe_interval_seconds = max(
            0.55, float(cfg.get("pre_snap_probe_interval_seconds", 1.15))
        )
        self.pre_snap_max_failsafe_probes = max(
            1, int(cfg.get("pre_snap_max_failsafe_probes", 3))
        )
        self.playcall_role_fresh_seconds = max(
            4.0, float(cfg.get("playcall_role_fresh_seconds", 18.0))
        )

        self.current_playcall_role = Possession.UNKNOWN
        self.current_playcall_role_at = -1e9
        self.pre_snap_urgency_probes = 0
        self.pre_snap_stale_role_downgrades = 0

    def _transition_phase(self, new_phase: MaddenPhase, now: float) -> None:
        old = self.phase
        super()._transition_phase(new_phase, now)
        if self.phase == old:
            return

        if new_phase == MaddenPhase.PLAYCALL:
            self.current_playcall_role = Possession.UNKNOWN
            self.current_playcall_role_at = -1e9
            self.possession_confidence = min(self.possession_confidence, 0.50)

        if new_phase == MaddenPhase.PRE_SNAP:
            self.pre_snap_urgency_probes = 0

    def _quarter_evidence_is_real(self) -> bool:
        text = self.last_ocr.text.upper()
        if "QTR" in text or "QUARTER" in text:
            return True
        return any(
            line.y <= 0.25 and _BARE_QUARTER_RE.fullmatch(line.text.strip())
            for line in self.last_ocr.lines
        )

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)

        # Live v0.6.1 OCR exposed Madden's native banner as OFFENSEPICKAPLAY.
        # The generic classifier did not include this wording, so a valid playcall
        # screen fell into MENU/UNKNOWN recovery and could receive Triangle. Treat
        # whitespace/punctuation-insensitive PICK A PLAY as authoritative playcall
        # evidence and bind OFFENSE/DEFENSE to this specific play.
        compact = re.sub(r"[^A-Z]", "", self.last_ocr.text.upper())
        if "PICKAPLAY" in compact:
            self.menu_assessment = MenuAssessment(
                MaddenScreen.PLAYCALL,
                0.98,
                "Madden pick-a-play banner",
            )
            if self.phase != MaddenPhase.PLAYCALL:
                self._transition_phase(MaddenPhase.PLAYCALL, ctx.now)
            if "OFFENSE" in compact:
                self._set_possession(Possession.OFFENSE, 0.97)
                self.playcall_role_reason = "OFFENSE PICK A PLAY"
            elif "DEFENSE" in compact:
                self._set_possession(Possession.DEFENSE, 0.97)
                self.playcall_role_reason = "DEFENSE PICK A PLAY"

        # A global bare-ordinal fallback can encounter down text such as
        # "1ST AND 10". Only keep that quarter when the score bug supplied a
        # dedicated ordinal/QTR line.
        if self.situation.quarter is not None and not self._quarter_evidence_is_real():
            self.situation = replace(self.situation, quarter=None)

        if self.phase == MaddenPhase.PLAYCALL and self.possession_confidence >= 0.84:
            reason = str(getattr(self, "playcall_role_reason", ""))
            if reason and reason not in {"none", "ambiguous", "awaiting playcall OCR"}:
                self.current_playcall_role = self.possession
                self.current_playcall_role_at = ctx.now
        return obs

    def _fresh_playcall_role(self, now: float) -> bool:
        return (
            self.current_playcall_role != Possession.UNKNOWN
            and self.current_playcall_role_at > -1e8
            and now - self.current_playcall_role_at <= self.playcall_role_fresh_seconds
        )

    def _fresh_defense_role(self, now: float) -> bool:
        return self._fresh_playcall_role(now) and self.current_playcall_role == Possession.DEFENSE

    def _safe_snap_switch_probe(self, controller: Controller, now: float, reason: str) -> str:
        controller.neutral_sticks()
        controller.tap("cross", 0.06)

        # When current-play OCR confidently says DEFENSE, Cross is a player switch,
        # not evidence that we caused the snap. Avoid poisoning snap-causality
        # possession inference if the CPU happens to snap immediately afterward.
        if not self._fresh_defense_role(now):
            self.last_snap_at = now
        self.snap_attempts += 1
        self.pre_snap_urgency_probes += 1
        self.next_action_at = now + self.pre_snap_probe_interval_seconds
        self.current_action = (
            f"pre-snap URGENCY: {reason} -> X snap/switch probe "
            f"{self.pre_snap_urgency_probes}/{self.pre_snap_max_failsafe_probes}"
        )
        return self.current_action

    def _pre_snap(self, controller: Controller, now: float) -> str:
        idle_for = max(0.0, now - self.phase_since)
        play_clock = self.situation.play_clock_seconds

        # Wait long enough for the first current pre-snap OCR refresh, avoiding a
        # stale :02 token carried over from the previous presentation frame.
        if (
            idle_for >= min(1.0, self.pre_snap_wait)
            and play_clock is not None
            and play_clock <= self.play_clock_urgent_seconds
            and now >= self.next_action_at
            and self.pre_snap_urgency_probes < self.pre_snap_max_failsafe_probes
        ):
            return self._safe_snap_switch_probe(controller, now, f"play clock {play_clock}s")

        if (
            self.possession == Possession.DEFENSE
            and self.possession_confidence >= 0.62
            and not self._fresh_defense_role(now)
            and idle_for >= self.pre_snap_wait
        ):
            # Demote stale previous-play evidence. Keep the enum value for history,
            # but below the threshold the parent policy treats the state as
            # unconfirmed and emits an early Cross probe instead of waiting 20s.
            self.possession_confidence = min(self.possession_confidence, 0.44)
            self.pre_snap_stale_role_downgrades += 1

        if (
            idle_for >= self.pre_snap_failsafe_seconds
            and now >= self.next_action_at
            and self.pre_snap_urgency_probes < self.pre_snap_max_failsafe_probes
        ):
            return self._safe_snap_switch_probe(
                controller,
                now,
                f"failsafe {idle_for:.1f}s at line",
            )

        return super()._pre_snap(controller, now)

    def _broadcast_score(self) -> tuple[str | None, int | None, str | None, int | None]:
        """Extract two score-bug team/score pairs from top-screen OCR when present."""

        lines = [line for line in self.last_ocr.lines if line.y <= 0.33]
        pairs: list[tuple[str, int]] = []
        for index, line in enumerate(lines):
            team = line.text.strip().upper()
            if team not in _NFL_TEAMS:
                continue
            for candidate in lines[index + 1 : index + 4]:
                score_text = candidate.text.strip()
                if _SCORE_RE.fullmatch(score_text):
                    score = int(score_text)
                    if 0 <= score <= 99:
                        pairs.append((team, score))
                        break
            if len(pairs) >= 2:
                break
        if len(pairs) < 2:
            return None, None, None, None
        return pairs[0][0], pairs[0][1], pairs[1][0], pairs[1][1]

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        team_a, score_a, team_b, score_b = self._broadcast_score()
        state.update(
            {
                "play_clock_seconds": self.situation.play_clock_seconds,
                "play_clock_urgent": (
                    self.situation.play_clock_seconds is not None
                    and self.situation.play_clock_seconds <= self.play_clock_urgent_seconds
                ),
                "goal_to_go": self.situation.goal_to_go,
                "current_playcall_role": self.current_playcall_role.value,
                "current_playcall_role_fresh": self._fresh_playcall_role(ctx.now),
                "pre_snap_urgency_probes": self.pre_snap_urgency_probes,
                "pre_snap_stale_role_downgrades": self.pre_snap_stale_role_downgrades,
                "ocr_processing_ms": round(self.ocr.last_processing_ms, 2),
                "ocr_runs": self.ocr.runs,
                "broadcast_team_a": team_a,
                "broadcast_score_a": score_a,
                "broadcast_team_b": team_b,
                "broadcast_score_b": score_b,
            }
        )
        return state
