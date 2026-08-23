from ps2_autopilot.madden_menu import MaddenScreen, MenuAssessment, MenuHighlight
from ps2_autopilot.madden_ocr import OCRLine, OCRSnapshot
from ps2_autopilot.madden_runtime import RuntimeDirective
from ps2_autopilot.profiles.madden2005_v16 import Madden2005V16Profile


def profile():
    return Madden2005V16Profile(
        {
            "ocr_enabled": False,
            "frontend_play_now_confirm_seconds": 0.85,
            "frontend_axis_stall_steps": 3,
        }
    )


def snapshot(*items: tuple[str, float]) -> OCRSnapshot:
    lines = tuple(
        OCRLine(text=text, confidence=0.95, x=0.35, y=y, width=0.30, height=0.06)
        for text, y in items
    )
    return OCRSnapshot(lines, " | ".join(line.text for line in lines), True, None)


class FakeController:
    def __init__(self):
        self.events = []

    def neutral_sticks(self):
        self.events.append(("neutral", None))

    def tap(self, action, duration=0.08):
        self.events.append(("tap", action))


def prepare_frontend(p, text="FOOTBALL 101"):
    p.last_ocr = snapshot((text, 0.50), ("Select", 0.90), ("Help", 0.90))
    p.menu_assessment = MenuAssessment(MaddenScreen.MAIN_MENU, 0.97, "Madden front-end carousel")
    p.next_action_at = 0.0
    p.menu.next_action_at = 0.0


def test_play_now_candidate_holds_before_cross_then_confirms_transactionally():
    p = profile()
    prepare_frontend(p, "PLAY NOW")
    c = FakeController()

    first = p._menu(c, None, 20.0)
    assert "candidate" in first
    assert [event for event in c.events if event[0] == "tap"] == []

    second = p._menu(c, None, 21.0)
    assert "verified PLAY NOW" in second
    assert [event for event in c.events if event[0] == "tap"] == [("tap", "cross")]
    assert p.frontend_verified_crosses == 1
    assert p.menu.pending is not None
    assert p.menu.pending.action == "cross"
    assert MaddenScreen.TEAM_SELECT in p.menu.pending.expected


def test_conflicting_highlight_forbids_cross_even_when_play_now_text_is_visible():
    p = profile()
    prepare_frontend(p, "PLAY NOW")
    p.frontend_last_marker = "PLAYNOW"
    p.frontend_play_now_candidate_since = 10.0
    p.menu_highlight = MenuHighlight("FOOTBALL 101", 0.50, 0.55, 0.80)
    c = FakeController()

    action = p._menu(c, None, 20.0)

    taps = [event for event in c.events if event[0] == "tap"]
    assert taps == [("tap", "left")]
    assert "seek PLAY NOW" in action
    assert p.frontend_verified_crosses == 0


def test_frontend_seek_rotates_axis_when_same_tile_does_not_change():
    p = profile()
    prepare_frontend(p, "FOOTBALL 101")
    c = FakeController()

    actions = [p._menu(c, None, now) for now in (20.0, 21.0, 22.0, 23.0)]
    taps = [action for kind, action in c.events if kind == "tap"]

    assert taps[:3] == ["left", "left", "left"]
    assert taps[3] == "up"
    assert "UP seek PLAY NOW" in actions[3]


def test_frontend_generic_progress_recovery_emits_no_navigation_input():
    p = profile()
    prepare_frontend(p, "FOOTBALL 101")
    c = FakeController()

    action = p._progress_recover(
        c,
        RuntimeDirective(level=2, reason="no semantic progress in menu", stalled_seconds=30.0),
        30.0,
    )

    assert [event for event in c.events if event[0] == "tap"] == []
    assert "suppressed" in action
    assert p.frontend_recovery_suppressed == 1
