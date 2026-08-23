from ps2_autopilot.madden_menu import GameSituation
from ps2_autopilot.madden_ocr import OCRLine, OCRSnapshot
from ps2_autopilot.madden_vision import MaddenObservation, MaddenVisualState
from ps2_autopilot.profiles.madden2005 import MaddenPhase
from ps2_autopilot.profiles.madden2005_v12 import Madden2005V12Profile


def profile():
    return Madden2005V12Profile(
        {
            "ocr_enabled": False,
            "final_zero_clock_confirm_seconds": 0.75,
            "final_presentation_hold_seconds": 12.0,
        }
    )


def snapshot() -> OCRSnapshot:
    lines = (
        OCRLine("JETS", 0.95, 0.25, 0.30, 0.10, 0.05),
        OCRLine("61", 0.95, 0.75, 0.30, 0.05, 0.05),
        OCRLine("BILLS", 0.95, 0.25, 0.50, 0.10, 0.05),
        OCRLine("6", 0.95, 0.75, 0.50, 0.05, 0.05),
    )
    return OCRSnapshot(
        lines,
        "NEW YORK JETS | 61 | BUFFALO BILLS | 6 | QTR | 4 | CLOCK | 0:00",
        True,
    )


def observation(state=MaddenVisualState.TRANSITION, green=0.10):
    return MaddenObservation(
        state=state,
        green_ratio=green,
        field_center_x=0.0,
        motion_center_x=0.0,
        brightness=0.5,
        motion=0.01,
        template_name=None,
        template_score=None,
    )


def test_two_team_q4_zero_clock_panel_is_postgame_evidence():
    p = profile()
    p.situation = GameSituation(quarter=4, clock_seconds=0)
    p.last_ocr = snapshot()

    assert p._looks_like_zero_clock_postgame(observation())


def test_live_final_snap_is_not_declared_game_over():
    p = profile()
    p.situation = GameSituation(quarter=4, clock_seconds=0)
    p.last_ocr = snapshot()

    assert not p._looks_like_zero_clock_postgame(
        observation(MaddenVisualState.LIVE_PLAY, green=0.70)
    )


def test_game_over_transition_holds_final_presentation_before_advancing():
    p = profile()
    p.phase = MaddenPhase.POST_PLAY

    p._transition_phase(MaddenPhase.GAME_OVER, 100.0)

    assert p.phase == MaddenPhase.GAME_OVER
    assert p.next_action_at >= 112.0
    assert "hold postgame presentation" in p.current_action
