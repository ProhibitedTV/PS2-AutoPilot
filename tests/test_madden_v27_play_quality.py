from __future__ import annotations

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_menu import GameSituation
from ps2_autopilot.madden_spatial import SpatialCandidate, SpatialSnapshot
from ps2_autopilot.madden_vision import MaddenObservation, MaddenVisualState
from ps2_autopilot.profiles.madden2005 import PlayIntent, Possession
from ps2_autopilot.profiles.madden2005_v27 import Madden2005V27Profile


class RecordingController(Controller):
    def __init__(self) -> None:
        self.taps: list[tuple[str, float]] = []
        self.left = (0.0, 0.0)
        self.right = (0.0, 0.0)

    def tap(self, action: str, duration: float = 0.08) -> None:
        self.taps.append((action, duration))

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


class FirstChoiceRng:
    def random(self) -> float:
        return 0.5

    def choice(self, values):
        return values[0]


def profile() -> Madden2005V27Profile:
    p = Madden2005V27Profile({"ocr_enabled": False, "random_seed": 27})
    p.rng = FirstChoiceRng()
    return p


def observation() -> MaddenObservation:
    return MaddenObservation(
        MaddenVisualState.LIVE_PLAY,
        green_ratio=0.50,
        field_center_x=0.08,
        motion_center_x=0.0,
        brightness=0.50,
        motion=0.10,
        template_name=None,
        template_score=None,
    )


def verified_spatial(distance: float) -> SpatialSnapshot:
    controlled = SpatialCandidate(
        track_id=1,
        x=0.0,
        y=0.0,
        confidence=0.95,
        area=70.0,
        motion=0.5,
        marker=0.9,
    )
    target = SpatialCandidate(
        track_id=2,
        x=distance,
        y=0.0,
        confidence=0.90,
        area=70.0,
        motion=0.5,
        marker=0.0,
    )
    return SpatialSnapshot(
        True,
        players=(controlled, target),
        controlled=controlled,
        ball=target,
        target_x=target.x,
        target_y=target.y,
        target_confidence=0.92,
        open_space_x=0.0,
        open_space_confidence=0.0,
        reason="test",
    )


def arm_live_pass(p: Madden2005V27Profile, label: str, started: float = 10.0) -> None:
    p.phase = p.phase.LIVE
    p.play_started_at = started
    p.next_action_at = 0.0
    p.planned_play = PlayIntent.PASS
    p.possession = Possession.OFFENSE
    p.possession_confidence = 0.99
    p.playcall_selected_label = label


def test_pass_concepts_derive_from_selected_play_name():
    p = profile()

    p.playcall_selected_label = "SLANTS"
    assert p._pass_concept() == "quick"

    p.playcall_selected_label = "CURL FLAT"
    assert p._pass_concept() == "quick"

    p.playcall_selected_label = "PA POST"
    assert p._pass_concept() == "shot"

    p.playcall_selected_label = "CURL COMEBACK"
    assert p._pass_concept() == "intermediate"


def test_quick_concept_releases_before_shot_concept():
    quick = profile()
    arm_live_pass(quick, "SLANTS")
    quick.pass_icons_requested = True
    c1 = RecordingController()

    action = quick._pass_live(c1, observation(), 11.02)

    assert quick.pass_thrown is True
    assert c1.taps[0][0] == "cross"
    assert c1.taps[0][1] == quick.pass_bullet_hold_seconds
    assert "quick bullet" in action

    shot = profile()
    arm_live_pass(shot, "FOUR VERTICALS")
    shot.pass_icons_requested = True
    c2 = RecordingController()

    action = shot._pass_live(c2, observation(), 11.02)

    assert shot.pass_thrown is False
    assert c2.taps == []
    assert "scan shot concept" in action


def test_shot_concept_uses_lob_tap_after_routes_develop():
    p = profile()
    arm_live_pass(p, "POST CORNER")
    p.pass_icons_requested = True
    c = RecordingController()

    action = p._pass_live(c, observation(), 11.70)

    assert p.pass_thrown is True
    assert c.taps[0][1] == p.pass_lob_tap_seconds
    assert p.pass_lob_tap_seconds < p.pass_bullet_hold_seconds
    assert "shot lob" in action


def test_pass_receiver_selection_avoids_immediate_repeat():
    p = profile()
    p.last_pass_receiver = "cross"

    selected = p._choose_pass_receiver()

    assert selected == "square"
    assert p.pass_receiver_history == ["square"]


def test_verified_far_defense_only_sprints_to_close_space():
    p = profile()
    c = RecordingController()
    p.phase = p.phase.LIVE
    p.play_started_at = 10.0
    p.next_action_at = 0.0
    p.possession = Possession.DEFENSE
    p.possession_confidence = 0.99
    p.last_spatial = verified_spatial(0.60)
    p.last_spatial_at = 10.0

    action = p._defense_live(c, observation(), 10.10)

    assert c.taps == [("circle", 0.055)]
    assert "close pursuit" in action
    assert p.defense_secure_tackles == 0
    assert p.defense_disciplined_sprints == 1


def test_verified_normal_contact_suppresses_high_risk_gambles():
    p = profile()
    c = RecordingController()
    p.phase = p.phase.LIVE
    p.play_started_at = 10.0
    p.next_action_at = 0.0
    p.situation = GameSituation(down=1, distance=10)
    p.last_spatial = verified_spatial(0.14)
    p.last_spatial_at = 10.0

    action = p._defense_live(c, observation(), 10.10)

    assert c.taps == [("circle", 0.055)]
    assert "suppress gamble" in action
    assert p.defense_high_risk_actions_suppressed == 1
    assert p.defense_secure_tackles == 0


def test_close_third_and_short_allows_secure_dive_tackle():
    p = profile()
    c = RecordingController()
    p.phase = p.phase.LIVE
    p.play_started_at = 10.0
    p.next_action_at = 0.0
    p.situation = GameSituation(down=3, distance=1)
    p.last_spatial = verified_spatial(0.12)
    p.last_spatial_at = 10.0

    action = p._defense_live(c, observation(), 10.10)

    assert c.taps == [("square", 0.055)]
    assert "short-yardage tackle" in action
    assert p.defense_secure_tackles == 1
