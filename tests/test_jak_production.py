import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter import JakPhase
from ps2_autopilot.profiles.jak_and_daxter_v3 import JakAndDaxterV3Profile
from ps2_autopilot.semantic_ocr import OCRSnapshot
from ps2_autopilot.vision import TemplateMatch


class FakeController:
    def __init__(self):
        self.taps = []
        self.holds = []
        self.releases = []
        self.left = []
        self.right = []
        self.release_all_count = 0

    def tap(self, action, duration=0.08):
        self.taps.append((action, duration))

    def hold(self, action):
        self.holds.append(action)

    def release(self, action):
        self.releases.append(action)

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
        return OCRSnapshot((), self.text, True, None)

    def telemetry(self, now):
        return {}


def ctx(now=1.0, template=None, motion=0.03, previous=None):
    frame = np.zeros((180, 320, 3), dtype=np.uint8)
    frame[:, :160] = 45
    frame[:, 160:] = 180
    return ProfileContext(
        frame=frame,
        previous_frame=previous,
        motion=motion,
        template=template,
        now=now,
    )


def profile(**overrides):
    cfg = {
        "mode": "production",
        "ocr_enabled": False,
        "production_jump_probability": 0.0,
        "progress_probe_initial_delay_seconds": 999.0,
        **overrides,
    }
    result = JakAndDaxterV3Profile(cfg)
    result.ocr = FakeOCR()
    return result


def test_production_gameplay_uses_analog_on_foot_policy():
    p = profile()
    controller = FakeController()
    action = p.tick(controller, ctx(template=TemplateMatch("jak_gameplay_geyser", 0.96)))
    assert p.phase == JakPhase.GAMEPLAY
    assert p.control_mode.value == "on_foot"
    assert any(abs(x) > 0 or abs(y) > 0 for x, y in controller.left)
    assert "jak:" in action


def test_cutscene_and_unknown_menu_fail_closed():
    p = profile()
    controller = FakeController()
    p.tick(controller, ctx(template=TemplateMatch("jak_cutscene", 0.96)))
    assert p.phase == JakPhase.CUTSCENE
    assert controller.taps == []

    p.ocr.text = "SELECT GAME | FILE 1 | FILE 2"
    p.tick(controller, ctx(now=5.0, template=None, motion=0.0))
    assert controller.taps == []


def test_opening_fallback_requires_elapsed_time_motion_and_no_save_text():
    p = profile(opening_cinematic_hold_seconds=10.0, opening_gameplay_motion_threshold=0.02)
    controller = FakeController()
    p.campaign_launch_at = 1.0

    p.tick(controller, ctx(now=8.0, template=None, motion=0.05))
    assert p.phase != JakPhase.GAMEPLAY

    p.ocr.text = "SELECT GAME | SAVE FILE"
    p.tick(controller, ctx(now=20.0, template=None, motion=0.05))
    assert p.phase != JakPhase.GAMEPLAY

    p.ocr.text = ""
    p.tick(controller, ctx(now=21.0, template=None, motion=0.0))
    assert p.phase != JakPhase.GAMEPLAY

    p.tick(controller, ctx(now=22.0, template=None, motion=0.05))
    assert p.phase == JakPhase.GAMEPLAY
    assert p.gameplay_assumed_after_opening is True


def test_progress_probe_does_not_release_held_r2_while_reading():
    p = profile(progress_probe_initial_delay_seconds=5.0, progress_probe_seconds=20.0)
    controller = FakeController()
    gameplay = TemplateMatch("jak_gameplay", 0.96)

    p.tick(controller, ctx(now=1.0, template=gameplay))
    p.tick(controller, ctx(now=6.1, template=gameplay))
    assert "r2" in controller.holds
    releases_before = controller.release_all_count

    p.tick(controller, ctx(now=6.5, template=gameplay))
    assert controller.release_all_count == releases_before
    assert "r2" not in controller.releases

    p.tick(controller, ctx(now=7.5, template=gameplay))
    assert "r2" in controller.releases


def test_watchdog_recovery_is_gameplay_only_and_zoomer_acceleration_is_bounded():
    p = profile()
    controller = FakeController()

    p.phase = JakPhase.UNKNOWN
    p.recover(controller)
    assert controller.taps == []

    p.phase = JakPhase.GAMEPLAY
    p.control_mode = p.control_mode.ZOOMER
    p.recover(controller)
    assert any(action == "cross" for action, _ in controller.taps)
    assert "cross" not in controller.holds


def test_fishing_mode_is_recognized_but_fail_closed_until_detector_exists():
    p = profile()
    controller = FakeController()
    action = p.tick(controller, ctx(template=TemplateMatch("jak_fishing", 0.96)))
    assert p.control_mode.value == "fishing"
    assert "await dedicated fish perception" in action
    assert controller.taps == []
