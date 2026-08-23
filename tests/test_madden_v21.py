from ps2_autopilot.madden_menu import MaddenScreen, MenuAssessment
from ps2_autopilot.profiles.madden2005_v21 import Madden2005V21Profile


def profile(**overrides):
    cfg = {
        "ocr_enabled": False,
        "random_seed": 21,
        "active_game_unknown_grace_seconds": 10.0,
    }
    cfg.update(overrides)
    return Madden2005V21Profile(cfg)


def test_v21_profile_remains_available():
    assert profile().name == "madden2005"


def test_profile_save_prompt_requires_explicit_modal_evidence():
    assert Madden2005V21Profile._looks_like_save_screen(
        "Do you want to save your Profile to memory card (PS2)? | Yes | Continue Without Saving | Enable Autosave"
    )
    assert not Madden2005V21Profile._looks_like_save_screen(
        "MEMORY CARD | SAVE"
    )


def test_memory_card_slot_picker_still_counts_as_real_save_context():
    assert Madden2005V21Profile._looks_like_save_screen(
        "SAVE | MEMORY CARD slot 1 | MEMORY CARD slot 2 | Status: Valid | 5,654 KB free"
    )


def test_active_game_grace_is_bounded():
    p = profile()
    p.runtime_monitor.active_game = True
    p.last_confident_gameplay_at = 100.0
    assert p._recent_active_gameplay(109.9)
    assert not p._recent_active_gameplay(110.1)


def test_unknown_and_dialog_are_ambiguous_during_recent_active_gameplay():
    p = profile()
    p.runtime_monitor.active_game = True
    p.last_confident_gameplay_at = 50.0

    p.menu_assessment = MenuAssessment(MaddenScreen.UNKNOWN, 0.55, "unclassified OCR")
    assert p._ambiguous_menu_during_game(55.0)

    p.menu_assessment = MenuAssessment(MaddenScreen.DIALOG, 0.72, "weak dialog")
    assert p._ambiguous_menu_during_game(55.0)

    p.menu_assessment = MenuAssessment(MaddenScreen.PAUSED, 0.96, "pause")
    assert not p._ambiguous_menu_during_game(55.0)
