from ps2_autopilot.madden_menu import MaddenScreen, MenuAssessment, MenuHighlight
from ps2_autopilot.madden_ocr import OCRLine, OCRSnapshot
from ps2_autopilot.madden_runtime import RuntimeDirective
from ps2_autopilot.profiles.madden2005 import MaddenPhase
from ps2_autopilot.profiles.madden2005_v15 import Madden2005V15Profile


def profile():
    return Madden2005V15Profile(
        {
            "ocr_enabled": False,
            "final_presentation_hold_seconds": 12.0,
        }
    )


def snapshot(*items: tuple[str, float]) -> OCRSnapshot:
    lines = tuple(
        OCRLine(text=text, confidence=0.95, x=0.30, y=y, width=0.28, height=0.05)
        for text, y in items
    )
    return OCRSnapshot(lines, " | ".join(line.text for line in lines), True, None)


def end_game_snapshot():
    return snapshot(
        ("END OF GAME", 0.08),
        ("STATS/INFO", 0.24),
        ("GRUDGE MATCH", 0.34),
        ("END GAME", 0.44),
        ("Jets", 0.30),
        ("Bills", 0.48),
        ("Final Score", 0.36),
        ("52", 0.40),
        ("6", 0.52),
    )


class FakeController:
    def __init__(self):
        self.events = []

    def neutral_sticks(self):
        self.events.append(("neutral", None))

    def tap(self, action, duration=0.08):
        self.events.append(("tap", action))


def test_postgame_visual_menu_cannot_transiently_leave_game_over_and_reset_hold():
    p = profile()
    p.phase = MaddenPhase.GAME_OVER
    p.phase_since = 5.0
    p.last_ocr = end_game_snapshot()
    p.postgame_exit_pending_until = -1e9

    p._transition_phase(MaddenPhase.MENU, 20.0)

    assert p.phase == MaddenPhase.GAME_OVER
    assert p.phase_since == 5.0
    assert p.postgame_phase_exit_suppressed == 1


def test_verified_postgame_rows_move_down_down_then_cross():
    p = profile()
    p.phase = MaddenPhase.GAME_OVER
    p.last_ocr = end_game_snapshot()
    c = FakeController()

    p.menu_highlight = MenuHighlight("STATS/INFO", 0.24, 0.50, 0.80)
    p.next_action_at = 0.0
    assert "DOWN toward END GAME" in p._postgame(c, 20.0)

    p.menu_highlight = MenuHighlight("GRUDGE MATCH", 0.34, 0.50, 0.80)
    assert "DOWN toward END GAME" in p._postgame(c, 21.0)

    p.menu_highlight = MenuHighlight("END GAME", 0.44, 0.50, 0.80)
    assert "verified END GAME" in p._postgame(c, 22.0)

    taps = [action for kind, action in c.events if kind == "tap"]
    assert taps == ["down", "down", "cross"]


def test_game_over_generic_progress_recovery_emits_no_navigation_input():
    p = profile()
    p.phase = MaddenPhase.GAME_OVER
    c = FakeController()

    action = p._progress_recover(
        c,
        RuntimeDirective(level=2, reason="no semantic progress in game_over", stalled_seconds=30.0),
        30.0,
    )

    assert [event for event in c.events if event[0] == "tap"] == []
    assert "suppressed" in action


def test_football_101_is_recognized_as_frontend_carousel_not_nested_drill():
    assert Madden2005V15Profile._looks_like_frontend_carousel_text(
        "FOOTBALL 101 | X Select | Square Help"
    )
    assert not Madden2005V15Profile._looks_like_frontend_carousel_text(
        "FOOTBALL 101 | SELECT DRILL | START DRILL"
    )


def test_non_play_now_frontend_carousel_seeks_without_cross():
    p = profile()
    p.last_ocr = snapshot(("FOOTBALL 101", 0.50), ("Select", 0.90), ("Help", 0.90))
    p.menu_assessment = MenuAssessment(MaddenScreen.MAIN_MENU, 0.97, "Madden front-end carousel")
    p.next_action_at = 0.0
    c = FakeController()

    action = p._menu(c, None, 20.0)

    taps = [event for event in c.events if event[0] == "tap"]
    assert taps == [("tap", "left")]
    assert "seek PLAY NOW" in action
