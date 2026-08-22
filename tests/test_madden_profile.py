import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.madden2005 import Madden2005Profile


class FakeController:
    def __init__(self): self.events = []
    def tap(self, action, duration=0.08): self.events.append(("tap", action))
    def hold(self, action): self.events.append(("hold", action))
    def release(self, action): self.events.append(("release", action))
    def release_all(self): self.events.append(("release_all", None))
    def set_left_stick(self, x, y): self.events.append(("stick", round(x, 2), round(y, 2)))
    def neutral_sticks(self): self.set_left_stick(0.0, 0.0)


def test_field_idle_eventually_taps_cross():
    profile = Madden2005Profile({"pre_snap_wait_seconds": 0.1})
    controller = FakeController()
    frame = np.zeros((200, 300, 3), dtype=np.uint8)
    frame[:, :, 1] = 160
    profile.tick(controller, ProfileContext(frame, 0.0, None, 10.0))
    profile.tick(controller, ProfileContext(frame, 0.0, None, 10.2))
    assert ("tap", "cross") in controller.events
