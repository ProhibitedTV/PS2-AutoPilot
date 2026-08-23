import numpy as np

from ps2_autopilot.madden_ocr import OCRLine, OCRSnapshot
from ps2_autopilot.madden_vision import MaddenObservation, MaddenVisualState
from ps2_autopilot.profiles.madden2005 import MaddenPhase, PlayIntent, Possession
from ps2_autopilot.profiles.madden2005_v6 import Madden2005V6Profile
from ps2_autopilot.profiles.madden2005_v6_runtime import Madden2005V6RuntimeProfile


class FakeController:
    def __init__(self):
        self.events = []

    def tap(self, action, duration=0.08):
        self.events.append(("tap", action))

    def neutral_sticks(self):
        self.events.append(("neutral", None))

    def set_left_stick(self, x, y):
        self.events.append(("left", round(float(x), 3), round(float(y), 3)))

    def set_right_stick(self, x, y):
        self.events.append(("right", round(float(x), 3), round(float(y), 3)))


def observation():
    return MaddenObservation(
        state=MaddenVisualState.LIVE_PLAY,
        green_ratio=0.82,
        field_center_x=0.02,
        motion_center_x=-0.08,
        brightness=0.45,
        motion=0.04,
        template_name=None,
        template_score=None,
    )


def snapshot(text):
    line = OCRLine(text=text, confidence=0.97, x=0.5, y=0.12, width=0.5, height=0.05)
    return OCRSnapshot((line,), text, True)


def test_playcall_vocabulary_infers_defense():
    role, confidence, reason = Madden2005V6Profile.infer_playcall_role(
        "ASK MADDEN | NICKEL | ENGAGE EIGHT | COVER 2"
    )
    assert role == Possession.DEFENSE
    assert confidence >= 0.92
    assert "NICKEL" in reason or "ENGAGE EIGHT" in reason


def test_playcall_vocabulary_infers_offense():
    role, confidence, reason = Madden2005V6Profile.infer_playcall_role(
        "ASK MADDEN | SINGLEBACK | HB DIVE"
    )
    assert role == Possession.OFFENSE
    assert confidence >= 0.90
    assert "SINGLEBACK" in reason


def test_pass_policy_waits_then_throws_exactly_once():
    profile = Madden2005V6Profile({"ocr_enabled": False})
    controller = FakeController()
    profile.phase = MaddenPhase.LIVE
    profile.play_started_at = 0.0
    profile.planned_play = PlayIntent.PASS
    profile.possession = Possession.OFFENSE
    profile.possession_confidence = 0.95
    profile.pass_throw_due_at = 1.50
    profile.next_action_at = 0.0

    action = profile._offense_live(controller, observation(), now=1.0)
    assert "pocket read" in action
    assert not [event for event in controller.events if event[0] == "tap"]

    controller.events.clear()
    profile.next_action_at = 0.0
    action = profile._offense_live(controller, observation(), now=1.6)
    taps = [event for event in controller.events if event[0] == "tap"]
    assert len(taps) == 1
    assert taps[0][1] in {"cross", "circle", "square", "l1", "r1"}
    assert "single throw" in action
    assert profile.pass_attempts == 1

    controller.events.clear()
    profile.next_action_at = 0.0
    profile._offense_live(controller, observation(), now=1.7)
    receiver_taps = [
        event
        for event in controller.events
        if event[0] == "tap" and event[1] in {"circle", "square", "l1", "r1"}
    ]
    assert not receiver_taps


def test_unknown_live_role_does_not_spam_buttons():
    profile = Madden2005V6Profile({"ocr_enabled": False})
    controller = FakeController()
    profile.phase = MaddenPhase.LIVE
    profile.possession = Possession.UNKNOWN
    profile.possession_confidence = 0.0
    profile.next_action_at = 0.0

    action = profile._live(controller, observation(), now=5.0)
    assert "without button spam" in action
    assert not [event for event in controller.events if event[0] == "tap"]


def test_post_play_cross_is_bounded_to_two_skips():
    profile = Madden2005V6Profile({"ocr_enabled": False})
    controller = FakeController()
    profile.next_action_at = 0.0

    profile._post_play(controller, now=1.0)
    profile.next_action_at = 0.0
    profile._post_play(controller, now=2.0)
    profile.next_action_at = 0.0
    action = profile._post_play(controller, now=3.0)

    cross_taps = [event for event in controller.events if event == ("tap", "cross")]
    assert len(cross_taps) == 2
    assert "wait for playcall" in action


def test_interception_event_flips_known_possession_once():
    profile = Madden2005V6RuntimeProfile({"ocr_enabled": False})
    profile.possession = Possession.OFFENSE
    profile.possession_confidence = 0.90
    profile.last_ocr = snapshot("INTERCEPTION")

    profile._note_game_event(now=10.0)
    assert profile.possession == Possession.DEFENSE
    assert profile.possession_confidence >= 0.95
    assert profile.game_event_counts["interception"] == 1

    profile._note_game_event(now=11.0)
    assert profile.game_event_counts["interception"] == 1


def test_high_confidence_playcall_can_replace_stale_role():
    profile = Madden2005V6RuntimeProfile({"ocr_enabled": False})
    profile.possession = Possession.OFFENSE
    profile.possession_confidence = 0.98

    profile._set_possession(Possession.DEFENSE, 0.96)
    assert profile.possession == Possession.DEFENSE
    assert profile.possession_confidence == 0.96


def test_defense_waits_for_play_to_develop_before_tackle_buttons():
    profile = Madden2005V6Profile({"ocr_enabled": False})
    controller = FakeController()
    profile.phase = MaddenPhase.LIVE
    profile.play_started_at = 10.0
    profile.next_action_at = 0.0

    action = profile._defense_live(controller, observation(), now=10.3)
    assert "read snap" in action
    assert not [event for event in controller.events if event[0] == "tap"]
