import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v6 import JakAndDaxterV6Profile
from ps2_autopilot.profiles.jak_and_daxter_v20 import JakAndDaxterV20Profile
from ps2_autopilot.semantic_ocr import OCRSnapshot


OVERWRITE_TEXT = (
    "A JAK AND DAXTER SAVE GAME ALREADY EXISTS IN THE SAVE FILE YOU HAVE SELECTED. "
    "DO YOU WISH TO OVERWRITE THIS? YES NO"
)


class FakeOCR:
    def __init__(self, text):
        self.text = text

    def read(self, frame, now):
        return OCRSnapshot((), self.text, True, 0.90)

    def telemetry(self, now):
        return {}


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


def context(frame, now=10.0):
    return ProfileContext(
        frame=frame,
        previous_frame=frame.copy(),
        motion=0.0,
        template=None,
        now=now,
        semantic={},
        performance={"loop_pressure": "healthy"},
    )


def profile6(text=OVERWRITE_TEXT):
    p = JakAndDaxterV6Profile(
        {
            "mode": "production",
            "ocr_enabled": False,
            "progress_probe_initial_delay_seconds": 999.0,
        }
    )
    p.ocr = FakeOCR(text)
    return p


def profile20(text=OVERWRITE_TEXT):
    p = JakAndDaxterV20Profile(
        {
            "mode": "production",
            "ocr_enabled": False,
            "progress_probe_initial_delay_seconds": 999.0,
        }
    )
    p.ocr = FakeOCR(text)
    return p


def paint_textish(frame, bounds):
    h, w = frame.shape[:2]
    x0, x1, y0, y1 = bounds
    xa, xb = int(x0 * w), int(x1 * w)
    ya, yb = int(y0 * h), int(y1 * h)
    yy0 = ya + max(1, (yb - ya) // 4)
    yy1 = yb - max(1, (yb - ya) // 4)
    for x in range(xa + 5, max(xa + 6, xb - 5), 13):
        frame[yy0:yy1, x:min(x + 7, xb), :] = 220


def paint_choice(frame, bounds):
    h, w = frame.shape[:2]
    x0, x1, y0, y1 = bounds
    frame[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] = (0, 255, 0)


def visual_overwrite_frame(profile, *, selected="no", missing_band=None):
    frame = np.zeros((576, 1024, 3), dtype=np.uint8)
    for index, bounds in enumerate(profile.SAVE_CONFIRM_MESSAGE_ROIS):
        if index != missing_band:
            paint_textish(frame, bounds)
    paint_choice(frame, profile.SAVE_CHOICE_ROIS[selected])
    return frame


def test_overwrite_semantics_beat_four_empty_visual_fallback(monkeypatch):
    p = profile6()
    frame = np.zeros((576, 1024, 3), dtype=np.uint8)
    monkeypatch.setattr(p, "_visual_four_empty_selector", lambda ctx: True)

    visible = p._read_ocr_title_gate(context(frame))

    assert visible is True
    assert p.save_prompt_visible is True
    assert p.save_prompt_kind == "overwrite"
    assert p.save_file_selector_visible is False
    assert p.save_file_selector_source == "suppressed-by-stronger-menu"


def test_v20_overwrite_menu_preempts_active_locomotion_before_super_tick():
    p = profile20()
    controller = FakeController()
    frame = np.zeros((576, 1024, 3), dtype=np.uint8)

    # Match the live capture: NO is currently selected. Paint the calibrated NO ROI
    # lime so the save transaction requests LEFT toward YES instead of confirming NO.
    paint_choice(frame, p.SAVE_CHOICE_ROIS["no"])

    p.navigation_commit_active = True
    p.navigation_commit_stage = "drive"
    p.skill_active = True
    p.ledge_jump_active = True
    p.mobility_active = True
    p.target_resolution_active = True

    action = p.tick(controller, context(frame, now=20.0))

    assert p.save_prompt_visible is True
    assert p.save_prompt_kind == "overwrite"
    assert p.save_file_selector_visible is False
    assert controller.taps == [("left", 0.08)]
    assert controller.release_all_count == 1
    assert controller.left == [(0.0, 0.0)]
    assert controller.right == [(0.0, 0.0)]
    assert p.navigation_commit_active is False
    assert p.skill_active is False
    assert p.ledge_jump_active is False
    assert p.mobility_active is False
    assert p.target_resolution_active is False
    assert p.v20_menu_preemptions == 1
    assert "overwrite save prompt" in action


def test_visual_overwrite_fallback_recovers_fresh_restart_with_blank_ocr():
    p = profile20("")
    controller = FakeController()

    frame = visual_overwrite_frame(p, selected="no")
    first = p.tick(controller, context(frame, now=30.0))

    assert p.save_prompt_visible is True
    assert p.save_prompt_kind == "overwrite"
    assert p.save_prompt_visual_message_hits == 4
    assert p.save_prompt_visual_choice_visible is True
    assert p.save_file_selector_visible is False
    assert p.save_file_selector_source == "suppressed-by-visual-save-confirmation"
    assert controller.taps == [("left", 0.08)]
    assert "NO selected -> LEFT toward YES" in first

    # After the highlight settles on YES, the same visual-only transaction confirms.
    frame = visual_overwrite_frame(p, selected="yes")
    second = p.tick(controller, context(frame, now=30.4))

    assert controller.taps == [("left", 0.08), ("cross", 0.08)]
    assert "verified YES -> CROSS" in second


def test_visual_overwrite_fallback_requires_complete_message_layout():
    p = profile20("")
    controller = FakeController()
    frame = visual_overwrite_frame(p, selected="no", missing_band=2)

    action = p.tick(controller, context(frame, now=40.0))

    assert p.save_prompt_visual_message_hits == 3
    assert p.save_prompt_visible is False
    assert controller.taps == []
    assert "fail closed" in action


def test_visual_overwrite_fallback_rejects_destructive_ocr_even_with_layout():
    p = profile20("FORMAT MEMORY CARD ERASE DELETE")
    controller = FakeController()
    frame = visual_overwrite_frame(p, selected="no")

    action = p.tick(controller, context(frame, now=50.0))

    assert p.save_prompt_visible is False
    assert controller.taps == []
    assert "fail closed" in action
