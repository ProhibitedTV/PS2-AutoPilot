from ps2_autopilot.profiles.madden2005_v18 import Madden2005V18Profile


def profile():
    return Madden2005V18Profile(
        {
            "ocr_enabled": False,
            "ocr_async_enabled": True,
            "ocr_bootstrap_sync": True,
            "defense_contact_distance": 0.34,
            "defense_far_action_delay_seconds": 0.28,
        }
    )


def test_v18_wires_async_ocr_mode_flags():
    p = profile()
    assert p.ocr.async_enabled is True
    assert p.ocr.bootstrap_sync is True


def test_broadcast_score_latch_survives_frames_without_scorebug():
    p = profile()
    p._broadcast_score = lambda: ("BUF", 14, "MIA", 10)
    p._update_broadcast_latch()
    assert (p._broadcast_team_a, p._broadcast_score_a) == ("BUF", 14)
    assert (p._broadcast_team_b, p._broadcast_score_b) == ("MIA", 10)

    p._broadcast_score = lambda: (None, None, None, None)
    p._update_broadcast_latch()
    assert (p._broadcast_team_a, p._broadcast_score_a) == ("BUF", 14)
    assert (p._broadcast_team_b, p._broadcast_score_b) == ("MIA", 10)
