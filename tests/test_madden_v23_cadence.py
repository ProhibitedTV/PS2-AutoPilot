from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_spatial import SpatialCandidate, SpatialSnapshot
from ps2_autopilot.madden_vision import MaddenObservation, MaddenVisualState
from ps2_autopilot.profiles.madden2005_v23 import Madden2005V23Profile
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


def profile():
    return Madden2005V23Profile(
        {
            "ocr_enabled": False,
            "random_seed": 7,
            "spatial_target_confidence": 0.50,
            "spatial_controlled_confidence": 0.48,
            "defense_contact_distance": 0.34,
            "defense_uncertain_action_delay_seconds": 0.62,
        }
    )


def observation():
    return MaddenObservation(
        MaddenVisualState.LIVE_PLAY,
        green_ratio=0.50,
        field_center_x=0.10,
        motion_center_x=-0.20,
        brightness=0.50,
        motion=0.10,
        template_name=None,
        template_score=None,
    )


def candidate(x=0.0, y=0.0, confidence=0.90):
    return SpatialCandidate(
        track_id=1,
        x=x,
        y=y,
        confidence=confidence,
        area=50.0,
        motion=0.5,
        marker=0.8,
    )


def set_spatial(p, *, now, target_confidence, target_x=0.0, target_y=0.0, controlled=None):
    p.last_spatial = SpatialSnapshot(
        True,
        players=() if controlled is None else (controlled,),
        controlled=controlled,
        target_x=target_x,
        target_y=target_y,
        target_confidence=target_confidence,
        reason="test",
    )
    p.last_spatial_at = now


def test_weak_target_confidence_uses_only_switch_then_sprint_not_contact_moves():
    p = profile()
    c = RecordingController()
    p.play_started_at = 0.0
    p.defense_switched = True
    p.next_action_at = 0.0
    set_spatial(
        p,
        now=2.0,
        target_confidence=0.20,
        controlled=candidate(confidence=0.90),
    )

    action = p._defense_live(c, observation(), 2.0)

    assert c.taps == ["circle"]
    assert not ({"square", "triangle", "r2", "l1", "r1"} & set(c.taps))
    assert "sprint" in action
    assert p.defense_cadence_mode == "uncertain-pursuit"
    assert p.defense_contact_suppressed_ticks == 1
    assert p.defense_uncertain_sprints == 1


def test_unverified_control_marker_also_blocks_contact_rng():
    p = profile()
    c = RecordingController()
    p.play_started_at = 0.0
    p.defense_switched = True
    p.next_action_at = 0.0
    set_spatial(
        p,
        now=2.0,
        target_confidence=0.90,
        target_x=0.1,
        target_y=0.1,
        controlled=candidate(confidence=0.20),
    )

    p._defense_live(c, observation(), 2.0)

    assert c.taps == ["circle"]
    assert p.defense_cadence_mode == "uncertain-pursuit"
    assert "controlled confidence" in p.defense_cadence_reason


def test_verified_far_geometry_keeps_existing_v18_contact_hold():
    p = profile()
    c = RecordingController()
    p.play_started_at = 0.0
    p.defense_switched = True
    p.next_action_at = 0.0
    set_spatial(
        p,
        now=2.0,
        target_confidence=0.90,
        target_x=0.80,
        target_y=0.0,
        controlled=candidate(x=0.0, y=0.0, confidence=0.90),
    )

    p._defense_live(c, observation(), 2.0)

    assert p.defense_cadence_mode == "far-pursuit"
    assert p.defense_action_holds == 1
    assert not ({"square", "triangle", "r2", "l1", "r1"} & set(c.taps))


def test_verified_near_geometry_authorizes_parent_contact_policy():
    p = profile()
    c = RecordingController()
    p.play_started_at = 0.0
    p.defense_switched = True
    p.next_action_at = 0.0
    set_spatial(
        p,
        now=3.0,
        target_confidence=0.90,
        target_x=0.10,
        target_y=0.05,
        controlled=candidate(x=0.0, y=0.0, confidence=0.90),
    )

    p._defense_live(c, observation(), 3.0)

    assert p.defense_cadence_mode == "contact-authorized"
    assert p.defense_contact_authorized_ticks == 1


def test_registry_promotes_madden_to_v23():
    p = build_profile({"name": "madden2005", "ocr_enabled": False})
    assert isinstance(p, Madden2005V23Profile)
