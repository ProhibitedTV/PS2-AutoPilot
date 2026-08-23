from ps2_autopilot.profiles import Madden2005Profile
from ps2_autopilot.profiles.madden2005_v20 import Madden2005V20Profile


def profile(**overrides):
    cfg = {
        "ocr_enabled": False,
        "random_seed": 17,
        "team_rotation_enabled": True,
        "team_rotation_min_steps": 1,
        "team_rotation_max_steps": 6,
    }
    cfg.update(overrides)
    return Madden2005V20Profile(cfg)


def test_default_profile_alias_points_at_v20():
    assert Madden2005Profile is Madden2005V20Profile


def test_extract_matchup_from_captured_team_select_text():
    text = "SELECT TEAMS | OVERALL | DOLPHINS | 10-6-0 | BILLS | 6-10-0 | Historic Teams"
    assert Madden2005V20Profile._extract_matchup(text) == ("DOLPHINS", "BILLS")


def test_rotation_plan_changes_both_sides_without_historic_team_buttons():
    p = profile()
    p._start_team_rotation(10.0)

    assert p.team_rotation_home_steps >= 1
    assert p.team_rotation_away_steps >= 1
    assert p.team_rotation_plan.count("left") == 1
    assert all(action in {"up", "down", "left"} for action in p.team_rotation_plan)
    assert "l1" not in p.team_rotation_plan
    assert "r1" not in p.team_rotation_plan
    assert "cross" not in p.team_rotation_plan

    split = p.team_rotation_plan.index("left")
    assert split == p.team_rotation_home_steps
    assert len(p.team_rotation_plan) - split - 1 == p.team_rotation_away_steps
    assert set(p.team_rotation_plan[:split]) == {p.team_rotation_home_direction}
    assert set(p.team_rotation_plan[split + 1 :]) == {p.team_rotation_away_direction}


def test_rotation_randomizes_controlled_home_or_away_side():
    p = profile()
    seen = set()
    for i in range(32):
        p._start_team_rotation(float(i))
        seen.add(p.controlled_side)
    assert seen == {"away", "home"}


def test_rotation_bounds_are_clamped_to_nonzero_safe_range():
    p = profile(team_rotation_min_steps=0, team_rotation_max_steps=99)
    assert p.team_rotation_min_steps == 1
    assert p.team_rotation_max_steps == 10
    for i in range(20):
        p._start_team_rotation(float(i))
        assert 1 <= p.team_rotation_home_steps <= 10
        assert 1 <= p.team_rotation_away_steps <= 10
