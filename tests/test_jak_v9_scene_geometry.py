import cv2
import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v9 import JakAndDaxterV9Profile
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
    }
    cfg.update(extra)
    return JakAndDaxterV9Profile(cfg)


def blue_bgr():
    hsv = np.uint8([[[110, 180, 120]]])
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]


def warm_frame():
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    frame[:] = (40, 90, 120)
    return frame


def water_roi_pixels(frame):
    h, w = frame.shape[:2]
    x0, x1, y0, y1 = JakAndDaxterV9Profile.WATER_ROI
    return int(x0 * w), int(x1 * w), int(y0 * h), int(y1 * h)


def fragmented_blue_frame():
    """High raw blue ratio made from many narrow disconnected scenery patches."""
    frame = warm_frame()
    xa, xb, ya, yb = water_roi_pixels(frame)
    rw = xb - xa
    rh = yb - ya
    color = blue_bgr()

    # Two rows of five isolated blocks. Combined blue coverage is intentionally above
    # V7's 0.11 total-caution threshold, but no component resembles a water surface.
    block_w = max(4, int(rw * 0.075))
    block_h = max(4, int(rh * 0.22))
    for row_y in (ya + int(rh * 0.08), ya + int(rh * 0.58)):
        for frac_x in (0.02, 0.22, 0.42, 0.62, 0.82):
            x = xa + int(rw * frac_x)
            frame[row_y:row_y + block_h, x:x + block_w] = color
    return frame


def coherent_water_frame(*, left=True, center=True, right=True):
    frame = warm_frame()
    xa, xb, ya, yb = water_roi_pixels(frame)
    span = xb - xa
    a = xa + span // 3
    b = xa + (span * 2) // 3
    y0 = ya + int((yb - ya) * 0.30)
    color = blue_bgr()
    if left:
        frame[y0:yb, xa:a] = color
    if center:
        frame[y0:yb, a:b] = color
    if right:
        frame[y0:yb, b:xb] = color
    return frame


def ctx(frame, now=10.0, motion=0.01):
    return ProfileContext(
        frame=frame,
        previous_frame=frame.copy(),
        motion=motion,
        template=None,
        now=now,
    )


def test_fragmented_blue_scenery_does_not_claim_water_policy():
    p = profile()
    c = ctx(fragmented_blue_frame(), now=20.0)
    p._refresh_water_state(c)

    assert p.water_ratio_total > p.water_total_caution
    assert p.water_geometry.candidate_ratio > p.water_total_caution
    assert p.water_geometry_confirmed is False
    assert p.water_escape_active is False
    assert p.water_false_color_frames == 1


def test_coherent_horizontal_water_surface_still_triggers_guardrail():
    p = profile()
    c = ctx(coherent_water_frame(), now=20.0)
    p._refresh_water_state(c)

    assert p.water_geometry_confirmed is True
    assert p.water_geometry.largest_component_ratio >= p.water_component_min_ratio
    assert p.water_geometry.largest_width_ratio >= p.water_component_min_width
    assert p.water_escape_active is True


def test_water_escape_direction_has_hysteresis_instead_of_frame_flipping():
    p = profile(water_direction_reconsider_seconds=10.0)

    # Left+center water means right is the drier escape side.
    p._refresh_water_state(ctx(coherent_water_frame(left=True, center=True, right=False), now=20.0))
    first_direction = p.water_escape_direction
    assert first_direction > 0

    # One frame later the opposite side looks drier. The direction lock should hold
    # rather than changing the left stick sign every frame.
    p._refresh_water_state(ctx(coherent_water_frame(left=False, center=True, right=True), now=21.0))
    assert p.water_escape_direction == first_direction
    assert p.water_direction_flips == 0


def test_local_stuck_recovery_triggers_before_global_watchdog_and_reverses_first():
    p = profile(
        local_stuck_trigger_seconds=0.6,
        local_stuck_command_warmup_seconds=0.2,
    )
    controller = FakeController()
    frame = warm_frame()

    p._arm_local_stuck(ctx(frame, now=1.0, motion=0.0))
    p._refresh_local_stuck(ctx(frame, now=1.3, motion=0.0))
    assert p.local_stuck_active is False
    p._refresh_local_stuck(ctx(frame, now=2.0, motion=0.0))
    assert p.local_stuck_active is True

    action = p._local_stuck_escape(controller, ctx(frame, now=2.0, motion=0.0))
    assert "obstacle escape reverse" in action
    assert controller.left[-1][1] < 0.0
    assert controller.taps == []


def test_local_stuck_retry_alternates_escape_direction():
    p = profile(local_stuck_max_nonjump_attempts=2)
    frame = warm_frame()
    p.local_stuck_active = True
    p.local_stuck_stage = "test"
    p.local_stuck_stage_until = 5.0
    p.local_stuck_direction = 1.0
    p.local_stuck_cycle_attempt = 0
    p.local_stuck_final_test = False
    controller = FakeController()

    action = p._local_stuck_escape(controller, ctx(frame, now=5.1, motion=0.0))
    assert "retry opposite side" in action
    assert p.local_stuck_direction < 0
    assert p.local_stuck_stage == "reverse"


def test_registry_promotes_jak_to_v9():
    p = build_profile({"name": "jak_and_daxter", "ocr_enabled": False})
    assert isinstance(p, JakAndDaxterV9Profile)
