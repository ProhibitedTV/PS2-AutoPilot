from ps2_autopilot.madden_ocr import OCRSnapshot
from ps2_autopilot.profiles.madden2005 import Possession
from ps2_autopilot.profiles.madden2005_v24 import Madden2005V24Profile


def profile() -> Madden2005V24Profile:
    return Madden2005V24Profile(
        {
            "ocr_enabled": False,
            "random_seed": 11,
            "event_attribution_min_confidence": 0.60,
        }
    )


def note(
    p: Madden2005V24Profile,
    text: str,
    *,
    possession: Possession,
    confidence: float = 0.90,
    now: float = 10.0,
) -> None:
    p.possession = possession
    p.possession_confidence = confidence
    p.last_ocr = OCRSnapshot((), text, True)
    p._note_game_event(now)


def test_static_attribution_uses_controlled_side_semantics():
    cases = [
        ("touchdown", Possession.OFFENSE, "touchdown_for"),
        ("touchdown", Possession.DEFENSE, "touchdown_against"),
        ("field_goal", Possession.OFFENSE, "field_goal_for"),
        ("field_goal", Possession.DEFENSE, "field_goal_against"),
        ("interception", Possession.OFFENSE, "interception_thrown"),
        ("interception", Possession.DEFENSE, "interception_made"),
        ("sack", Possession.OFFENSE, "sack_suffered"),
        ("sack", Possession.DEFENSE, "sack_caused"),
        ("first_down", Possession.OFFENSE, "first_down_gained"),
        ("first_down", Possession.DEFENSE, "first_down_allowed"),
        ("fumble", Possession.OFFENSE, "fumble_on_offense"),
        ("fumble", Possession.DEFENSE, "opponent_fumble_observed"),
    ]
    for event, role, expected in cases:
        assert (
            Madden2005V24Profile.attribute_game_event(event, role, 0.90) == expected
        )


def test_low_confidence_event_remains_explicitly_unowned():
    assert (
        Madden2005V24Profile.attribute_game_event(
            "sack", Possession.DEFENSE, 0.40, minimum_confidence=0.60
        )
        == "sack_ownership_unknown"
    )
    assert (
        Madden2005V24Profile.attribute_game_event(
            "touchdown", Possession.UNKNOWN, 0.99, minimum_confidence=0.60
        )
        == "touchdown_ownership_unknown"
    )


def test_interception_is_attributed_from_pre_flip_possession():
    p = profile()
    note(p, "INTERCEPTION", possession=Possession.DEFENSE)

    # Base V6 semantics flip our role after the interception. Attribution must use
    # the role immediately before that mutation or it would invert made/thrown.
    assert p.possession == Possession.OFFENSE
    assert p.game_event_counts["interception"] == 1
    assert p.game_event_attribution_counts == {"interception_made": 1}
    assert p.last_game_event_attribution == "interception_made"


def test_offensive_interception_is_thrown_before_base_role_flip():
    p = profile()
    note(p, "INTERCEPTION", possession=Possession.OFFENSE)
    assert p.possession == Possession.DEFENSE
    assert p.game_event_attribution_counts == {"interception_thrown": 1}


def test_fumble_attribution_does_not_claim_recovery_ownership():
    p = profile()
    note(p, "FUMBLE", possession=Possession.DEFENSE)
    assert p.game_event_attribution_counts == {"opponent_fumble_observed": 1}
    assert p.possession_confidence <= 0.35


def test_banner_cooldown_does_not_double_count_attribution():
    p = profile()
    note(p, "SACK", possession=Possession.DEFENSE, now=10.0)
    note(p, "SACK", possession=Possession.DEFENSE, now=12.0)
    assert p.game_event_counts["sack"] == 1
    assert p.game_event_attribution_counts["sack_caused"] == 1


def test_penalty_is_observed_without_inventing_team_ownership():
    p = profile()
    note(p, "PENALTY", possession=Possession.OFFENSE)
    assert p.game_event_attribution_counts == {"penalty_observed": 1}
