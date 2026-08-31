import numpy as np

from ps2_autopilot.madden_menu import MaddenScreen, MenuAssessment
from ps2_autopilot.madden_ocr import OCRSnapshot
from ps2_autopilot.profiles.madden2005 import MaddenPhase
from ps2_autopilot.profiles.madden2005_v32 import Madden2005V32Profile


class FakeController:
    def __init__(self):
        self.events = []

    def neutral_sticks(self):
        self.events.append(("neutral", None))

    def tap(self, action, duration=0.08):
        self.events.append(("tap", action))


def profile() -> Madden2005V32Profile:
    return Madden2005V32Profile({"ocr_enabled": False, "rng_seed": 7})


def neutral_theme_playcall_frame() -> np.ndarray:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    # Reproduce the V31 Bills/Jets layout at a synthetic level: broad neutral gray
    # controls at lower-left and a dark team-colored play-diagram panel at right.
    frame[int(720 * 0.64) : int(720 * 0.96), : int(1280 * 0.39)] = (150, 150, 150)
    frame[int(720 * 0.64) : int(720 * 0.96), int(1280 * 0.39) : int(1280 * 0.98)] = (
        28,
        55,
        34,
    )
    return frame


def red_theme_playcall_frame() -> np.ndarray:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[int(720 * 0.64) : int(720 * 0.96), : int(1280 * 0.39)] = (0, 0, 185)
    frame[int(720 * 0.64) : int(720 * 0.96), int(1280 * 0.39) : int(1280 * 0.98)] = (
        28,
        28,
        28,
    )
    return frame


def dark_broadcast_frame() -> np.ndarray:
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    frame[:] = (34, 38, 42)
    return frame


def test_neutral_team_theme_playcall_signature_is_recognized():
    p = profile()

    assert p._looks_like_visual_playcall(neutral_theme_playcall_frame())
    assert p.visual_playcall_variant == "neutral"
    assert p.visual_playcall_left_neutral_ratio >= p.playcall_visual_neutral_ratio
    assert p.visual_playcall_right_dark_ratio >= p.playcall_visual_right_dark_ratio


def test_legacy_red_playcall_signature_still_works():
    p = profile()

    assert p._looks_like_visual_playcall(red_theme_playcall_frame())
    assert p.visual_playcall_variant == "red"


def test_dark_broadcast_cutaway_does_not_match_neutral_playcall():
    p = profile()

    assert not p._looks_like_visual_playcall(dark_broadcast_frame())
    assert p.visual_playcall_variant == "none"


def test_neutral_visual_reacquisition_can_release_post_play_presentation():
    p = profile()
    p.phase = MaddenPhase.POST_PLAY
    assert p._looks_like_visual_playcall(neutral_theme_playcall_frame())

    p._apply_visual_playcall_reacquisition(20.0)

    assert p.phase == MaddenPhase.PLAYCALL
    assert p.menu_assessment.screen == MaddenScreen.PLAYCALL
    assert p.theme_playcall_reacquisitions == 1
    assert "neutral" in p.menu_assessment.reason


def test_live_end_of_third_quarter_marker_is_explicit_presentation():
    text = "NEWYORK | JETS | BUFFALO | 57 | BILLS | END OF 3RD QUARTER"

    assert Madden2005V32Profile._quarter_break_marker(text) == "end of 3rd quarter"


def test_quarter_break_hold_never_emits_menu_buttons():
    p = profile()
    c = FakeController()
    p._activate_quarter_break("end of 3rd quarter", 10.0)
    p._own_quarter_break(10.1)

    action = p._pregame_hold(c, 10.2)

    assert action == "presentation: end of 3rd quarter; hold inputs"
    assert p.phase == MaddenPhase.TRANSITION
    assert p.pregame_presentation
    assert p.menu_assessment.screen == MaddenScreen.DIALOG
    assert not any(event[0] == "tap" for event in c.events)


def test_quarter_break_releases_when_real_playcall_returns():
    p = profile()
    p._activate_quarter_break("end of 3rd quarter", 10.0)
    p._own_quarter_break(10.1)
    p.menu_assessment = MenuAssessment(MaddenScreen.PLAYCALL, 0.98, "pick a play")

    assert p._quarter_break_resume_visible()
    p._clear_quarter_break()

    assert not p.quarter_break_active
    assert not p.pregame_presentation
    assert p.quarter_break_releases == 1


def test_quarter_break_timeout_is_bounded():
    p = profile()
    p._activate_quarter_break("halftime", 10.0)
    p._own_quarter_break(10.1)

    p._clear_quarter_break(timeout=True)

    assert not p.quarter_break_active
    assert p.quarter_break_timeouts == 1


def arm_sparse_postplay_spillover(p: Madden2005V32Profile) -> None:
    p.phase = MaddenPhase.MENU
    p.menu_assessment = MenuAssessment(MaddenScreen.UNKNOWN, 0.42, "unclassified OCR")
    p.runtime_monitor.active_game = True
    p.last_confident_gameplay_at = 12.5
    p.last_presentation_exit_at = 10.0
    p.last_observation = None
    p.last_ocr = OCRSnapshot(lines=(), text="", available=False)


def test_sparse_postplay_broadcast_cutaway_gets_bounded_hold():
    p = profile()
    arm_sparse_postplay_spillover(p)

    assert p._presentation_spillover_should_hold(13.0)


def test_postplay_spillover_owns_input_without_button_press():
    p = profile()
    c = FakeController()
    arm_sparse_postplay_spillover(p)

    p._own_presentation_spillover(13.0)
    action = p._pregame_hold(c, 13.1)

    assert p.presentation_spillover_active
    assert p.pregame_presentation
    assert p.menu_assessment.screen == MaddenScreen.DIALOG
    assert action == "presentation: post-play broadcast spillover; hold inputs"
    assert not any(event[0] == "tap" for event in c.events)


def test_postplay_spillover_expires_quickly():
    p = profile()
    arm_sparse_postplay_spillover(p)

    assert not p._presentation_spillover_should_hold(
        p.last_presentation_exit_at + p.presentation_spillover_seconds + 0.1
    )


def test_known_field_context_does_not_use_spillover_fallback():
    p = profile()
    arm_sparse_postplay_spillover(p)
    p._navigation_context = lambda: "field"

    assert not p._presentation_spillover_should_hold(13.0)
