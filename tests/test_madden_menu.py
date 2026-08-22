from ps2_autopilot.madden_menu import (
    MaddenScreen,
    classify_madden_screen,
    parse_game_situation,
)
from ps2_autopilot.madden_ocr import OCRLine, OCRSnapshot


def snap(*items: tuple[str, float]) -> OCRSnapshot:
    lines = tuple(
        OCRLine(text=text, confidence=0.95, x=0.5, y=y, width=0.4, height=0.08)
        for text, y in items
    )
    return OCRSnapshot(lines=lines, text=" | ".join(x.text for x in lines), available=True)


def test_franchise_setup_is_escape_state():
    s = snap(("FRANCHISE SETUP", 0.08), ("TEAM SELECT", 0.52))
    result = classify_madden_screen(s)
    assert result.screen == MaddenScreen.WRONG_MODE


def test_pocket_presence_drill_is_escape_state():
    s = snap(
        ("SELECT DRILL/PLAYER", 0.08),
        ("POCKET PRESENCE", 0.25),
        ("START DRILL", 0.72),
        ("CANCEL", 0.82),
    )
    assert classify_madden_screen(s).screen == MaddenScreen.WRONG_MODE


def test_play_now_main_menu():
    s = snap(("PLAY NOW", 0.30), ("GAME MODES", 0.42), ("MY MADDEN", 0.54))
    assert classify_madden_screen(s).screen == MaddenScreen.MAIN_MENU


def test_parse_down_distance_quarter_clock():
    s = snap(("3RD & 7", 0.08), ("QTR 4", 0.08), ("2:14", 0.08))
    situation = parse_game_situation(s)
    assert situation.down == 3
    assert situation.distance == 7
    assert situation.quarter == 4
    assert situation.clock_seconds == 134


def test_parse_goal_to_go():
    s = snap(("2ND & GOAL", 0.08))
    situation = parse_game_situation(s)
    assert situation.down == 2
    assert situation.goal_to_go
