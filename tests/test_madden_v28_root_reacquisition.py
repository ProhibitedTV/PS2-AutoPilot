from ps2_autopilot.madden_menu import MaddenScreen
from ps2_autopilot.profiles.madden2005 import MaddenPhase
from ps2_autopilot.profiles.madden2005_v28 import Madden2005V28Profile


def profile() -> Madden2005V28Profile:
    return Madden2005V28Profile({"ocr_enabled": False, "random_seed": 28})


def test_live_capture_pressstart_without_space_reacquires_title():
    # Exact semantic shape captured from the live V27 failure on 2026-08-31.
    text = (
        "NFB | PLATERSR | A | SPORTS | MADDEN | 52 | 2005 | "
        "PRESSSTART BUTTON | 2OO4ELECTRONICARTSINC.ALLRIGHTSRESERVED."
    )

    assert Madden2005V28Profile._compact_root_screen(text) == MaddenScreen.TITLE


def test_compact_play_now_reacquires_main_menu():
    text = "MADDEN NFL 2005 | PLAYNOW | GAME MODES | MY MADDEN"

    assert Madden2005V28Profile._compact_root_screen(text) == MaddenScreen.MAIN_MENU


def test_loading_player_card_is_not_mistaken_for_root_menu():
    text = (
        "NFLICONS | RAIDERS | JERRY | RICE | WR | CAREERSTATS | "
        "RECEIVINGYARDS | AVERAGEYAC | LOADING"
    )

    assert Madden2005V28Profile._compact_root_screen(text) is None
    assert (
        Madden2005V28Profile._explicit_presentation_reason(text)
        == "loading/player-card presentation"
    )


def test_root_reacquisition_clears_stale_pregame_ownership_and_transition_phase():
    p = profile()
    p.phase = MaddenPhase.TRANSITION
    p.candidate_phase = MaddenPhase.LIVE
    p.pregame_active = True
    p.pregame_presentation = True
    p.pregame_reason = "pregame/stadium cinematic"
    p.runtime_monitor.recovery_level = 3
    p.runtime_monitor.next_recovery_at = 99.0

    applied = p._apply_root_reacquisition(
        MaddenScreen.TITLE,
        42.0,
        reason="v28 compact root OCR: title",
    )

    assert applied
    assert p.phase == MaddenPhase.MENU
    assert p.candidate_phase is None
    assert not p.pregame_active
    assert not p.pregame_presentation
    assert p.pregame_reason is None
    assert p.root_menu_reacquisitions == 1
    assert p.runtime_monitor.recovery_level == 0
    assert p.runtime_monitor.next_recovery_at == 0.0


def test_non_root_screen_does_not_clear_pregame_guard():
    p = profile()
    p.phase = MaddenPhase.TRANSITION
    p.pregame_active = True
    p.pregame_presentation = True

    applied = p._apply_root_reacquisition(
        MaddenScreen.UNKNOWN,
        42.0,
        reason="unknown",
    )

    assert not applied
    assert p.phase == MaddenPhase.TRANSITION
    assert p.pregame_active
    assert p.pregame_presentation
    assert p.root_menu_reacquisitions == 0
