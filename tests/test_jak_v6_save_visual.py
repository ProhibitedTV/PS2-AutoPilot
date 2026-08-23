import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v6 import JakAndDaxterV6Profile
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


def profile(text=""):
    p = JakAndDaxterV6Profile(
        {
            "mode": "production",
            "ocr_enabled": False,
            "progress_probe_initial_delay_seconds": 999.0,
        }
    )
    p.ocr = FakeOCR(text)
    return p


def paint_textish(frame, bounds):
    height, width = frame.shape[:2]
    x0, x1, y0, y1 = bounds
    xa, xb = int(x0 * width), int(x1 * width)
    ya, yb = int(y0 * height), int(y1 * height)
    yy0 = ya + max(1, (yb - ya) // 4)
    yy1 = yb - max(1, (yb - ya) // 4)
    for x in range(xa + 5, max(xa + 6, xb - 5), 12):
        frame[yy0:yy1, x:min(x + 6, xb), :] = 220


def visual_selector_frame(*, missing_row=None):
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    paint_textish(frame, JakAndDaxterV6Profile.SAVE_SELECTOR_TITLE_ROI)
    for index, bounds in enumerate(JakAndDaxterV6Profile.SAVE_SELECTOR_EMPTY_ROIS):
        if index != missing_row:
            paint_textish(frame, bounds)
    paint_textish(frame, JakAndDaxterV6Profile.SAVE_SELECTOR_CONTINUE_ROI)
    return frame


def context(frame, *, now=10.0, motion=0.0):
    return ProfileContext(frame=frame, previous_frame=None, motion=motion, template=None, now=now)


def test_visual_four_empty_selector_recovers_when_ocr_is_blank():
    p = profile("")
    controller = FakeController()

    action = p.tick(controller, context(visual_selector_frame(), now=10.0, motion=0.0))

    assert p.save_file_selector_visible is True
    assert p.save_file_selector_source == "visual-four-empty"
    assert p.save_file_empty_count == 4
    assert p.save_selector_visual_row_hits == 4
    assert controller.taps == [("cross", 0.08)]
    assert "CROSS first slot" in action


def test_visual_selector_requires_stable_screen():
    p = profile("")
    controller = FakeController()

    action = p.tick(controller, context(visual_selector_frame(), now=20.0, motion=0.05))

    assert p.save_file_selector_visible is False
    assert p.save_file_selector_source == "none"
    assert controller.taps == []
    assert "fail closed" in action


def test_visual_selector_requires_all_four_empty_rows():
    p = profile("")
    controller = FakeController()

    action = p.tick(
        controller,
        context(visual_selector_frame(missing_row=2), now=30.0, motion=0.0),
    )

    assert p.save_selector_visual_row_hits == 3
    assert p.save_file_selector_visible is False
    assert controller.taps == []
    assert "fail closed" in action


def test_visual_selector_never_overrides_destructive_ocr():
    p = profile("FORMAT MEMORY CARD ERASE DELETE")
    controller = FakeController()

    action = p.tick(controller, context(visual_selector_frame(), now=40.0, motion=0.0))

    assert p.save_file_selector_visible is False
    assert p.save_file_selector_source == "none"
    assert controller.taps == []
    assert "fail closed" in action


def test_registry_promotes_jak_to_v6():
    p = build_profile({"name": "jak", "mode": "production", "ocr_enabled": False})
    assert isinstance(p, JakAndDaxterV6Profile)
