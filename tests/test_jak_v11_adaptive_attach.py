import cv2
import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter import JakPhase
from ps2_autopilot.profiles.jak_and_daxter_v11 import JakAndDaxterV11Profile
from ps2_autopilot.profiles.registry import build_profile


class FakeController:
    def __init__(self):
        self.left = []
        self.right = []
        self.taps = []
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
        "attach_probe_after_seconds": 1.0,
        "attach_probe_idle_seconds": 0.4,
        "attach_probe_baseline_max": 0.020,
        "attach_probe_camera_x": 0.30,
        "attach_probe_drive_seconds": 0.20,
        "attach_probe_observe_seconds": 0.50,
        "attach_probe_retry_seconds": 0.50,
        "attach_adaptive_min_motion": 0.0036,
        "attach_adaptive_delta": 0.0016,
        "attach_adaptive_multiplier": 1.25,
        "attach_evidence_required": 2.0,
        "attach_evidence_decay": 0.35,
        "attach_evidence_window_seconds": 14.0,
        "attach_water_assist_ratio": 0.55,
        "attach_water_assist_required": 1.5,
        "reacquire_after_seconds": 0.8,
        "reacquire_confirmations_required": 1,
    }
    cfg.update(extra)
    return JakAndDaxterV11Profile(cfg)


def warm_frame():
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    frame[:] = (35, 75, 105)
    return frame


def water_frame():
    frame = warm_frame()
    hsv = np.uint8([[[110, 190, 150]]])
    blue = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]
    frame[125:, :] = blue
    return frame


def ctx(frame, now, motion):
    return ProfileContext(
        frame=frame,
        previous_frame=frame.copy(),
        motion=motion,
        template=None,
        now=now,
    )


def test_adaptive_threshold_matches_live_probe_scale():
    p = profile()
    p.attach_probe_baseline_motion = 0.0015
    assert p._adaptive_probe_threshold() == 0.0036

    p.attach_probe_baseline_motion = 0.0030
    assert abs(p._adaptive_probe_threshold() - 0.00535) < 1e-9


def test_repeatable_modest_camera_pulses_reacquire_water_gameplay():
    p = profile()
    c = FakeController()
    frame = water_frame()
    p.runtime_started_at = 0.0
    p.phase = JakPhase.UNKNOWN

    # First probe: live bundle scale, ~0.0015 idle -> ~0.006 camera response.
    assert p._service_attach_probe(c, ctx(frame, 2.0, 0.0015)) is None
    assert "nudge camera" in p._service_attach_probe(c, ctx(frame, 2.5, 0.0015))
    assert "observe response" in p._service_attach_probe(c, ctx(frame, 2.8, 0.0015))
    action = p._service_attach_probe(c, ctx(frame, 2.9, 0.0060))
    assert "adaptive attach evidence" in action
    assert p.attach_evidence > 0.75
    assert p.attach_water_assist_active is True
    assert p.phase == JakPhase.UNKNOWN

    # Second opposite-direction pulse supplies repeatability evidence and owns gameplay.
    assert p._service_attach_probe(c, ctx(frame, 3.4, 0.0015)) is None
    assert "nudge camera" in p._service_attach_probe(c, ctx(frame, 3.9, 0.0015))
    assert "observe response" in p._service_attach_probe(c, ctx(frame, 4.2, 0.0015))
    p._service_attach_probe(c, ctx(frame, 4.3, 0.0060))

    assert p.phase == JakPhase.GAMEPLAY
    assert p.attach_probe_successes == 1
    assert p.gameplay_session_established is True


def test_subthreshold_animation_does_not_grant_gameplay():
    p = profile(attach_water_assist_ratio=0.95)
    c = FakeController()
    frame = warm_frame()
    p.runtime_started_at = 0.0
    p.phase = JakPhase.UNKNOWN

    assert p._service_attach_probe(c, ctx(frame, 2.0, 0.0015)) is None
    assert "nudge camera" in p._service_attach_probe(c, ctx(frame, 2.5, 0.0015))
    assert "observe response" in p._service_attach_probe(c, ctx(frame, 2.8, 0.0015))
    assert "waiting" in p._service_attach_probe(c, ctx(frame, 2.9, 0.0032))
    action = p._service_attach_probe(c, ctx(frame, 3.4, 0.0032))

    assert "inconclusive" in action
    assert p.attach_evidence == 0.0
    assert p.phase == JakPhase.UNKNOWN


def test_sticky_gameplay_reacquire_uses_adaptive_live_threshold():
    p = profile()
    c = FakeController()
    frame = warm_frame()
    p.gameplay_session_established = True
    p.phase = JakPhase.UNKNOWN
    p.lost_gameplay_since = 0.0
    p.attach_probe_stage = "observe"
    p.attach_probe_baseline_motion = 0.0015
    p.attach_probe_peak_motion = 0.0015
    p.attach_probe_deadline = 10.0

    action = p._service_lost_gameplay_probe(c, ctx(frame, 5.0, 0.0060))

    assert p.phase == JakPhase.GAMEPLAY
    assert p.reacquire_probe_successes == 1
    assert "reacquired" in action


def test_registry_promotes_jak_to_v11():
    p = build_profile({"name": "jak_and_daxter", "ocr_enabled": False})
    assert isinstance(p, JakAndDaxterV11Profile)
