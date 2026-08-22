from ps2_autopilot.window import PCSX2Window


def test_best_candidate_uses_largest_client_area():
    assert PCSX2Window._best_candidate([(10, 100), (20, 500), (30, 300)]) == 20
    assert PCSX2Window._best_candidate([]) is None
