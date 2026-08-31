from ps2_autopilot.madden_vision import MaddenObservation, MaddenVisualState
from ps2_autopilot.profiles.madden2005 import MaddenPhase, Possession
from ps2_autopilot.profiles.madden2005_v24 import SpecialTeamsIntent, SpecialTeamsSide
from ps2_autopilot.profiles.madden2005_v31 import Madden2005V31Profile


class FakeController:
    def __init__(self):
        self.events = []

    def neutral_sticks(self):
        self.events.append(("neutral", None))

    def tap(self, action, duration=0.08):
        self.events.append(("tap", action))

    def set_left_stick(self, x, y):
        self.events.append(("left", (x, y)))

    def set_right_stick(self, x, y):
        self.events.append(("right", (x, y)))


def profile() -> Madden2005V31Profile:
    return Madden2005V31Profile({"ocr_enabled": False, "rng_seed": 7})


def field_idle() -> MaddenObservation:
    return MaddenObservation(
        state=MaddenVisualState.FIELD_IDLE,
        green_ratio=0.95,
        field_center_x=0.0,
        motion_center_x=0.0,
        brightness=100.0,
        motion=0.002,
        template_name=None,
        template_score=None,
    )


def test_return_playcall_arms_controller_side_ownership():
    p = profile()
    p.phase = MaddenPhase.PLAYCALL
    p.special_teams_intent = SpecialTeamsIntent.KICK_RETURN
    p.special_teams_side = SpecialTeamsSide.RETURNING
    p.special_teams_confidence = 0.99

    p._arm_special_setup(10.0)

    assert p.special_setup_armed
    assert p.special_setup_intent == SpecialTeamsIntent.KICK_RETURN
    assert p.special_setup_side == SpecialTeamsSide.RETURNING
    assert p.special_setup_arms == 1


def test_bare_kickoff_cannot_flip_previously_proven_return_side():
    p = profile()
    p.special_teams_intent = SpecialTeamsIntent.KICK_RETURN
    p.special_teams_side = SpecialTeamsSide.RETURNING
    p._arm_special_setup(10.0)

    # This reproduces the live failure: after the return play-call closes, OCR
    # simplifies to bare KICKOFF and the inherited classifier calls that KICKING.
    p.special_teams_intent = SpecialTeamsIntent.KICKOFF
    p.special_teams_side = SpecialTeamsSide.KICKING
    p.special_teams_confidence = 0.96
    p.special_teams_reason = "kickoff marker"

    p._restore_armed_owner(11.0)

    assert p.special_teams_intent == SpecialTeamsIntent.KICK_RETURN
    assert p.special_teams_side == SpecialTeamsSide.RETURNING
    assert p.special_teams_confidence == 0.99
    assert p.special_setup_owner_preservations == 1


def test_armed_field_idle_special_team_setup_promotes_pre_snap_to_kicking():
    p = profile()
    p.phase = MaddenPhase.PRE_SNAP
    p.phase_since = 9.0
    p.special_setup_armed = True
    p.special_setup_intent = SpecialTeamsIntent.KICK_RETURN
    p.special_setup_side = SpecialTeamsSide.RETURNING
    p.special_setup_armed_at = 10.0
    p.special_teams_intent = SpecialTeamsIntent.KICK_RETURN
    p.special_teams_side = SpecialTeamsSide.RETURNING

    p._promote_special_setup_phase(field_idle(), 11.0)

    assert p.phase == MaddenPhase.KICKING
    assert p.special_setup_phase_promotions == 1


def test_playcall_phase_is_never_promoted_directly_to_kicking():
    p = profile()
    p.phase = MaddenPhase.PLAYCALL
    p.special_setup_armed = True
    p.special_setup_intent = SpecialTeamsIntent.KICK_RETURN
    p.special_setup_side = SpecialTeamsSide.RETURNING
    p.special_teams_intent = SpecialTeamsIntent.KICK_RETURN
    p.special_teams_side = SpecialTeamsSide.RETURNING

    p._promote_special_setup_phase(field_idle(), 11.0)

    assert p.phase == MaddenPhase.PLAYCALL
    assert p.special_setup_phase_promotions == 0


def test_return_setup_never_touches_kick_meter():
    p = profile()
    c = FakeController()
    p.phase = MaddenPhase.KICKING
    p.special_teams_intent = SpecialTeamsIntent.KICK_RETURN
    p.special_teams_side = SpecialTeamsSide.RETURNING

    action = p._kicking(c, 12.0)

    assert "wait for CPU kick" in action
    assert not any(event == ("tap", "cross") for event in c.events)
    assert p.special_return_holds == 1


def test_kicking_to_live_uses_preserved_return_handoff_then_clears_setup():
    p = profile()
    p.phase = MaddenPhase.KICKING
    p.special_setup_armed = True
    p.special_setup_intent = SpecialTeamsIntent.KICK_RETURN
    p.special_setup_side = SpecialTeamsSide.RETURNING
    p.special_teams_intent = SpecialTeamsIntent.KICK_RETURN
    p.special_teams_side = SpecialTeamsSide.RETURNING

    p._transition_phase(MaddenPhase.LIVE, 13.0)

    assert p.phase == MaddenPhase.LIVE
    assert p.possession == Possession.OFFENSE
    assert p.possession_confidence == 0.99
    assert p.special_return_active
    assert not p.special_setup_armed
    assert p.special_setup_clears == 1
