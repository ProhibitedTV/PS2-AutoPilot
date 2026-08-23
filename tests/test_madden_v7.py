from ps2_autopilot.madden_ocr import OCRLine, OCRSnapshot
from ps2_autopilot.madden_runtime import RuntimeDirective
from ps2_autopilot.profiles.madden2005 import MaddenPhase
from ps2_autopilot.profiles.madden2005_v7 import Madden2005V7Profile


class FakeController:
    def __init__(self):
        self.events = []

    def tap(self, action, duration=0.08):
        self.events.append(("tap", action))

    def neutral_sticks(self):
        self.events.append(("neutral", None))


def snapshot(text: str) -> OCRSnapshot:
    if not text:
        return OCRSnapshot((), "", True)
    line = OCRLine(text=text, confidence=0.98, x=0.5, y=0.5, width=0.5, height=0.05)
    return OCRSnapshot((line,), text, True)


def post_play_profile(**cfg) -> Madden2005V7Profile:
    profile = Madden2005V7Profile({"ocr_enabled": False, **cfg})
    profile.phase = MaddenPhase.POST_PLAY
    profile.phase_since = 0.0
    profile.next_action_at = 0.0
    profile.last_ocr = snapshot("")
    return profile


def tap_actions(controller: FakeController):
    return [event[1] for event in controller.events if event[0] == "tap"]


def test_standard_post_play_presentation_is_not_skipped():
    profile = post_play_profile(presentation_hold_seconds=8.0)
    controller = FakeController()

    action = profile._post_play(controller, now=5.0)

    assert "watch standard sequence" in action
    assert tap_actions(controller) == []


def test_auto_flow_remains_hands_off_after_minimum_hold():
    profile = post_play_profile(
        presentation_hold_seconds=8.0,
        presentation_stall_nudge_seconds=30.0,
    )
    controller = FakeController()

    action = profile._post_play(controller, now=12.0)

    assert "auto-flow / hands off" in action
    assert tap_actions(controller) == []


def test_explicit_continue_prompt_gets_one_cross_after_hold():
    profile = post_play_profile(presentation_hold_seconds=8.0)
    profile.last_ocr = snapshot("PRESS X TO CONTINUE")
    controller = FakeController()

    action = profile._post_play(controller, now=9.0)
    assert "explicit continue prompt" in action
    assert tap_actions(controller) == ["cross"]

    profile.next_action_at = 0.0
    profile._post_play(controller, now=13.0)
    assert tap_actions(controller) == ["cross"]


def test_major_event_gets_longer_broadcast_hold_even_with_prompt():
    profile = post_play_profile(
        presentation_hold_seconds=8.0,
        event_presentation_hold_seconds=12.0,
    )
    profile.last_game_event = "touchdown"
    profile.last_game_event_at = 4.0
    profile.last_ocr = snapshot("PRESS X TO CONTINUE")
    controller = FakeController()

    action = profile._post_play(controller, now=10.0)

    assert "watch event sequence" in action
    assert tap_actions(controller) == []


def test_long_presentation_stall_gets_only_one_automatic_nudge():
    profile = post_play_profile(presentation_stall_nudge_seconds=30.0)
    controller = FakeController()

    action = profile._post_play(controller, now=31.0)
    assert "single advance nudge" in action
    assert tap_actions(controller) == ["cross"]

    profile.next_action_at = 0.0
    profile._post_play(controller, now=36.0)
    assert tap_actions(controller) == ["cross"]


def test_legacy_soft_stall_recovery_does_not_skip_presentation():
    profile = post_play_profile()
    controller = FakeController()

    assert profile._soft_stall_recovery(controller, now=20.0) is None
    assert tap_actions(controller) == []


def test_semantic_watchdog_preserves_long_presentation_before_hard_recovery():
    profile = post_play_profile(presentation_hard_recovery_seconds=55.0)
    controller = FakeController()
    directive = RuntimeDirective(1, "no semantic progress in post_play", 45.0)

    action = profile._progress_recover(controller, directive, now=45.0)
    assert "preserve sequence" in action
    assert tap_actions(controller) == []

    profile.next_action_at = 0.0
    action = profile._progress_recover(controller, directive, now=56.0)
    assert "hard-stall CROSS" in action
    assert tap_actions(controller) == ["cross"]
