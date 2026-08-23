import cv2
import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v10 import GameplayCue, JakAndDaxterV10Profile
from ps2_autopilot.profiles.registry import build_profile


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


def profile(**extra):
    cfg = {
        "mode": "production",
        "ocr_enabled": False,
        "progress_probe_initial_delay_seconds": 999.0,
        "water_risk_confirmations_required": 1,
        "scout_cue_min_confidence": 0.25,
        "blue_eco_cue_min_confidence": 0.25,
    }
    cfg.update(extra)
    return JakAndDaxterV10Profile(cfg)


def ctx(frame, now=10.0, motion=0.02):
    return ProfileContext(
        frame=frame,
        previous_frame=frame.copy(),
        motion=motion,
        template=None,
        now=now,
    )


def warm_frame():
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    frame[:] = (35, 80, 110)
    return frame


def hsv_bgr(h, s, v):
    hsv = np.uint8([[[h, s, v]]])
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]


def scout_box_frame():
    frame = warm_frame()
    # Gray metal body with a strong red front panel, placed in the lower-center playfield.
    cv2.rectangle(frame, (150, 135), (210, 195), (155, 155, 155), -1)
    cv2.rectangle(frame, (160, 146), (200, 184), (20, 20, 225), -1)
    return frame


def blue_eco_frame():
    frame = warm_frame()
    blue = tuple(int(v) for v in hsv_bgr(102, 210, 245))
    cv2.rectangle(frame, (170, 105), (190, 185), blue, -1)
    cv2.rectangle(frame, (176, 90), (184, 188), (245, 245, 245), -1)
    cv2.circle(frame, (180, 150), 18, blue, -1)
    return frame


def test_scout_box_detector_prefers_red_gray_world_object():
    p = profile()
    cue = p._detect_scout_box(scout_box_frame())
    assert cue.kind == "scout_box"
    assert cue.confidence >= p.scout_cue_min_confidence
    assert abs(cue.x) < 0.25
    assert cue.y > 0.5


def test_blue_eco_detector_finds_bright_cyan_white_energy():
    p = profile()
    cue = p._detect_blue_eco(blue_eco_frame())
    assert cue.kind == "blue_eco"
    assert cue.confidence >= p.blue_eco_cue_min_confidence
    assert abs(cue.x) < 0.25


def test_scout_skill_executes_jump_then_dive_attack():
    p = profile(scouter_approach_seconds=0.2)
    controller = FakeController()
    frame = scout_box_frame()
    p.gameplay_cue = GameplayCue("scout_box", 0.05, 0.70, 0.01, 0.9)
    p._start_scout_dive(ctx(frame, now=1.0))

    p._service_skill(controller, ctx(frame, now=1.0))
    assert controller.taps == []

    p._service_skill(controller, ctx(frame, now=2.0))
    assert any(action == "cross" for action, _ in controller.taps)

    p._service_skill(controller, ctx(frame, now=2.3))
    assert any(action == "square" for action, _ in controller.taps)
    assert p.scout_dive_attempts == 1


def test_roll_jump_is_allowed_only_on_dry_stable_travel():
    p = profile(roll_jump_motion_min=0.01, roll_jump_water_max=0.05)
    frame = warm_frame()
    c = ctx(frame, now=20.0, motion=0.03)
    p.gameplay_cue = GameplayCue()
    p.water_ratio_total = 0.0
    p.water_geometry_confirmed = False
    p.next_roll_jump_at = 0.0

    assert p._can_roll_jump(c, 0.08) is True

    p.water_ratio_total = 0.10
    assert p._can_roll_jump(c, 0.08) is False

    p.water_ratio_total = 0.0
    p.water_geometry_confirmed = True
    assert p._can_roll_jump(c, 0.08) is False


def test_roll_jump_skill_uses_roll_then_cross():
    p = profile(roll_jump_roll_seconds=0.10, roll_jump_air_seconds=0.20)
    controller = FakeController()
    frame = warm_frame()
    p._start_roll_jump(ctx(frame, now=1.0), 0.05)

    p._service_skill(controller, ctx(frame, now=1.0))
    assert controller.taps[-1][0] == "l1"

    p._service_skill(controller, ctx(frame, now=1.2))
    assert controller.taps[-1][0] == "cross"
    assert p.skill_stage == "air"


def test_registry_promotes_jak_to_v10():
    p = build_profile({"name": "jak_and_daxter", "ocr_enabled": False})
    assert isinstance(p, JakAndDaxterV10Profile)
