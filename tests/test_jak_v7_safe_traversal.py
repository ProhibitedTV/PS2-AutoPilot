import cv2
import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v7 import JakAndDaxterV7Profile
from ps2_autopilot.profiles.registry import build_profile


class FakeController:
    def __init__(self):
        self.taps = []
        self.left = []
        self.right = []
        self.releases = []
        self.release_all_count = 0

    def tap(self, action, duration=0.08):
        self.taps.append((action, duration))

    def hold(self, action):
        pass

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


def profile(**extra):
    cfg = {
        "mode": "production",
        "ocr_enabled": False,
        "progress_probe_initial_delay_seconds": 999.0,
    }
    cfg.update(extra)
    return JakAndDaxterV7Profile(cfg)


def blue_bgr():
    hsv = np.uint8([[[110, 180, 120]]])
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]


def water_frame(*, left=True, center=True, right=False):
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    frame[:] = (40, 90, 120)  # warm-ish land/background, outside water hue gate
    h, w = frame.shape[:2]
    x0, x1, y0, y1 = JakAndDaxterV7Profile.WATER_ROI
    xa, xb = int(x0 * w), int(x1 * w)
    ya, yb = int(y0 * h), int(y1 * h)
    span = xb - xa
    a = xa + span // 3
    b = xa + (span * 2) // 3
    color = blue_bgr()
    if left:
        frame[ya:yb, xa:a] = color
    if center:
        frame[ya:yb, a:b] = color
    if right:
        frame[ya:yb, b:xb] = color
    return frame


def ctx(frame, now=10.0, motion=0.01):
    return ProfileContext(frame=frame, previous_frame=None, motion=motion, template=None, now=now)


def test_water_ratios_find_drier_right_side():
    p = profile()
    total, left, center, right = p._water_ratios(water_frame(left=True, center=True, right=False))
    assert total > 0.5
    assert left > 0.9
    assert center > 0.9
    assert right < 0.05


def test_water_risk_interrupts_with_backtrack_toward_drier_side():
    p = profile()
    controller = FakeController()
    c = ctx(water_frame(left=True, center=True, right=False), now=20.0)
    p._refresh_water_state(c)
    assert p.water_escape_active is True
    assert p.water_escape_direction > 0
    action = p._water_escape(controller, c)
    assert "WATER backtrack" in action
    x, y = controller.left[-1]
    assert x > 0
    assert y < 0


def test_water_guard_releases_after_dry_frames():
    p = profile(water_clear_seconds=0.5)
    wet = ctx(water_frame(left=True, center=True, right=False), now=20.0)
    p._refresh_water_state(wet)
    assert p.water_escape_active

    dry = np.zeros((240, 360, 3), dtype=np.uint8)
    p._refresh_water_state(ctx(dry, now=21.0))
    assert p.water_escape_active
    p._refresh_water_state(ctx(dry, now=21.6))
    assert p.water_escape_active is False


def test_registry_promotes_jak_to_v7():
    p = build_profile({"name": "jak_and_daxter", "ocr_enabled": False})
    assert isinstance(p, JakAndDaxterV7Profile)


def test_mid_session_probe_refuses_known_menu_text():
    p = profile(attach_probe_after_seconds=1.0)
    controller = FakeController()
    p.runtime_started_at = 0.0
    p.last_ocr_text = "OPTIONS BACK"
    action = p._service_attach_probe(controller, ctx(np.zeros((240, 360, 3), dtype=np.uint8), now=5.0, motion=0.0))
    assert action is None
    assert p.attach_probe_attempts == 0
    assert controller.right == []
