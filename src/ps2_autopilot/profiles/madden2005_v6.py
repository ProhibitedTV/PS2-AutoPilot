from __future__ import annotations

import math

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_menu import MaddenScreen
from ps2_autopilot.madden_vision import MaddenObservation

from .base import ProfileContext
from .madden2005 import MaddenPhase, PlayIntent, Possession
from .madden2005_v5 import Madden2005V5Profile


class Madden2005V6Profile(Madden2005V5Profile):
    """Gameplay-quality pass layered on top of the v0.5 24/7 runtime.

    v0.5 proved the emulator/controller/menu pipeline can reach and survive a real
    game. v0.5.2 keeps those recovery guarantees but makes the football policy less
    button-spammy and more possession-aware. The policy still stays deliberately
    simple: reliable unattended play is more valuable than brittle sophistication.
    """

    name = "madden2005"

    EVENT_MARKERS = (
        ("TOUCHDOWN", "touchdown"),
        ("INTERCEPTION", "interception"),
        ("FUMBLE", "fumble"),
        ("FIRST DOWN", "first_down"),
        ("INCOMPLETE", "incomplete"),
        ("FIELD GOAL", "field_goal"),
        ("PENALTY", "penalty"),
        ("SACK", "sack"),
        ("PUNT", "punt"),
        ("KICKOFF", "kickoff"),
    )

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.pass_read_min = float(cfg.get("pass_read_min_seconds", 1.10))
        self.pass_read_max = float(cfg.get("pass_read_max_seconds", 2.05))
        self.pass_throw_due_at = -1e9
        self.pass_thrown_at = -1e9
        self.receiver_bursts = 0

        self.defense_switched = False
        self.defense_pre_snap_cycles = 0
        self.post_play_skip_attempts = 0

        self.playcall_role_reason = "none"
        self.last_game_event = "none"
        self.last_game_event_at = -1e9
        self.game_event_counts: dict[str, int] = {}

    @staticmethod
    def _clean_text(text: str) -> str:
        return (
            text.upper()
            .replace("0", "O")
            .replace("1", "I")
            .replace("5", "S")
            .replace("|", " ")
        )

    @classmethod
    def infer_playcall_role(cls, text: str) -> tuple[Possession | None, float, str]:
        """Infer whether a play-call page belongs to offense or defense.

        Play-call vocabulary is much stronger possession evidence than waiting for
        an X snap probe. We intentionally use high-signal formations/play names and
        refuse to guess when offense and defense evidence are tied.
        """

        alpha = cls._clean_text(text)
        offense_markers = (
            "SINGLEBACK",
            "I FORM",
            "I-FORM",
            "IFORM",
            "SHOTGUN",
            "STRONG I",
            "WEAK I",
            "PRO SET",
            "GUN ",
        )
        defense_markers = (
            "NICKEL",
            "DIME",
            "ENGAGE EIGHT",
            "BLITZ",
            "COVER ",
            "ZONE ",
            "MAN ",
            "4-3",
            "3-4",
            "4 3",
            "3 4",
            "QUARTER DEF",
        )
        offense_hits = [marker for marker in offense_markers if marker in alpha]
        defense_hits = [marker for marker in defense_markers if marker in alpha]

        if len(offense_hits) > len(defense_hits) and offense_hits:
            confidence = min(0.98, 0.88 + 0.04 * len(offense_hits))
            return Possession.OFFENSE, confidence, ",".join(offense_hits[:3])
        if len(defense_hits) > len(offense_hits) and defense_hits:
            confidence = min(0.98, 0.88 + 0.04 * len(defense_hits))
            return Possession.DEFENSE, confidence, ",".join(defense_hits[:3])
        return None, 0.0, "ambiguous"

    @classmethod
    def detect_game_event(cls, text: str) -> str | None:
        alpha = cls._clean_text(text)
        for marker, event in cls.EVENT_MARKERS:
            if marker in alpha:
                return event
        return None

    def _note_game_event(self, now: float) -> None:
        event = self.detect_game_event(self.last_ocr.text)
        if event is None:
            return
        # Score banners can remain on screen for several OCR cycles. Count a new
        # occurrence only after a cooldown or when the event label changes.
        if event == self.last_game_event and now - self.last_game_event_at < 6.0:
            return

        self.last_game_event = event
        self.last_game_event_at = now
        self.game_event_counts[event] = self.game_event_counts.get(event, 0) + 1

        if event == "interception":
            if self.possession == Possession.OFFENSE:
                self._set_possession(Possession.DEFENSE, 0.97)
            elif self.possession == Possession.DEFENSE:
                self._set_possession(Possession.OFFENSE, 0.97)
        elif event == "fumble":
            # Recovery ownership is unknown, so deliberately reduce certainty.
            self.possession_confidence = min(self.possession_confidence, 0.35)

    def _observe(self, ctx: ProfileContext) -> MaddenObservation:
        obs = super()._observe(ctx)

        if self.menu_assessment.screen == MaddenScreen.PLAYCALL or self.phase == MaddenPhase.PLAYCALL:
            role, confidence, reason = self.infer_playcall_role(self.last_ocr.text)
            if role is not None:
                self._set_possession(role, confidence)
                self.playcall_role_reason = reason

        self._note_game_event(ctx.now)
        return obs

    def _transition_phase(self, new_phase: MaddenPhase, now: float) -> None:
        old = self.phase
        super()._transition_phase(new_phase, now)
        if self.phase == old:
            return

        if new_phase == MaddenPhase.PLAYCALL:
            self.playcall_role_reason = "awaiting playcall OCR"
            self.post_play_skip_attempts = 0

        if new_phase == MaddenPhase.PRE_SNAP:
            self.defense_pre_snap_cycles = 0
            self.post_play_skip_attempts = 0

        if new_phase == MaddenPhase.LIVE:
            self.defense_switched = False
            self.receiver_bursts = 0
            self.pass_thrown_at = -1e9
            if self.planned_play == PlayIntent.PASS:
                self.pass_throw_due_at = now + self.rng.uniform(
                    self.pass_read_min, max(self.pass_read_min, self.pass_read_max)
                )
            else:
                self.pass_throw_due_at = -1e9

        if new_phase == MaddenPhase.POST_PLAY:
            self.post_play_skip_attempts = 0

    def _pre_snap(self, controller: Controller, now: float) -> str:
        controller.neutral_sticks()
        idle_for = now - self.phase_since
        if idle_for < self.pre_snap_wait or now < self.next_action_at:
            self.current_action = f"pre-snap: read line ({idle_for:.1f}s)"
            return self.current_action

        if self.possession == Possession.DEFENSE and self.possession_confidence >= 0.62:
            if self.defense_pre_snap_cycles < 1:
                controller.tap("circle", 0.05)
                self.defense_pre_snap_cycles += 1
                self.next_action_at = now + 4.0
                self.current_action = "defense pre-snap: select defender once / wait snap"
            else:
                self.next_action_at = now + 2.0
                self.current_action = "defense pre-snap: hold position / wait snap"
            return self.current_action

        # On offense, retry a snap a few times but do not hammer X indefinitely.
        # Unknown possession gets at most two probes before the semantic watchdog
        # becomes responsible for escalation.
        max_probes = 3 if self.possession == Possession.OFFENSE else 2
        if self.snap_attempts < max_probes:
            controller.tap("cross", 0.06)
            self.last_snap_at = now
            self.snap_attempts += 1
            self.next_action_at = now + (1.65 if self.snap_attempts == 1 else 2.15)
            role = self.possession.value if self.possession != Possession.UNKNOWN else "unknown"
            self.current_action = f"pre-snap: X snap probe {self.snap_attempts}/{max_probes} ({role})"
            return self.current_action

        self.next_action_at = now + 3.0
        self.current_action = "pre-snap: probes exhausted; observe before recovery"
        return self.current_action

    def _offense_live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        elapsed = max(0.0, now - self.play_started_at)
        correction = self._clamp(obs.field_center_x * 0.30, -0.24, 0.24)

        if self.planned_play == PlayIntent.PASS:
            if not self.pass_thrown:
                # Stay in a small pocket and make exactly one receiver throw. The
                # previous policy pressed X early under the mistaken assumption it
                # merely opened receiver icons; in Madden that can itself be a pass.
                pocket_x = math.sin(now * 0.85) * 0.06
                controller.set_left_stick(self._clamp(correction + pocket_x, -0.20, 0.20), 0.02)
                controller.set_right_stick(0.0, 0.0)

                if now < self.pass_throw_due_at or now < self.next_action_at:
                    remaining = max(0.0, self.pass_throw_due_at - now)
                    self.current_action = f"offense pass: pocket read ({remaining:.1f}s)"
                    return self.current_action

                receiver = self.rng.choices(
                    ["cross", "circle", "square", "l1", "r1"],
                    weights=[0.22, 0.24, 0.22, 0.16, 0.16],
                    k=1,
                )[0]
                controller.tap(receiver, self.rng.uniform(0.065, 0.12))
                self.receiver_button = receiver
                self.pass_thrown = True
                self.pass_thrown_at = now
                self.pass_attempts += 1
                self.live_action_count += 1
                self.next_action_at = now + 0.52
                self.current_action = f"offense pass: single throw -> {receiver} at {elapsed:.1f}s"
                return self.current_action

            # After the ball is away, move upfield and make one catch attempt. Do
            # not keep cycling receiver buttons; that creates accidental inputs.
            controller.set_left_stick(correction, 1.0)
            controller.set_right_stick(0.0, 0.0)
            if now < self.next_action_at:
                return self.current_action
            if not self.catch_attempted and now - self.pass_thrown_at >= 0.45:
                controller.tap("triangle", 0.055)
                self.catch_attempted = True
                self.next_action_at = now + 0.75
                self.current_action = "offense pass: one catch attempt"
                return self.current_action
            if self.receiver_bursts < 2:
                controller.tap("cross", 0.055)
                self.receiver_bursts += 1
                self.next_action_at = now + 0.90
                self.current_action = f"offense pass: receiver burst {self.receiver_bursts}/2"
                return self.current_action
            self.next_action_at = now + 0.75
            self.current_action = "offense pass: steer receiver / no button spam"
            return self.current_action

        # Run policy: north/south first, with fewer special-move presses than v0.4.
        wander = math.sin(now * 1.05) * self.steering_wander * 0.28
        controller.set_left_stick(self._clamp(correction + wander, -0.55, 0.55), 1.0)
        controller.set_right_stick(0.0, 0.0)
        if now < self.next_action_at:
            return self.current_action

        if elapsed < 0.75 and self.live_action_count == 0:
            controller.tap("cross", 0.055)
            action = "offense run: initial speed burst"
        else:
            roll = self.rng.random()
            if roll < 0.48:
                controller.tap("cross", 0.055)
                action = "offense run: speed burst"
            elif roll < 0.66:
                move = self.rng.choice(["l1", "r1"])
                controller.tap(move, 0.055)
                action = f"offense run: juke {move}"
            elif roll < 0.78:
                controller.tap("circle", 0.055)
                action = "offense run: spin"
            elif roll < 0.90:
                arm = self.rng.choice(["l2", "r2"])
                controller.tap(arm, 0.055)
                action = f"offense run: stiff-arm {arm}"
            elif roll < 0.97:
                controller.tap("triangle", 0.05)
                action = "offense run: protect / hurdle"
            else:
                controller.tap("square", 0.055)
                action = "offense run: dive for marker"
        self.live_action_count += 1
        self.next_action_at = now + self.rng.uniform(0.62, 0.95)
        self.current_action = action
        return action

    def _defense_live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        elapsed = max(0.0, now - self.play_started_at)
        target_x = self._clamp(
            obs.motion_center_x * 0.68 + obs.field_center_x * 0.12,
            -0.88,
            0.88,
        )
        target_y = self._clamp(-self.motion_target_y * 0.60 + 0.34, -0.82, 1.0)
        controller.set_left_stick(target_x, target_y)
        controller.set_right_stick(0.0, 0.0)

        # Let the play actually develop before throwing tackle buttons into it.
        if elapsed < 0.55:
            self.current_action = "defense: read snap / pursue"
            return self.current_action
        if now < self.next_action_at:
            return self.current_action

        if not self.defense_switched:
            controller.tap("cross", 0.05)
            self.defense_switched = True
            self.live_action_count += 1
            self.next_action_at = now + 0.62
            self.current_action = "defense: switch nearest ball once"
            return self.current_action

        if elapsed < 1.35:
            controller.tap("circle", 0.055)
            action = "defense: sprint / close space"
        else:
            roll = self.rng.random()
            if roll < 0.30:
                controller.tap("square", 0.06)
                self.tackle_attempts += 1
                action = "defense: tackle attempt"
            elif roll < 0.62:
                controller.tap("circle", 0.055)
                action = "defense: sprint / shed"
            elif roll < 0.76:
                controller.tap("triangle", 0.055)
                action = "defense: play ball"
            elif roll < 0.88:
                controller.tap("r2", 0.055)
                action = "defense: strip attempt"
            else:
                move = self.rng.choice(["l1", "r1"])
                controller.tap(move, 0.055)
                action = f"defense: rush move {move}"
        self.live_action_count += 1
        self.next_action_at = now + self.rng.uniform(0.58, 0.88)
        self.current_action = action
        return action

    def _live(self, controller: Controller, obs: MaddenObservation, now: float) -> str:
        if self.possession == Possession.OFFENSE and self.possession_confidence >= 0.45:
            return self._offense_live(controller, obs, now)
        if self.possession == Possession.DEFENSE and self.possession_confidence >= 0.45:
            return self._defense_live(controller, obs, now)

        # Unknown role should be conservative. We still steer toward field motion,
        # but avoid the old X-every-0.6s probe loop that could become random throws.
        controller.set_left_stick(
            self._clamp(obs.motion_center_x * 0.30 + obs.field_center_x * 0.12, -0.42, 0.42),
            0.55,
        )
        controller.set_right_stick(0.0, 0.0)
        if now >= self.next_action_at:
            self.next_action_at = now + 1.25
            self.current_action = "live: role unknown; steer/observe without button spam"
        return self.current_action

    def _post_play(self, controller: Controller, now: float) -> str:
        controller.neutral_sticks()
        if now < self.next_action_at:
            return self.current_action

        if self.post_play_skip_attempts < 2:
            controller.tap("cross", 0.055)
            self.post_play_skip_attempts += 1
            self.next_action_at = now + 1.30
            self.current_action = f"post-play: skip presentation {self.post_play_skip_attempts}/2"
            return self.current_action

        # Stop pressing Cross after two skips. If the state really is stuck, the
        # semantic progress watchdog will recover it without bleeding inputs into
        # the next play-call screen.
        self.next_action_at = now + 2.0
        self.current_action = "post-play: wait for playcall transition"
        return self.current_action

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "playcall_role_reason": self.playcall_role_reason,
                "pass_throw_due_in": round(max(0.0, self.pass_throw_due_at - ctx.now), 2)
                if self.phase == MaddenPhase.LIVE and not self.pass_thrown
                else 0.0,
                "receiver_button": self.receiver_button,
                "last_game_event": self.last_game_event,
                "game_event_counts": dict(self.game_event_counts),
                "post_play_skip_attempts": self.post_play_skip_attempts,
                "defense_switched": self.defense_switched,
            }
        )
        return state
