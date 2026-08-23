from ps2_autopilot.madden_menu import MaddenScreen
from ps2_autopilot.profiles.madden2005_v17 import Madden2005V17Profile


def profile():
    return Madden2005V17Profile({"ocr_enabled": False})


class FakeController:
    def __init__(self):
        self.events = []

    def neutral_sticks(self):
        self.events.append(("neutral", None))

    def tap(self, action, duration=0.08):
        self.events.append(("tap", action))


def test_compact_select_teams_is_authoritative_team_select():
    text = (
        "SELECTTEAMS | OVERALL | 8182 | OFFENSE | 8678 | DEFENSE | AWAY | HOME | "
        "BILLS | DOLPHINS | R1HISTORICTEAMS | LOAD TEAM | SELECTSTADIUM"
    )
    assert Madden2005V17Profile._compact_menu_screen(text) == MaddenScreen.TEAM_SELECT


def test_compact_select_sides_is_authoritative_controller_select():
    text = "SELECTSIDES | CPU | AWAY | PROF1 | HOME | JETS | BILLS | OFFENSE"
    assert Madden2005V17Profile._compact_menu_screen(text) == MaddenScreen.CONTROLLER_SELECT


def test_loading_weather_and_player_cards_are_presentation():
    assert Madden2005V17Profile._explicit_presentation_reason(
        "PEYTON MANNING | CAREER STATS | PASSING YARDS | TD PASSES | INTERCEPTIONS | LOADING"
    ) == "loading/player-card presentation"

    assert Madden2005V17Profile._explicit_presentation_reason(
        "WEATHER | ORCHARD PARK NEW YORK | TEMPERATURE 35 DEG | WIND NW 3-8 MPH | HUMIDITY 44 | FORECAST FAIR"
    ) == "weather presentation"

    assert Madden2005V17Profile._explicit_presentation_reason(
        "CAREER STATS | PASSING YARDS | TD PASSES | INTERCEPTIONS | QB RATING | HEIGHT | WEIGHT"
    ) == "player-card presentation"


def test_memory_card_save_is_detected_and_never_confirmed():
    p = profile()
    c = FakeController()
    text = "SAVE | MEMORY CARD slot 1 | memory card (PS2) | 5654 KB free | STATUS: VALID"

    assert p._looks_like_save_screen(text)
    action = p._safe_save_backout(c, 10.0)

    assert ("tap", "triangle") in c.events
    assert not any(event == ("tap", "cross") for event in c.events)
    assert "never confirm memory card" in action


def test_pregame_hold_emits_no_navigation_or_gameplay_button():
    p = profile()
    c = FakeController()
    p.pregame_presentation = True
    p.pregame_reason = "pregame/stadium cinematic"

    action = p._pregame_hold(c, 20.0)

    assert [event for event in c.events if event[0] == "tap"] == []
    assert "hold inputs" in action
    assert p.pregame_holds == 1
