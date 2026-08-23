from ps2_autopilot.madden_vision import MaddenObservation, MaddenVisualState
from ps2_autopilot.profiles.madden2005 import MaddenPhase
from ps2_autopilot.profiles.madden2005_v10 import Madden2005V10Profile


def profile():
    return Madden2005V10Profile(
        {
            "ocr_enabled": False,
            "presentation_hold_seconds": 8.0,
            "event_presentation_hold_seconds": 12.0,
            "presentation_reentry_guard_seconds": 1.75,
            "presentation_live_guard_seconds": 0.90,
            "phase_stability_seconds": 0.18,
        }
    )


def obs(state: MaddenVisualState) -> MaddenObservation:
    return MaddenObservation(
        state=state,
        green_ratio=0.60,
        field_center_x=0.0,
        motion_center_x=0.0,
        brightness=0.5,
        motion=0.04,
        template_name=None,
        template_score=None,
    )


def test_live_looking_replay_stays_post_play_during_broadcast_hold():
    p = profile()
    p.phase = MaddenPhase.POST_PLAY
    p.phase_since = 10.0
    p.current_action = "presentation: watch standard sequence"

    result = p._stabilize_phase(MaddenPhase.LIVE, obs(MaddenVisualState.LIVE_PLAY), 12.0)

    assert result == MaddenPhase.POST_PLAY
    assert p.phase == MaddenPhase.POST_PLAY
    assert p.presentation_replay_live_suppressed == 1


def test_explicit_pre_snap_can_release_presentation_before_hold_expires():
    p = profile()
    p.phase = MaddenPhase.POST_PLAY
    p.phase_since = 10.0

    field_idle = obs(MaddenVisualState.FIELD_IDLE)
    p._stabilize_phase(MaddenPhase.PRE_SNAP, field_idle, 11.0)
    result = p._stabilize_phase(MaddenPhase.PRE_SNAP, field_idle, 11.3)

    assert result == MaddenPhase.PRE_SNAP
    assert p.phase == MaddenPhase.PRE_SNAP
    assert p.last_presentation_exit_at == 11.3
    assert "presentation released" in p.current_action


def test_stale_post_play_bounce_is_suppressed_after_pre_snap_release():
    p = profile()
    p.phase = MaddenPhase.POST_PLAY
    p.phase_since = 5.0
    p._transition_phase(MaddenPhase.PRE_SNAP, 10.0)

    result = p._stabilize_phase(
        MaddenPhase.POST_PLAY,
        obs(MaddenVisualState.FIELD_IDLE),
        10.6,
    )

    assert result == MaddenPhase.PRE_SNAP
    assert p.phase == MaddenPhase.PRE_SNAP
    assert p.presentation_reentry_suppressed == 1


def test_first_moment_of_live_play_rejects_stale_post_play_reentry():
    p = profile()
    p.phase = MaddenPhase.POST_PLAY
    p.phase_since = 5.0
    p._transition_phase(MaddenPhase.LIVE, 10.0)

    result = p._stabilize_phase(
        MaddenPhase.POST_PLAY,
        obs(MaddenVisualState.FIELD_IDLE),
        10.4,
    )

    assert result == MaddenPhase.LIVE
    assert p.phase == MaddenPhase.LIVE
    assert p.presentation_reentry_suppressed == 1


def test_live_transition_clears_stale_presentation_action_label():
    p = profile()
    p.phase = MaddenPhase.POST_PLAY
    p.phase_since = 5.0
    p.current_action = "presentation: watch event sequence (10.2s minimum hold)"

    p._transition_phase(MaddenPhase.LIVE, 20.0)

    assert p.phase == MaddenPhase.LIVE
    assert p.current_action == "live: presentation released / acquire play"
    assert p.presentation_nudges == 0
    assert not p.presentation_prompt_seen
