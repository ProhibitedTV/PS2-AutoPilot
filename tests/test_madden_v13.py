from ps2_autopilot.madden_menu import MenuHighlight
from ps2_autopilot.madden_ocr import OCRLine, OCRSnapshot
from ps2_autopilot.profiles.madden2005 import MaddenPhase
from ps2_autopilot.profiles.madden2005_v13 import Madden2005V13Profile


def profile():
    return Madden2005V13Profile(
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


class FakeController:
    def __init__(self):
        self.events = []

    def neutral_sticks(self):
        self.events.append(("neutral", None))

    def tap(self, action, duration=0.08):
        self.events.append(("tap", action))


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


def test_end_of_game_banner_is_authoritative_without_q4_clock_parse():
    p = profile()
    p.last_ocr = end_game_snapshot()

    assert p._looks_like_postgame_menu()


def test_postgame_moves_toward_end_game_without_crossing_stats():
    p = profile()
    p.phase = MaddenPhase.GAME_OVER
    p.last_ocr = end_game_snapshot()
    p.menu_highlight = MenuHighlight("STATS/INFO", 0.24, 0.50, 0.80)
    p.next_action_at = 0.0
    controller = FakeController()

    action = p._postgame(controller, 20.0)

    assert ("tap", "down") in controller.events
    assert ("tap", "cross") not in controller.events
    assert "toward END GAME" in action


def test_postgame_crosses_only_verified_end_game_row():
    p = profile()
    p.phase = MaddenPhase.GAME_OVER
    p.last_ocr = end_game_snapshot()
    p.menu_highlight = MenuHighlight("END GAME", 0.44, 0.55, 0.88)
    p.next_action_at = 0.0
    controller = FakeController()

    action = p._postgame(controller, 20.0)

    assert ("tap", "cross") in controller.events
    assert p.postgame_confirm_attempts == 1
    assert p.postgame_exit_pending_until == 23.0
    assert "verified END GAME" in action


def test_postgame_does_not_confirm_when_highlight_is_unverified():
    p = profile()
    p.phase = MaddenPhase.GAME_OVER
    p.last_ocr = end_game_snapshot()
    p.menu_highlight = None
    p.postgame_seek_steps = 5
    p.next_action_at = 0.0
    controller = FakeController()

    action = p._postgame(controller, 20.0)

    assert ("tap", "cross") not in controller.events
    assert "unverified" in action
