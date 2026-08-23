import numpy as np

from ps2_autopilot.madden_menu import GameSituation, MaddenScreen, parse_game_situation
from ps2_autopilot.madden_ocr import OCRLine, OCRSnapshot
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.madden2005 import MaddenPhase, Possession
from ps2_autopilot.profiles.madden2005_v9 import Madden2005V9Profile


class FakeController:
    def __init__(self):
        self.events = []

    def tap(self, action, duration=0.08):
        self.events.append(("tap", action))

    def neutral_sticks(self):
        self.events.append(("neutral", None))

    def set_left_stick(self, x, y):
        self.events.append(("left", float(x), float(y)))

    def set_right_stick(self, x, y):
        self.events.append(("right", float(x), float(y)))


def ocr(*texts):
    lines = tuple(
        OCRLine(text=text, confidence=0.96, x=0.5, y=0.08 + index * 0.03, width=0.2, height=0.03)
        for index, text in enumerate(texts)
    )
    return OCRSnapshot(lines, " | ".join(texts), True)


def profile():
    return Madden2005V9Profile(
        {
            "ocr_enabled": False,
            "pre_snap_wait_seconds": 1.0,
            "pre_snap_failsafe_seconds": 5.0,
            "play_clock_urgent_seconds": 8,
        }
    )


def test_parse_madden_bare_quarter_and_play_clock():
    situation = parse_game_situation(
        ocr("2:39", "2ND", "NYJ", "38", ":15", "BUF", "6", "1ST AND 10")
    )
    assert situation.clock_seconds == 159
    assert situation.quarter == 2
    assert situation.play_clock_seconds == 15
    assert situation.down == 1
    assert situation.distance == 10


def test_game_clock_seconds_are_not_mistaken_for_play_clock():
    situation = parse_game_situation(ocr("1:39", "2ND", "1ST AND 10"))
    assert situation.clock_seconds == 99
    assert situation.play_clock_seconds is None


def test_live_offense_pick_a_play_banner_forces_playcall_and_role():
    p = profile()
    p.ocr.read = lambda frame, now: ocr(
        "TO", "CLOCK", ":24", "3:10", "OFFENSEPICKAPLAY"
    )
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    ctx = ProfileContext(frame=frame, previous_frame=None, motion=0.0, template=None, now=10.0)

    p._observe(ctx)

    assert p.phase == MaddenPhase.PLAYCALL
    assert p.menu_assessment.screen == MaddenScreen.PLAYCALL
    assert p.possession == Possession.OFFENSE
    assert p.possession_confidence >= 0.96
    assert p.current_playcall_role == Possession.OFFENSE


def test_live_defense_pick_a_play_banner_forces_playcall_and_role():
    p = profile()
    p.ocr.read = lambda frame, now: ocr("DEFENSE PICK A PLAY")
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    ctx = ProfileContext(frame=frame, previous_frame=None, motion=0.0, template=None, now=10.0)

    p._observe(ctx)

    assert p.phase == MaddenPhase.PLAYCALL
    assert p.menu_assessment.screen == MaddenScreen.PLAYCALL
    assert p.possession == Possession.DEFENSE
    assert p.current_playcall_role == Possession.DEFENSE


def test_low_play_clock_forces_safe_cross_probe_even_with_defense_belief():
    p = profile()
    c = FakeController()
    p.phase = MaddenPhase.PRE_SNAP
    p.phase_since = 0.0
    p.next_action_at = 0.0
    p.possession = Possession.DEFENSE
    p.possession_confidence = 0.96
    p.current_playcall_role = Possession.DEFENSE
    p.current_playcall_role_at = 8.0
    p.situation = GameSituation(play_clock_seconds=5)

    action = p._pre_snap(c, now=10.0)

    assert ("tap", "cross") in c.events
    assert "play clock 5s" in action
    assert p.pre_snap_urgency_probes == 1


def test_stale_defense_role_is_demoted_and_snap_probe_happens_early():
    p = profile()
    c = FakeController()
    p.phase = MaddenPhase.PRE_SNAP
    p.phase_since = 0.0
    p.next_action_at = 0.0
    p.possession = Possession.DEFENSE
    p.possession_confidence = 0.92
    p.current_playcall_role = Possession.UNKNOWN
    p.current_playcall_role_at = -1e9
    p.situation = GameSituation()

    action = p._pre_snap(c, now=2.0)

    assert p.possession_confidence <= 0.44
    assert p.pre_snap_stale_role_downgrades == 1
    assert ("tap", "cross") in c.events
    assert "snap probe" in action.lower()


def test_fresh_defense_role_waits_normally_before_failsafe():
    p = profile()
    c = FakeController()
    p.phase = MaddenPhase.PRE_SNAP
    p.phase_since = 0.0
    p.next_action_at = 0.0
    p.possession = Possession.DEFENSE
    p.possession_confidence = 0.94
    p.current_playcall_role = Possession.DEFENSE
    p.current_playcall_role_at = 1.0
    p.situation = GameSituation()

    action = p._pre_snap(c, now=2.0)

    assert ("tap", "cross") not in c.events
    assert "defense" in action.lower()


def test_failsafe_probe_caps_long_pre_snap_even_if_defense_was_confirmed():
    p = profile()
    c = FakeController()
    p.phase = MaddenPhase.PRE_SNAP
    p.phase_since = 0.0
    p.next_action_at = 0.0
    p.possession = Possession.DEFENSE
    p.possession_confidence = 0.94
    p.current_playcall_role = Possession.DEFENSE
    p.current_playcall_role_at = 1.0
    p.situation = GameSituation()

    action = p._pre_snap(c, now=5.1)

    assert ("tap", "cross") in c.events
    assert "failsafe" in action.lower()
