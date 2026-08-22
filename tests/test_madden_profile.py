import cv2
import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.madden2005 import Madden2005Profile, MaddenPhase, Possession
from ps2_autopilot.vision import TemplateMatch


class FakeController:
    def __init__(self):
        self.events = []

    def tap(self, action, duration=0.08): self.events.append(("tap", action))
    def hold(self, action): self.events.append(("hold", action))
    def release(self, action): self.events.append(("release", action))
    def release_all(self): self.events.append(("release_all", None))
    def set_left_stick(self, x, y): self.events.append(("stick", round(x, 2), round(y, 2)))
    def set_right_stick(self, x, y): self.events.append(("right_stick", round(x, 2), round(y, 2)))
    def neutral_sticks(self): self.set_left_stick(0.0, 0.0)


def green_frame() -> np.ndarray:
    hsv = np.zeros((200, 300, 3), dtype=np.uint8)
    hsv[:, :, 0] = 55
    hsv[:, :, 1] = 180
    hsv[:, :, 2] = 130
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def ctx(frame, motion, now, template=None, previous=None):
    return ProfileContext(frame, motion, template, now, previous)


def test_pre_snap_eventually_taps_cross():
    profile = Madden2005Profile({"pre_snap_wait_seconds": 0.1, "phase_stability_seconds": 0.05})
    controller = FakeController()
    frame = green_frame()

    profile.tick(controller, ctx(frame, 0.0, 10.0))
    profile.tick(controller, ctx(frame, 0.0, 10.1))
    profile.tick(controller, ctx(frame, 0.0, 10.25))
    assert profile.phase == MaddenPhase.PRE_SNAP
    assert ("tap", "cross") in controller.events


def test_snap_causality_infers_offense():
    profile = Madden2005Profile({"pre_snap_wait_seconds": 0.05, "phase_stability_seconds": 0.05})
    controller = FakeController()
    frame = green_frame()

    profile.tick(controller, ctx(frame, 0.0, 1.0))
    profile.tick(controller, ctx(frame, 0.0, 1.1))
    profile.tick(controller, ctx(frame, 0.0, 1.2))
    profile.tick(controller, ctx(frame, 0.05, 1.28, previous=frame))
    profile.tick(controller, ctx(frame, 0.05, 1.40, previous=frame))

    assert profile.phase == MaddenPhase.LIVE
    assert profile.possession == Possession.OFFENSE
    assert profile.possession_confidence >= 0.8


def test_labeled_template_sets_defense_immediately():
    profile = Madden2005Profile({"template_threshold": 0.8})
    controller = FakeController()
    frame = green_frame()
    template = TemplateMatch("pre_snap_defense", 0.95)

    profile.tick(controller, ctx(frame, 0.0, 2.0, template=template))

    assert profile.phase == MaddenPhase.PRE_SNAP
    assert profile.possession == Possession.DEFENSE
    assert profile.possession_confidence >= 0.95


def test_live_to_idle_becomes_post_play_after_hold():
    profile = Madden2005Profile({"phase_stability_seconds": 0.05, "live_hold_seconds": 0.20})
    controller = FakeController()
    frame = green_frame()

    template = TemplateMatch("pre_snap_offense", 0.95)
    profile.tick(controller, ctx(frame, 0.0, 5.0, template=template))
    profile.last_snap_at = 5.05
    profile.tick(controller, ctx(frame, 0.06, 5.10, previous=frame))
    profile.tick(controller, ctx(frame, 0.06, 5.22, previous=frame))
    assert profile.phase == MaddenPhase.LIVE

    profile.tick(controller, ctx(frame, 0.0, 5.50, previous=frame))
    profile.tick(controller, ctx(frame, 0.0, 5.62, previous=frame))
    assert profile.phase == MaddenPhase.POST_PLAY
    assert profile.plays_completed == 1
