from ps2_autopilot.madden_ocr import OCRLine, OCRSnapshot
from ps2_autopilot.profiles.madden2005_v14 import Madden2005V14Profile


def profile():
    return Madden2005V14Profile({"ocr_enabled": False})


def snapshot(*items: tuple[str, float]) -> OCRSnapshot:
    lines = tuple(
        OCRLine(text=text, confidence=0.95, x=0.35, y=y, width=0.40, height=0.05)
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


def test_stats_info_tab_screen_is_detected():
    p = profile()
    p.last_ocr = snapshot(
        ("STATS/INFO", 0.08),
        ("GAME", 0.18),
        ("INDIVIDUAL", 0.28),
        ("SCORING", 0.38),
        ("DRIVE SUMMARY", 0.48),
        ("Jets", 0.30),
        ("Bills", 0.50),
    )

    assert p._looks_like_stats_screen()


def test_game_stats_table_is_detected():
    p = profile()
    p.last_ocr = snapshot(
        ("GAME STATS", 0.08),
        ("TOTAL OFFENSE", 0.24),
        ("RUSHING YARDS", 0.30),
        ("PASSING YARDS", 0.36),
        ("FIRST DOWNS", 0.42),
        ("TOTAL YARDS", 0.54),
        ("GIVEAWAYS", 0.60),
    )

    assert p._looks_like_stats_screen()


def test_stats_recovery_uses_triangle_not_cross():
    p = profile()
    p.next_action_at = 0.0
    controller = FakeController()

    action = p._stats_backout(controller, 20.0)

    assert ("tap", "triangle") in controller.events
    assert ("tap", "cross") not in controller.events
    assert p.stats_backout_attempts == 1
    assert p.stats_backout_pending_until == 21.25
    assert "back to parent menu" in action


def test_stats_recovery_waits_for_parent_reclassification():
    p = profile()
    p.stats_backout_pending_until = 21.25
    p.current_action = "stats: TRIANGLE back to parent menu 1"
    controller = FakeController()

    action = p._stats_backout(controller, 20.5)

    assert not any(event[0] == "tap" for event in controller.events)
    assert "waiting for parent menu" in action
