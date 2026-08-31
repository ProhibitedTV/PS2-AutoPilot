from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_spatial import SpatialSnapshot
from ps2_autopilot.madden_vision import MaddenObservation, MaddenVisualState
from ps2_autopilot.profiles.madden2005 import MaddenPhase, PlayIntent, Possession
from ps2_autopilot.profiles.madden2005_v24 import (
    Madden2005V24Profile,
    SpecialTeamsIntent,
    SpecialTeamsSide,
)
from ps2_autopilot.profiles.registry import build_profile


class RecordingController(Controller):
    def __init__(self):
        self.taps: list[str] = []
        self.left = (0.0, 0.0)
        self.right = (0.0, 0.0)

    def tap(self, action: str, duration: float = 0.08) -> None:
        del duration
        self.taps.append(action)

    def hold(self, action: str) -> None:
        del action

    def release(self, action: str) -> None:
        del action

    def release_all(self) -> None:
        pass

    def set_left_stick(self, x: float, y: float) -> None:
        self.left = (x, y)

    def set_right_stick(self, x: float, y: float) -> None:
        self.right = (x, y)


def profile() -> Madden2005V24Profile:
    return Madden2005V24Profile(
        {
            "ocr_enabled": False,
            "random_seed": 7,
            "special_return_action_seconds": 0.72,
        }
    )


def live_observation() -> MaddenObservation:
    return MaddenObservation(
        MaddenVisualState.LIVE_PLAY,
        green_ratio=0.50,
        field_center_x=0.12,
        motion_center_x=0.0,
        brightness=0.50,
        motion=0.10,
        template_name=None,
        template_score=None,
    )


def test_special_teams_classifier_is_strict_and_distinguishes_ownership():
    cases = {
        "KICKOFF": (SpecialTeamsIntent.KICKOFF, SpecialTeamsSide.KICKING),
        "KICK RETURN LEFT": (SpecialTeamsIntent.KICK_RETURN, SpecialTeamsSide.RETURNING),
        "PUNT": (SpecialTeamsIntent.PUNT, SpecialTeamsSide.KICKING),
        "PUNT RETURN": (SpecialTeamsIntent.PUNT_RETURN, SpecialTeamsSide.RETURNING),
        "FIELD GOAL": (SpecialTeamsIntent.FIELD_GOAL, SpecialTeamsSide.KICKING),
        "EXTRA POINT": (SpecialTeamsIntent.EXTRA_POINT, SpecialTeamsSide.KICKING),
    }
    for text, expected in cases.items():
        intent, side, confidence, _reason = Madden2005V24Profile.classify_special_teams(text)
        assert (intent, side) == expected
        assert confidence >= 0.95

    # Bare PAT is deliberately too ambiguous to authorize special-teams ownership.
    intent, side, confidence, _reason = Madden2005V24Profile.classify_special_teams("PAT")
    assert intent == SpecialTeamsIntent.UNKNOWN
    assert side == SpecialTeamsSide.UNKNOWN
    assert confidence == 0.0


def test_returning_side_never_executes_kick_meter_macro():
    p = profile()
    c = RecordingController()
    p.phase = MaddenPhase.KICKING
    p.special_teams_intent = SpecialTeamsIntent.KICK_RETURN
    p.special_teams_side = SpecialTeamsSide.RETURNING

    action = p._kicking(c, 10.0)

    assert c.taps == []
    assert len(p.queue) == 0
    assert p.kick_armed is False
    assert p.special_return_holds == 1
    assert "wait for CPU kick" in action


def test_kick_and_punt_returns_override_legacy_kicking_to_defense_assumption():
    for intent in (SpecialTeamsIntent.KICK_RETURN, SpecialTeamsIntent.PUNT_RETURN):
        p = profile()
        p.phase = MaddenPhase.KICKING
        p.phase_since = 1.0
        p.special_teams_intent = intent
        p.special_teams_side = SpecialTeamsSide.RETURNING

        p._transition_phase(MaddenPhase.LIVE, 2.0)

        assert p.possession == Possession.OFFENSE
        assert p.possession_confidence >= 0.95
        assert p.planned_play == PlayIntent.RUN
        assert p.special_return_active is True
        assert p.special_teams_handoffs == 1


def test_kickoff_and_punt_coverage_explicitly_handoff_to_defense():
    for intent in (SpecialTeamsIntent.KICKOFF, SpecialTeamsIntent.PUNT):
        p = profile()
        p.phase = MaddenPhase.KICKING
        p.phase_since = 1.0
        p.special_teams_intent = intent
        p.special_teams_side = SpecialTeamsSide.KICKING

        p._transition_phase(MaddenPhase.LIVE, 2.0)

        assert p.possession == Possession.DEFENSE
        assert p.possession_confidence >= 0.95
        assert p.special_return_active is False
        assert p.special_teams_handoffs == 1


def test_scoring_kick_live_transition_drops_invented_possession_confidence():
    for intent in (SpecialTeamsIntent.FIELD_GOAL, SpecialTeamsIntent.EXTRA_POINT):
        p = profile()
        p.phase = MaddenPhase.KICKING
        p.phase_since = 1.0
        p.special_teams_intent = intent
        p.special_teams_side = SpecialTeamsSide.KICKING

        p._transition_phase(MaddenPhase.LIVE, 2.0)

        assert p.possession == Possession.UNKNOWN
        assert p.possession_confidence == 0.0
        assert p.special_teams_scoring_ambiguities == 1


def test_return_policy_is_run_only_and_never_emits_receiver_buttons():
    p = profile()
    c = RecordingController()
    p.phase = MaddenPhase.LIVE
    p.play_started_at = 0.0
    p.next_action_at = 0.0
    p.special_teams_intent = SpecialTeamsIntent.KICK_RETURN
    p.special_teams_side = SpecialTeamsSide.RETURNING
    p.special_return_active = True
    p.last_spatial = SpatialSnapshot(False, reason="test fallback")
    p.last_spatial_at = -1e9

    action = p._offense_live(c, live_observation(), 2.0)

    assert c.taps == ["cross"]
    assert not ({"circle", "square", "l1", "r1", "triangle"} & set(c.taps))
    assert c.left[1] == 1.0
    assert p.special_return_sprints == 1
    assert "run north/south" in action


def test_registry_active_madden_preserves_v24_special_teams_contract():
    p = build_profile({"name": "madden2005", "ocr_enabled": False})
    assert isinstance(p, Madden2005V24Profile)
