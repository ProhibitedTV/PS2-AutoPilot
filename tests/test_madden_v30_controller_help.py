from ps2_autopilot.madden_ocr import OCRLine, OCRSnapshot
from ps2_autopilot.profiles.madden2005_v30 import Madden2005V30Profile


LIVE_HELP_OCR = (
    "CONTROLLERLAYOUT | R2 | PASSING | THROW TO | RECEIVER | THROWTO | RECEIVER | "
    "THROWTO | RECEIVER | THROWBALLAWAY | THROWTOL1 | RECEIVER | THROWTOR1 | "
    "RECEIVER | TURNPASSINGICONSON/OFF | PUMPFAKE | A | SPORTS | HELP"
)


class FakeController:
    def __init__(self):
        self.events = []

    def neutral_sticks(self):
        self.events.append(("neutral", None))

    def tap(self, action, duration=0.08):
        self.events.append(("tap", action))


def snapshot(text: str) -> OCRSnapshot:
    return OCRSnapshot(
        lines=(OCRLine(text=text, confidence=0.95, x=0.5, y=0.5, width=0.8, height=0.08),),
        text=text,
        available=True,
    )


def profile() -> Madden2005V30Profile:
    return Madden2005V30Profile({"ocr_enabled": False})


def test_exact_live_controller_layout_ocr_is_owned():
    assert Madden2005V30Profile._looks_like_controller_help(LIVE_HELP_OCR)


def test_controller_layout_requires_control_semantics():
    assert not Madden2005V30Profile._looks_like_controller_help("CONTROLLER LAYOUT")
    assert not Madden2005V30Profile._looks_like_controller_help(
        "DEFENSE PICK A PLAY | ASK MADDEN | 4-3 NORMAL"
    )


def test_controller_help_uses_triangle_not_cross():
    p = profile()
    c = FakeController()
    p.controller_help_visible = True
    p.last_ocr = snapshot(LIVE_HELP_OCR)

    action = p._controller_help(c, 10.0)

    assert action.startswith("help: controller layout -> TRIANGLE return")
    assert ("tap", "triangle") in c.events
    assert not any(event == ("tap", "cross") for event in c.events)
    assert p.controller_help_dismiss_attempts == 1
    assert p._controller_help_pending_exit


def test_same_async_snapshot_cannot_repeat_triangle():
    p = profile()
    c = FakeController()
    help_snapshot = snapshot(LIVE_HELP_OCR)
    p.controller_help_visible = True
    p.last_ocr = help_snapshot

    p._controller_help(c, 10.0)
    c.events.clear()
    p.next_action_at = 0.0  # prove snapshot ownership, not cooldown, blocks the tap

    action = p._controller_help(c, 12.0)

    assert "wait for fresh OCR" in action
    assert not any(event[0] == "tap" for event in c.events)
    assert p.controller_help_stale_snapshot_holds == 1


def test_fresh_help_snapshot_allows_bounded_retry():
    p = profile()
    c = FakeController()
    p.controller_help_visible = True
    p.last_ocr = snapshot(LIVE_HELP_OCR)
    p._controller_help(c, 10.0)

    c.events.clear()
    p.last_ocr = snapshot(LIVE_HELP_OCR)  # distinct completed OCR read
    p.next_action_at = 0.0
    action = p._controller_help(c, 12.0)

    assert "2/3" in action
    assert ("tap", "triangle") in c.events
    assert p.controller_help_dismiss_attempts == 2


def test_help_modal_suppresses_soft_recovery():
    p = profile()
    c = FakeController()
    p.controller_help_visible = True

    assert p._soft_stall_recovery(c, 1000.0) is None
    assert not any(event[0] == "tap" for event in c.events)


def test_help_modal_stops_after_bounded_retries():
    p = profile()
    c = FakeController()
    p.controller_help_visible = True
    p.controller_help_dismiss_attempts = p.controller_help_max_dismiss_attempts
    p.last_ocr = snapshot(LIVE_HELP_OCR)

    action = p._controller_help(c, 20.0)

    assert "safe hold after retries" in action
    assert not any(event[0] == "tap" for event in c.events)
