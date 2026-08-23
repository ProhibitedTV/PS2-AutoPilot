import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter import JakAndDaxterProfile, JakPhase
from ps2_autopilot.semantic_ocr import OCRSnapshot
from ps2_autopilot.vision import TemplateMatch


class FakeController:
    def __init__(self):
        self.taps = []
        self.left = []
        self.right = []
        self.release_all_count = 0

    def tap(self, action, duration=0.08):
        self.taps.append((action, duration))

    def hold(self, action):
        pass

    def release(self, action):
        pass

    def release_all(self):
        self.release_all_count += 1

    def set_left_stick(self, x, y):
        self.left.append((x, y))

    def set_right_stick(self, x, y):
        self.right.append((x, y))

    def neutral_sticks(self):
        self.set_left_stick(0.0, 0.0)
        self.set_right_stick(0.0, 0.0)


class FakeOCR:
    def __init__(self, text: str, available: bool = True):
        self.snapshot = OCRSnapshot((), text, available, None)

    def read(self, frame, now):
        return self.snapshot

    def telemetry(self, now):
        return {}


def ctx(now=1.0, template=None, motion=0.02):
    return ProfileContext(
        frame=np.zeros((360, 640, 3), dtype=np.uint8),
        previous_frame=None,
        motion=motion,
        template=template,
        now=now,
    )


def test_observe_mode_never_taps_controller_even_on_gameplay_template():
    profile = JakAndDaxterProfile({"mode": "observe", "template_threshold": 0.8})
    controller = FakeController()
    action = profile.tick(controller, ctx(template=TemplateMatch("jak_gameplay", 0.95)))
    assert profile.phase == JakPhase.GAMEPLAY
    assert controller.taps == []
    assert "hold inputs" in action


def test_observe_mode_can_clear_explicit_press_start_gate():
    profile = JakAndDaxterProfile(
        {"mode": "observe", "template_threshold": 0.8, "title_start_retry_seconds": 3.0}
    )
    profile.ocr = FakeOCR("JAK AND DAXTER | PRECURSOR LEGACY | PRESS START")
    controller = FakeController()

    action = profile.tick(controller, ctx(now=1.0, template=None))
    assert profile.phase == JakPhase.MENU
    assert profile.title_gate_visible is True
    assert controller.taps == [("start", 0.08)]
    assert "PRESS START" in action

    # The same stale/unchanged OCR result cannot create a rapid START loop.
    profile.tick(controller, ctx(now=2.0, template=None))
    assert controller.taps == [("start", 0.08)]

    # If the gate genuinely remains visible, a bounded retry is allowed.
    profile.tick(controller, ctx(now=4.1, template=None))
    assert [tap[0] for tap in controller.taps] == ["start", "start"]


def test_unrelated_unknown_ocr_does_not_gain_controller_ownership():
    profile = JakAndDaxterProfile({"mode": "observe", "template_threshold": 0.8})
    profile.ocr = FakeOCR("NAUGHTY DOG PRESENTS")
    controller = FakeController()
    action = profile.tick(controller, ctx(template=None))
    assert profile.phase == JakPhase.UNKNOWN
    assert controller.taps == []
    assert "hold inputs" in action


def test_unknown_and_cutscene_fail_closed_in_explore_mode():
    profile = JakAndDaxterProfile({"mode": "explore", "template_threshold": 0.8})
    controller = FakeController()
    profile.tick(controller, ctx(template=None))
    profile.tick(controller, ctx(now=2.0, template=TemplateMatch("jak_cutscene", 0.96)))
    assert controller.taps == []
    assert profile.phase == JakPhase.CUTSCENE


def test_calibrated_gameplay_allows_analog_exploration():
    profile = JakAndDaxterProfile(
        {"mode": "explore", "template_threshold": 0.8, "jump_probability": 0.0}
    )
    controller = FakeController()
    profile.tick(controller, ctx(template=TemplateMatch("jak_gameplay", 0.96)))
    assert profile.phase == JakPhase.GAMEPLAY
    assert any(abs(x) > 0 or abs(y) > 0 for x, y in controller.left)
    assert controller.taps == []


def test_calibrated_menu_and_death_have_bounded_actions():
    profile = JakAndDaxterProfile({"mode": "explore", "template_threshold": 0.8})
    controller = FakeController()
    profile.tick(controller, ctx(template=TemplateMatch("jak_main_menu", 0.96)))
    assert controller.taps[-1][0] == "cross"

    profile.tick(controller, ctx(now=4.0, template=TemplateMatch("jak_death", 0.96)))
    assert controller.taps[-1][0] == "cross"
    assert profile.death_confirms == 1


def test_watchdog_recovery_never_uses_generic_button_sequence():
    profile = JakAndDaxterProfile({"mode": "observe"})
    controller = FakeController()
    action = profile.recover(controller)
    assert controller.taps == []
    assert controller.release_all_count == 1
    assert "neutral hold" in action
