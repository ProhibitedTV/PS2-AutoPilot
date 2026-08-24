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


def profile6():
    p = JakAndDaxterV6Profile(
        {
            "mode": "production",
            "ocr_enabled": False,
            "progress_probe_initial_delay_seconds": 999.0,
        }
    )
    p.ocr = FakeOCR(OVERWRITE_TEXT)
    return p


def profile20():
    p = JakAndDaxterV20Profile(
        {
            "mode": "production",
            "ocr_enabled": False,
            "progress_probe_initial_delay_seconds": 999.0,
        }
    )
    p.ocr = FakeOCR(OVERWRITE_TEXT)
    return p


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
    x0, x1, y0, y1 = p.SAVE_CHOICE_ROIS["no"]
    h, w = frame.shape[:2]
    frame[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)] = (0, 255, 0)

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
