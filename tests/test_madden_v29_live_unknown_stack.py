import numpy as np

from ps2_autopilot.madden_menu import MaddenScreen
from ps2_autopilot.profiles.madden2005 import MaddenPhase, PlayIntent, Possession
from ps2_autopilot.profiles.madden2005_v29 import Madden2005V29Profile


def profile() -> Madden2005V29Profile:
    return Madden2005V29Profile({"ocr_enabled": False, "rng_seed": 7})


def visual_playcall_frame() -> np.ndarray:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    # Synthetic version of the live Madden play-call chrome: saturated red lower
    # left and a dark play-diagram panel on the lower right.
    frame[int(720 * 0.64) : int(720 * 0.96), : int(1280 * 0.39)] = (0, 0, 185)
    frame[int(720 * 0.64) : int(720 * 0.96), int(1280 * 0.39) : int(1280 * 0.98)] = (
        28,
        28,
        28,
    )
    return frame


def test_degraded_live_title_prompt_reacquires_title():
    text = (
        "NFB | PLATERSR | SPORTS | MADDEN | 52 | N2005 | PRESS | BUTTON | "
        "2OO4ELECTRONICARTSINC.ALL RIGHTSRESERVED."
    )
    assert Madden2005V29Profile._compact_root_screen(text) == MaddenScreen.TITLE


def test_loading_card_is_not_visual_playcall():
    p = profile()
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    # Dark loading card with no characteristic red playbook rail.
    frame[int(720 * 0.64) : int(720 * 0.96), :] = (22, 38, 44)
    assert not p._looks_like_visual_playcall(frame)


def test_live_playcall_chrome_signature_matches_calibrated_geometry():
    p = profile()
    assert p._looks_like_visual_playcall(visual_playcall_frame())
    assert p.visual_playcall_red_ratio >= p.playcall_visual_red_ratio
    assert p.visual_playcall_right_dark_ratio >= p.playcall_visual_right_dark_ratio


def test_visual_reacquisition_returns_phase_to_playcall():
    p = profile()
    p.phase = MaddenPhase.MENU
    p.pregame_active = True
    p.pregame_presentation = True
    p.visual_playcall_red_ratio = 0.81
    p.visual_playcall_right_dark_ratio = 0.79

    p._apply_visual_playcall_reacquisition(20.0)

    assert p.phase == MaddenPhase.PLAYCALL
    assert p.menu_assessment.screen == MaddenScreen.PLAYCALL
    assert not p.pregame_active
    assert not p.pregame_presentation
    assert p.visual_playcall_reacquisitions == 1


def test_sparse_visual_playcall_asks_madden_before_legacy_macro():
    p = profile()
    p.visual_playcall_signature = True
    p.possession = Possession.DEFENSE
    p.possession_confidence = 0.91

    p._arm_playcall(10.0)

    assert p.planned_play == PlayIntent.DEFENSE
    assert p.playcall_selection_mode == "ask-madden-bootstrap"
    assert p.ask_madden_attempted
    assert p.ask_madden_bootstraps == 1
    assert len(p.queue) == 1
    assert p.queue[0].action == "square"


def test_unreadable_ask_madden_cards_choose_only_recommended_card_buttons():
    p = profile()
    p.visual_playcall_signature = True
    p.possession = Possession.DEFENSE
    p.possession_confidence = 0.91
    p.ask_madden_attempted = True

    p._arm_playcall(12.0)

    assert p.playcall_selection_mode == "ask-madden-recommendation"
    assert p.ask_madden_recommendation_fallbacks == 1
    assert len(p.queue) == 1
    assert p.queue[0].action in {"square", "cross", "circle"}
