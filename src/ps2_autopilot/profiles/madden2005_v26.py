from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import re

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_ocr import OCRLine
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005 import MaddenPhase, PlayIntent, Possession, QueuedTap
from .madden2005_v25 import Madden2005V25Profile


@dataclass(frozen=True)
class PlayCardCandidate:
    label: str
    button: str
    score: float
    confidence: float
    x: float


class Madden2005V26Profile(Madden2005V25Profile):
    """Turn the proven Madden lifecycle into more deliberate football.

    V25 is good at surviving Madden 2005, but much of the football policy still
    descends from the early autonomous prototype: choose one of the visible plays
    with weighted randomness, sprint immediately on many runs, and sprinkle special
    moves into the ballcarrier cadence regardless of down-and-distance.

    V26 keeps every lifecycle/special-teams safety fix and adds a conservative
    football layer grounded in Madden 2005's actual mechanics:

    * call run/pass from down-and-distance with deliberate tendency breaking;
    * read OCR play names and prefer a visible play card matching that intent;
    * recognize visible punt/field-goal cards on fourth down instead of blindly
      treating every fourth down as normal offense;
    * let run blocking develop before accelerating north/south;
    * reserve dives for goal-line/short-yardage situations instead of random use.

    This deliberately does not pretend that generic blob tracking can identify an
    open receiver or diagnose man/zone coverage. Passing and defensive contact keep
    the confidence-gated V23/V24 behavior until perception can support those reads.
    """

    name = "madden2005"

    RUN_TERMS = {
        "DIVE",
        "ISO",
        "SLAM",
        "DRAW",
        "TOSS",
        "PITCH",
        "SWEEP",
        "COUNTER",
        "STRETCH",
        "TRAP",
        "BLAST",
        "POWER",
        "OFFTACKLE",
    }
    PASS_TERMS = {
        "SLANT",
        "SLANTS",
        "CROSS",
        "CURL",
        "HITCH",
        "POST",
        "CORNER",
        "DRAG",
        "SCREEN",
        "FADE",
        "STREAK",
        "SEAM",
        "VERT",
        "VERTS",
        "FLAT",
        "OPTION",
        "COMEBACK",
        "OUTS",
    }
    COVERAGE_TERMS = {
        "COVER",
        "ZONE",
        "MAN",
        "SPY",
        "CONTAIN",
        "CLOUD",
        "ROB",
        "QUARTER",
    }
    PRESSURE_TERMS = {
        "BLITZ",
        "DOG",
        "FIRE",
        "SMOKE",
        "STUNT",
        "BUCK",
        "THUNDER",
        "STORM",
    }
    SPECIAL_TERMS = {"PUNT", "FIELDGOAL", "FG"}
    HEADER_TERMS = {
        "PICKAPLAY",
        "OFFENSEPICKAPLAY",
        "DEFENSEPICKAPLAY",
        "FORMATION",
        "ASKMADDEN",
        "PLAYCALL",
    }

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.playcard_min_confidence = max(
            0.35, min(0.90, float(cfg.get("playcard_min_confidence", 0.50)))
        )
        self.run_patience_seconds = max(
            0.25, min(1.25, float(cfg.get("run_patience_seconds", 0.62)))
        )
        self.run_action_seconds = max(
            0.45, min(1.40, float(cfg.get("run_action_seconds", 0.78)))
        )
        self.recent_offensive_intents: deque[PlayIntent] = deque(maxlen=4)
        self.recent_play_labels: deque[str] = deque(maxlen=6)

        self.playcall_decision_reason = "awaiting situation"
        self.playcall_selection_mode = "legacy-fallback"
        self.playcall_selected_label: str | None = None
        self.playcall_selected_button: str | None = None
        self.playcall_semantic_selections = 0
        self.playcall_semantic_fallbacks = 0
        self.playcall_formation_entries = 0
        self.fourth_down_special_candidates = 0
        self.run_patience_holds = 0
        self.run_short_yardage_dives = 0
        self.run_discipline_actions = 0

    @staticmethod
    def _compact_play_text(text: str | None) -> str:
        return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())

    @classmethod
    def _face_button_for_x(cls, x: float) -> str:
        if x < 0.39:
            return "square"
        if x > 0.61:
            return "circle"
        return "cross"

    @classmethod
    def _term_hits(cls, compact: str, terms: set[str]) -> int:
        return sum(term in compact for term in terms)

    def _intent_with_tendency_break(
        self,
        run_probability: float,
        *,
        reason: str,
    ) -> PlayIntent:
        run_probability = max(0.04, min(0.96, float(run_probability)))
        last_two = list(self.recent_offensive_intents)[-2:]
        if last_two == [PlayIntent.RUN, PlayIntent.RUN]:
            run_probability = max(0.08, run_probability - 0.24)
            reason += "; break two-run tendency"
        elif last_two == [PlayIntent.PASS, PlayIntent.PASS]:
            run_probability = min(0.92, run_probability + 0.24)
            reason += "; break two-pass tendency"

        intent = PlayIntent.RUN if self.rng.random() < run_probability else PlayIntent.PASS
        self.playcall_decision_reason = f"{reason}; run_p={run_probability:.2f}"
        return intent

    def _choose_offensive_intent(self) -> PlayIntent:
        s = self.situation
        down = s.down
        distance = s.distance

        if down is None:
            return self._intent_with_tendency_break(0.52, reason="down unknown; balanced")

        if s.goal_to_go:
            if distance is not None and distance <= 2:
                return self._intent_with_tendency_break(0.72, reason="goal-to-go <=2")
            return self._intent_with_tendency_break(0.42, reason="goal-to-go; preserve pass threat")

        if down == 1:
            return self._intent_with_tendency_break(0.52, reason="1st down balanced")

        if down == 2:
            if distance is None:
                return self._intent_with_tendency_break(0.50, reason="2nd down distance unknown")
            if distance <= 3:
                # Madden strategy guides specifically identify 2nd-and-short as a
                # good opportunity to test the pass defense rather than auto-running.
                return self._intent_with_tendency_break(0.42, reason="2nd-and-short shot opportunity")
            if distance <= 6:
                return self._intent_with_tendency_break(0.48, reason="2nd-and-medium balanced")
            return self._intent_with_tendency_break(0.28, reason="2nd-and-long pass lean")

        if down == 3:
            if distance is None:
                self.playcall_decision_reason = "3rd down distance unknown; pass lean"
                return PlayIntent.PASS
            if distance <= 2:
                return self._intent_with_tendency_break(0.62, reason="3rd-and-short conversion")
            if distance <= 6:
                self.playcall_decision_reason = "3rd-and-medium; high-percentage pass"
                return PlayIntent.PASS
            self.playcall_decision_reason = "3rd-and-long; pass required"
            return PlayIntent.PASS

        if down == 4:
            # Actual field position is not yet parsed reliably. Prefer a normal
            # conversion only at one yard or less; otherwise the play-card reader
            # gets first chance to select visible PUNT/FIELD GOAL recommendations.
            if distance is not None and distance <= 1:
                return self._intent_with_tendency_break(0.66, reason="4th-and-1 conversion")
            self.playcall_decision_reason = "4th down; seek visible special teams before pass fallback"
            return PlayIntent.PASS

        return self._intent_with_tendency_break(0.46, reason="fallback balanced football")

    def _defense_style(self) -> str:
        s = self.situation
        if s.down in {3, 4} and s.distance is not None:
            if s.distance >= 7:
                return "coverage"
            if s.distance <= 2:
                return "pressure"
        return "balanced"

    def _score_play_line(self, line: OCRLine, intent: PlayIntent) -> float:
        if line.confidence < self.playcard_min_confidence:
            return -99.0
        if not 0.20 <= line.y <= 0.90:
            return -99.0

        compact = self._compact_play_text(line.text)
        if not compact or len(compact) < 3:
            return -99.0
        if any(header == compact for header in self.HEADER_TERMS):
            return -99.0

        run_hits = self._term_hits(compact, self.RUN_TERMS)
        pass_hits = self._term_hits(compact, self.PASS_TERMS)
        coverage_hits = self._term_hits(compact, self.COVERAGE_TERMS)
        pressure_hits = self._term_hits(compact, self.PRESSURE_TERMS)
        special_hits = self._term_hits(compact, self.SPECIAL_TERMS)

        # PA / PLAY ACTION is a pass even though the play name may also mention HB.
        play_action = compact.startswith("PA") or "PLAYACTION" in compact
        if play_action:
            pass_hits += 2

        score = line.confidence * 0.8
        if intent == PlayIntent.RUN:
            score += run_hits * 2.8 - pass_hits * 1.8
            if ("HB" in compact or "FB" in compact) and pass_hits == 0:
                score += 1.1
        elif intent == PlayIntent.PASS:
            score += pass_hits * 2.7 - run_hits * 1.5
            if any(token in compact for token in ("PASS", "SHOTGUN", "GUN")):
                score += 0.8
        else:
            style = self._defense_style()
            if style == "coverage":
                score += coverage_hits * 2.8 + pressure_hits * 0.6
            elif style == "pressure":
                score += pressure_hits * 2.4 + coverage_hits * 1.0
            else:
                score += coverage_hits * 1.7 + pressure_hits * 1.4

        # On 4th down beyond a yard, a visible special-teams card is much more
        # football-correct than blindly selecting a normal pass. Madden already
        # knows field position even when our OCR parser does not.
        if (
            self.situation.down == 4
            and (self.situation.distance is None or self.situation.distance > 1)
            and special_hits
        ):
            score += 9.0

        if compact in self.recent_play_labels:
            score -= 1.6
        return score

    def _visible_play_candidates(self, intent: PlayIntent) -> list[PlayCardCandidate]:
        candidates: list[PlayCardCandidate] = []
        for line in self.last_ocr.lines:
            score = self._score_play_line(line, intent)
            if score < 2.0:
                continue
            compact = self._compact_play_text(line.text)
            candidates.append(
                PlayCardCandidate(
                    label=compact,
                    button=self._face_button_for_x(line.x),
                    score=score,
                    confidence=line.confidence,
                    x=line.x,
                )
            )
        candidates.sort(key=lambda item: (item.score, item.confidence), reverse=True)
        return candidates

    def _formation_level(self) -> bool:
        compact = self._compact_play_text(self.last_ocr.text)
        return any(
            marker in compact
            for marker in (
                "FORMATION",
                "SINGLEBACK",
                "IFORM",
                "SHOTGUN",
                "STRONGI",
                "WEAKI",
                "GOALLINE",
                "NICKEL",
                "DIME",
                "QUARTER",
                "43",
                "34",
            )
        ) and "ASKMADDEN" not in compact

    def _arm_playcall(self, now: float) -> None:
        self.queue.clear()
        if self.possession == Possession.DEFENSE and self.possession_confidence >= 0.55:
            self.planned_play = PlayIntent.DEFENSE
            self.playcall_decision_reason = f"defense {_safe_label(self._defense_style())} by down/distance"
        else:
            self.planned_play = self._choose_offensive_intent()

        candidates = self._visible_play_candidates(self.planned_play)
        if candidates:
            chosen = candidates[0]
            self.queue.append(QueuedTap(now, chosen.button))
            self.next_action_at = now + self.playcall_action_seconds
            self.playcall_armed = True
            self.playcall_selection_mode = "semantic-card"
            self.playcall_selected_label = chosen.label
            self.playcall_selected_button = chosen.button
            self.playcall_semantic_selections += 1
            self.recent_play_labels.append(chosen.label)
            if self.planned_play in {PlayIntent.RUN, PlayIntent.PASS}:
                self.recent_offensive_intents.append(self.planned_play)
            if (
                self.situation.down == 4
                and (self.situation.distance is None or self.situation.distance > 1)
                and self._term_hits(chosen.label, self.SPECIAL_TERMS)
            ):
                self.fourth_down_special_candidates += 1
            self.current_action = (
                f"playcall: {chosen.label} -> {chosen.button} "
                f"({self.planned_play.value}; {self.playcall_decision_reason})"
            )
            return

        # The old policy queued formation selection and the eventual play button in
        # the same sub-second macro. V26 enters the formation first, then waits for
        # a fresh OCR pass before deciding which visible play card actually matches
        # the football intent.
        if self._formation_level():
            self.queue.append(QueuedTap(now, "cross"))
            self.next_action_at = now + max(0.80, self.ocr.interval_seconds + 0.10)
            self.playcall_armed = True
            self.playcall_selection_mode = "formation-first"
            self.playcall_selected_label = None
            self.playcall_selected_button = "cross"
            self.playcall_formation_entries += 1
            self.current_action = (
                f"playcall: enter current formation; rescan before play "
                f"({self.planned_play.value})"
            )
            return

        # Fail soft when OCR cannot classify the cards. Preserve the proven V25
        # path instead of turning a quality improvement into a lifecycle blocker.
        self.playcall_semantic_fallbacks += 1
        self.playcall_selection_mode = "legacy-fallback"
        self.playcall_selected_label = None
        self.playcall_selected_button = None
        super()._arm_playcall(now)

    def _transition_phase(self, new_phase: MaddenPhase, now: float) -> None:
        old = self.phase
        super()._transition_phase(new_phase, now)
        if self.phase == old:
            return
        if new_phase == MaddenPhase.PLAYCALL:
            self.playcall_selection_mode = "acquiring-cards"
            self.playcall_selected_label = None
            self.playcall_selected_button = None

    def _run_live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        elapsed = max(0.0, now - self.play_started_at)
        spatial = self.last_spatial

        if self._spatial_fresh(now) and spatial.open_space_confidence >= self.spatial_open_confidence:
            lateral = self._clamp(
                spatial.open_space_x * (0.28 if elapsed < self.run_patience_seconds else 0.58)
                + obs.field_center_x * 0.08,
                -0.62,
                0.62,
            )
            steering_reason = (
                f"open-space {spatial.open_space_x:+.2f}/{spatial.open_space_confidence:.2f}"
            )
        else:
            lateral = self._clamp(obs.field_center_x * 0.28, -0.34, 0.34)
            steering_reason = "field-center fallback"

        if elapsed < self.run_patience_seconds:
            # Madden 2005 rewards following the designed blocks instead of outrunning
            # them immediately. Keep moving downhill, but do not mash sprint in the
            # backfield before the lane has had time to form.
            controller.set_left_stick(lateral, 0.62)
            controller.set_right_stick(0.0, 0.0)
            self.run_patience_holds += 1
            self.current_action = (
                f"offense run: press hole / follow blocks ({elapsed:.2f}s; {steering_reason})"
            )
            return self.current_action

        controller.set_left_stick(lateral, 1.0)
        controller.set_right_stick(0.0, 0.0)
        if now < self.next_action_at:
            return self.current_action

        short_yardage = bool(
            self.situation.goal_to_go
            or (
                self.situation.down in {3, 4}
                and self.situation.distance is not None
                and self.situation.distance <= 2
            )
        )

        roll = self.rng.random()
        if roll < 0.60:
            controller.tap("cross", 0.055)
            action = "offense run: accelerate through lane"
        elif roll < 0.78:
            move = self.rng.choice(["l1", "r1"])
            controller.tap(move, 0.055)
            action = f"offense run: one-cut juke {move}"
        elif roll < 0.89:
            arm = self.rng.choice(["l2", "r2"])
            controller.tap(arm, 0.055)
            action = f"offense run: stiff-arm {arm}"
        elif roll < 0.96:
            controller.tap("triangle", 0.05)
            action = "offense run: secure/hurdle through contact"
        elif short_yardage:
            controller.tap("square", 0.055)
            self.run_short_yardage_dives += 1
            action = "offense run: dive for short-yardage marker"
        else:
            controller.tap("cross", 0.055)
            action = "offense run: stay north/south; no low-value dive"

        self.live_action_count += 1
        self.run_discipline_actions += 1
        self.next_action_at = now + self.run_action_seconds
        self.current_action = action
        return action

    def _offense_live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        if self.special_return_active:
            return super()._offense_live(controller, obs, now)
        if self.planned_play == PlayIntent.RUN:
            return self._run_live(controller, obs, now)
        return super()._offense_live(controller, obs, now)

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "madden_policy_version": "v26",
                "playcall_decision_reason": self.playcall_decision_reason,
                "playcall_selection_mode": self.playcall_selection_mode,
                "playcall_selected_label": self.playcall_selected_label,
                "playcall_selected_button": self.playcall_selected_button,
                "playcall_semantic_selections": self.playcall_semantic_selections,
                "playcall_semantic_fallbacks": self.playcall_semantic_fallbacks,
                "playcall_formation_entries": self.playcall_formation_entries,
                "fourth_down_special_candidates": self.fourth_down_special_candidates,
                "recent_offensive_intents": [intent.value for intent in self.recent_offensive_intents],
                "recent_play_labels": list(self.recent_play_labels),
                "run_patience_seconds": round(self.run_patience_seconds, 2),
                "run_patience_holds": self.run_patience_holds,
                "run_short_yardage_dives": self.run_short_yardage_dives,
                "run_discipline_actions": self.run_discipline_actions,
                "defense_situation_style": self._defense_style(),
            }
        )
        return state


def _safe_label(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "", value.lower()) or "balanced"
