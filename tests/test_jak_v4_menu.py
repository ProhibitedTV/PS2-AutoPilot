import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v4 import JakAndDaxterV4Profile
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


def frame_with_menu_green(*, competitors=False):
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    rois = JakAndDaxterV4Profile.MAIN_MENU_ROIS

    def fill(bounds):
        x0, x1, y0, y1 = bounds
        frame[int(y0 * 1080):int(y1 * 1080), int(x0 * 1920):int(x1 * 1920)] = (0, 255, 0)

    fill(rois["new"])
    if competitors:
        fill(rois["load"])
        fill(rois["options"])
        fill(rois["back"])
    return frame


def frame_with_save_choice(selected=None):
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    if selected is None:
        return frame
    bounds = JakAndDaxterV4Profile.SAVE_CHOICE_ROIS[selected]
    x0, x1, y0, y1 = bounds
    frame[int(y0 * 1080):int(y1 * 1080), int(x0 * 1920):int(x1 * 1920)] = (0, 255, 0)
    return frame


def context(frame, *, motion=0.0, now=10.0):
    return ProfileContext(frame=frame, previous_frame=None, motion=motion, template=None, now=now)


def profile(text=""):
    p = JakAndDaxterV4Profile(
        {
            "mode": "production",
            "ocr_enabled": False,
            "progress_probe_initial_delay_seconds": 999.0,
        }
    )
    p.ocr = FakeOCR(text)
    return p


def test_static_real_menu_shape_can_use_visual_fallback_without_perfect_ocr():
    p = profile("")
    controller = FakeController()
    action = p.tick(controller, context(frame_with_menu_green(), motion=0.0))

    assert p.main_menu_visible is True
    assert p.new_game_selected is True
    assert p.main_menu_detection_source == "visual-fallback"
    assert any(button == "cross" for button, _ in controller.taps)
    assert "NEW GAME" in action


def test_partial_ocr_can_promote_visual_menu_before_motion_settles():
    p = profile("OPTIONS")
    controller = FakeController()
    p.tick(controller, context(frame_with_menu_green(), motion=1.0))

    assert p.main_menu_visible is True
    assert p.main_menu_ocr_markers == 1
    assert p.main_menu_detection_source == "visual-fallback"
    assert any(button == "cross" for button, _ in controller.taps)


def test_green_competitors_reject_visual_fallback():
    p = profile("")
    controller = FakeController()
    action = p.tick(controller, context(frame_with_menu_green(competitors=True), motion=0.0))

    assert p.main_menu_visible is False
    assert p.new_game_selected is False
    assert controller.taps == []
    assert "fail closed" in action


def test_semantic_menu_without_verified_new_game_highlight_does_not_confirm():
    p = profile("NEW GAME | LOAD GAME | OPTIONS")
    controller = FakeController()
    blank = np.zeros((1080, 1920, 3), dtype=np.uint8)
    action = p.tick(controller, context(blank, motion=0.0))

    assert p.main_menu_visible is True
    assert p.main_menu_detection_source == "ocr-quorum"
    assert p.new_game_selected is False
    assert controller.taps == []
    assert "highlight unverified" in action


def test_first_run_save_prompt_moves_from_no_to_yes_then_confirms():
    text = (
        "NO JAK AND DAXTER GAME DATA ON THE MEMORY CARD (PS2) INSERTED IN MEMORY CARD SLOT 1. "
        "WOULD YOU LIKE TO CREATE A JAK AND DAXTER SAVE FILE? YES NO"
    )
    p = profile(text)
    controller = FakeController()

    first = p.tick(controller, context(frame_with_save_choice("no"), now=10.0))
    assert p.save_prompt_visible is True
    assert p.save_prompt_kind == "create"
    assert p.save_no_selected is True
    assert controller.taps[-1][0] == "left"
    assert "LEFT toward YES" in first

    second = p.tick(controller, context(frame_with_save_choice("yes"), now=10.5))
    assert p.save_yes_selected is True
    assert controller.taps[-1][0] == "cross"
    assert p.save_prompt_selects == 1
    assert p.save_prompt_confirms == 1
    assert "verified YES" in second


def test_save_prompt_can_confirm_after_bounded_left_when_highlight_is_unclear():
    text = "MEMORY CARD GAME DATA: WOULD YOU LIKE TO CREATE A SAVE FILE? YES NO"
    p = profile(text)
    controller = FakeController()
    blank = frame_with_save_choice(None)

    p.tick(controller, context(blank, now=20.0))
    assert controller.taps[-1][0] == "left"

    action = p.tick(controller, context(blank, now=20.5))
    assert controller.taps[-1][0] == "cross"
    assert p.save_prompt_confirms == 1
    assert "LEFT settled" in action


def test_destructive_memory_card_prompt_remains_fail_closed():
    p = profile("MEMORY CARD: FORMAT OR ERASE SAVE GAME DATA? YES NO")
    controller = FakeController()
    action = p.tick(controller, context(frame_with_save_choice("no"), now=30.0))

    assert p.save_prompt_visible is False
    assert controller.taps == []
    assert "fail closed" in action
