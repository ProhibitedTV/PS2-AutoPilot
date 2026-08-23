import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v5 import JakAndDaxterV5Profile
from ps2_autopilot.profiles.registry import build_profile
from ps2_autopilot.semantic_ocr import OCRSnapshot


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
    def __init__(self, text=""):
        self.text = text

    def read(self, frame, now):
        return OCRSnapshot((), self.text, True, 0.80)

    def telemetry(self, now):
        return {}


def context(*, now=10.0):
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    return ProfileContext(frame=frame, previous_frame=None, motion=0.0, template=None, now=now)


def profile(text=""):
    p = JakAndDaxterV5Profile(
        {
            "mode": "production",
            "ocr_enabled": False,
            "progress_probe_initial_delay_seconds": 999.0,
        }
    )
    p.ocr = FakeOCR(text)
    return p


def test_real_save_file_selector_confirms_first_empty_slot():
    text = (
        "SELECT FILE TO SAVE TO\n"
        "EMPTY\nEMPTY\nEMPTY\nEMPTY\n"
        "CONTINUE WITHOUT SAVING\nBACK"
    )
    p = profile(text)
    controller = FakeController()

    action = p.tick(controller, context(now=10.0))

    assert p.save_file_selector_visible is True
    assert p.save_file_empty_count == 4
    assert controller.taps == [("cross", 0.08)]
    assert p.save_file_confirms == 1
    assert "CROSS first slot" in action


def test_save_file_selector_uses_bounded_retry_cooldown():
    text = "SELECT FILE TO SAVE TO EMPTY EMPTY EMPTY EMPTY CONTINUE WITHOUT SAVING BACK"
    p = profile(text)
    controller = FakeController()

    p.tick(controller, context(now=20.0))
    action = p.tick(controller, context(now=21.0))

    assert controller.taps == [("cross", 0.08)]
    assert p.save_file_confirms == 1
    assert "wait" in action


def test_selector_requires_an_empty_slot():
    p = profile("SELECT FILE TO SAVE TO CONTINUE WITHOUT SAVING BACK")
    controller = FakeController()

    action = p.tick(controller, context(now=30.0))

    assert p.save_file_selector_visible is False
    assert controller.taps == []
    assert "fail closed" in action


def test_destructive_selector_wording_remains_fail_closed():
    p = profile("SELECT FILE TO SAVE TO EMPTY FORMAT MEMORY CARD")
    controller = FakeController()

    action = p.tick(controller, context(now=40.0))

    assert p.save_file_selector_visible is False
    assert controller.taps == []
    assert "fail closed" in action


def test_registry_promotes_jak_to_v5():
    p = build_profile({"name": "jak", "mode": "production", "ocr_enabled": False})
    assert isinstance(p, JakAndDaxterV5Profile)
