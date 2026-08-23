import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter import JakPhase
from ps2_autopilot.profiles.jak_and_daxter_v8 import JakAndDaxterV8Profile
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
        "reacquire_after_seconds": 0.8,
        "attach_probe_idle_seconds": 0.4,
        "attach_probe_drive_seconds": 0.2,
        "attach_probe_observe_seconds": 0.5,
        "attach_probe_response_min": 0.01,
        "reacquire_confirmations_required": 1,
    }
    cfg.update(extra)
    return JakAndDaxterV8Profile(cfg)


def ctx(now, motion=0.0):
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    return ProfileContext(frame=frame, previous_frame=frame.copy(), motion=motion, template=None, now=now)


def test_post_gameplay_visual_only_main_menu_is_suppressed():
    p = profile()
    p.gameplay_session_established = True
    p.main_menu_visible = True
    p.main_menu_detection_source = "visual-fallback"
    p.main_menu_ocr_markers = 0
    p.main_menu_ocr_quorum = 3
    assert p._should_suppress_visual_main_menu() is True


def test_semantic_menu_is_not_suppressed_after_gameplay():
    p = profile()
    p.gameplay_session_established = True
    p.main_menu_visible = True
    p.main_menu_detection_source = "ocr-quorum"
    p.main_menu_ocr_markers = 4
    assert p._should_suppress_visual_main_menu() is False


def test_lost_gameplay_probe_reacquires_and_backturns():
    p = profile()
    c = FakeController()
    p.gameplay_session_established = True
    p.phase = JakPhase.UNKNOWN
    p.runtime_started_at = 0.0
    p.lost_gameplay_since = 0.0

    # Establish an idle baseline, then begin the reversible camera-only probe.
    assert p._service_lost_gameplay_probe(c, ctx(1.0, 0.0)) is None
    action = p._service_lost_gameplay_probe(c, ctx(1.5, 0.0))
    assert "nudge camera only" in action
    assert c.right[-1][0] != 0.0

    # Release the camera stick and observe a measurable response.
    action = p._service_lost_gameplay_probe(c, ctx(1.8, 0.0))
    assert "observe" in action
    action = p._service_lost_gameplay_probe(c, ctx(1.9, 0.03))
    assert "back-turn" in action
    assert p.phase == JakPhase.GAMEPLAY
    assert p.reacquire_probe_successes == 1
    assert c.left[-1][1] < 0.0


def test_watchdog_does_not_inject_generic_menu_inputs_when_ownership_is_lost():
    p = profile()
    c = FakeController()
    p.gameplay_session_established = True
    p.phase = JakPhase.UNKNOWN
    action = p.recover(c)
    assert "V8 camera probe owns reacquisition" in action
    assert c.release_all_count == 1
    assert c.taps == []


def test_registry_promotes_jak_to_v8():
    p = build_profile({"name": "jak_and_daxter", "ocr_enabled": False})
    assert isinstance(p, JakAndDaxterV8Profile)
