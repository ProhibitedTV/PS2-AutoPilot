import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter import JakPhase
from ps2_autopilot.profiles.jak_and_daxter_v2 import JakAndDaxterV2Profile
from ps2_autopilot.semantic_ocr import OCRLine, OCRSnapshot


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
    def __init__(self, snapshot: OCRSnapshot):
        self.snapshot = snapshot

    def read(self, frame, now):
        return self.snapshot

    def telemetry(self, now):
        return {}


def line(text, y):
    return OCRLine(text=text, confidence=0.95, x=0.42, y=y, width=0.22, height=0.06)


def main_menu_snapshot():
    lines = (
        line("NEW GAME", 0.20),
        line("LOAD GAME", 0.32),
        line("OPTIONS", 0.44),
        line("BACK", 0.56),
    )
    return OCRSnapshot(lines, "NEW GAME | LOAD GAME | OPTIONS | BACK", True, None)


def menu_frame(selected_new_game=True):
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    # Match the normalized NEW GAME OCR box. Bright BGR green represents the
    # selected Jak menu text; inactive entries remain gray/black in this fixture.
    if selected_new_game:
        frame[60:84, 198:340] = (20, 235, 60)
    return frame


def ctx(frame, now=1.0):
    return ProfileContext(
        frame=frame,
        previous_frame=None,
        motion=0.01,
        template=None,
        now=now,
    )


def test_main_menu_promotes_unknown_to_menu_and_confirms_verified_new_game():
    profile = JakAndDaxterV2Profile(
        {
            "mode": "observe",
            "new_game_green_ratio_threshold": 0.02,
            "new_game_green_margin": 0.01,
        }
    )
    profile.ocr = FakeOCR(main_menu_snapshot())
    controller = FakeController()

    action = profile.tick(controller, ctx(menu_frame(True), now=1.0))

    assert profile.phase == JakPhase.MENU
    assert profile.main_menu_visible is True
    assert profile.new_game_selected is True
    assert controller.taps == [("cross", 0.08)]
    assert "verified NEW GAME" in action


def test_main_menu_never_crosses_without_green_new_game_verification():
    profile = JakAndDaxterV2Profile(
        {
            "mode": "observe",
            "new_game_green_ratio_threshold": 0.02,
            "new_game_green_margin": 0.01,
        }
    )
    profile.ocr = FakeOCR(main_menu_snapshot())
    controller = FakeController()

    action = profile.tick(controller, ctx(menu_frame(False), now=1.0))

    assert profile.phase == JakPhase.MENU
    assert profile.main_menu_visible is True
    assert profile.new_game_selected is False
    assert controller.taps == []
    assert "highlight unverified" in action


def test_verified_new_game_cross_is_rate_limited_while_menu_remains_visible():
    profile = JakAndDaxterV2Profile(
        {
            "mode": "observe",
            "main_menu_retry_seconds": 3.0,
            "new_game_green_ratio_threshold": 0.02,
            "new_game_green_margin": 0.01,
        }
    )
    profile.ocr = FakeOCR(main_menu_snapshot())
    controller = FakeController()
    frame = menu_frame(True)

    profile.tick(controller, ctx(frame, now=1.0))
    profile.tick(controller, ctx(frame, now=2.0))
    assert [tap[0] for tap in controller.taps] == ["cross"]

    profile.tick(controller, ctx(frame, now=4.1))
    assert [tap[0] for tap in controller.taps] == ["cross", "cross"]
