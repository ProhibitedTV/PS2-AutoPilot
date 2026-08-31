from __future__ import annotations

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.madden_menu import GameSituation
from ps2_autopilot.madden_ocr import OCRLine, OCRSnapshot
from ps2_autopilot.madden_spatial import SpatialSnapshot
from ps2_autopilot.madden_vision import MaddenObservation, MaddenVisualState
from ps2_autopilot.profiles.madden2005 import PlayIntent, Possession
from ps2_autopilot.profiles.madden2005_v26 import Madden2005V26Profile


class RecordingController(Controller):
    def __init__(self) -> None:
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


class FixedRng:
    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value

    def choice(self, values):
        return values[0]


def profile() -> Madden2005V26Profile:
    return Madden2005V26Profile({"ocr_enabled": False, "random_seed": 26})


def observation() -> MaddenObservation:
    return MaddenObservation(
        MaddenVisualState.LIVE_PLAY,
        green_ratio=0.50,
        field_center_x=0.10,
        motion_center_x=0.0,
        brightness=0.50,
        motion=0.10,
        template_name=None,
        template_score=None,
    )


def snapshot(*lines: tuple[str, float, float]) -> OCRSnapshot:
    ocr_lines = tuple(
        OCRLine(text=text, confidence=0.92, x=x, y=y, width=0.18, height=0.04)
        for text, x, y in lines
    )
    return OCRSnapshot(ocr_lines, " | ".join(line.text for line in ocr_lines), True)


def test_third_and_long_is_a_required_pass_not_a_coin_flip():
    p = profile()
    p.situation = GameSituation(down=3, distance=9, quarter=2, clock_seconds=420)

    assert p._choose_offensive_intent() == PlayIntent.PASS
    assert "3rd-and-long" in p.playcall_decision_reason


def test_second_and_short_keeps_run_and_pass_available_for_tendency_breaking():
    p = profile()
    p.rng = FixedRng(0.50)
    p.situation = GameSituation(down=2, distance=2)

    # V26 uses a 42% run baseline here: 0.50 therefore selects pass rather than
    # automatically running just because the offense is ahead of the sticks.
    assert p._choose_offensive_intent() == PlayIntent.PASS
    assert "shot opportunity" in p.playcall_decision_reason


def test_visible_play_cards_are_selected_by_football_intent_and_screen_position():
    p = profile()
    p.last_ocr = snapshot(
        ("HB DIVE", 0.22, 0.58),
        ("SLANTS", 0.50, 0.58),
        ("CURL FLAT", 0.79, 0.58),
    )

    run = p._visible_play_candidates(PlayIntent.RUN)
    passed = p._visible_play_candidates(PlayIntent.PASS)

    assert run[0].label == "HBDIVE"
    assert run[0].button == "square"
    assert passed[0].label in {"SLANTS", "CURLFLAT"}
    assert passed[0].button in {"cross", "circle"}


def test_fourth_down_prefers_visible_special_teams_over_desperation_pass():
    p = profile()
    p.situation = GameSituation(down=4, distance=7, quarter=2, clock_seconds=500)
    p.last_ocr = snapshot(
        ("SLANTS", 0.22, 0.58),
        ("PUNT", 0.50, 0.58),
        ("CURL FLAT", 0.79, 0.58),
    )

    candidates = p._visible_play_candidates(PlayIntent.PASS)

    assert candidates[0].label == "PUNT"
    assert candidates[0].button == "cross"


def test_third_and_long_defense_prefers_coverage_named_card():
    p = profile()
    p.situation = GameSituation(down=3, distance=11)
    p.last_ocr = snapshot(
        ("COVER 3", 0.22, 0.58),
        ("DOG BLITZ", 0.50, 0.58),
        ("COVER 2 MAN", 0.79, 0.58),
    )

    candidates = p._visible_play_candidates(PlayIntent.DEFENSE)

    assert candidates[0].label in {"COVER3", "COVER2MAN"}


def test_run_policy_waits_for_blocks_before_sprint_or_special_move():
    p = profile()
    c = RecordingController()
    p.phase = p.phase.LIVE
    p.play_started_at = 10.0
    p.next_action_at = 0.0
    p.planned_play = PlayIntent.RUN
    p.possession = Possession.OFFENSE
    p.possession_confidence = 0.99
    p.last_spatial = SpatialSnapshot(False, reason="test")
    p.last_spatial_at = -1e9

    action = p._offense_live(c, observation(), 10.30)

    assert c.taps == []
    assert c.left[1] == 0.62
    assert "follow blocks" in action
    assert p.run_patience_holds == 1


def test_run_policy_refuses_random_dive_on_normal_down():
    p = profile()
    p.rng = FixedRng(0.99)
    c = RecordingController()
    p.phase = p.phase.LIVE
    p.play_started_at = 10.0
    p.next_action_at = 0.0
    p.planned_play = PlayIntent.RUN
    p.situation = GameSituation(down=1, distance=10)
    p.last_spatial = SpatialSnapshot(False, reason="test")
    p.last_spatial_at = -1e9

    action = p._run_live(c, observation(), 11.0)

    assert c.taps == ["cross"]
    assert "no low-value dive" in action
    assert p.run_short_yardage_dives == 0


def test_run_policy_allows_dive_when_the_marker_is_actually_close():
    p = profile()
    p.rng = FixedRng(0.99)
    c = RecordingController()
    p.phase = p.phase.LIVE
    p.play_started_at = 10.0
    p.next_action_at = 0.0
    p.planned_play = PlayIntent.RUN
    p.situation = GameSituation(down=3, distance=1)
    p.last_spatial = SpatialSnapshot(False, reason="test")
    p.last_spatial_at = -1e9

    action = p._run_live(c, observation(), 11.0)

    assert c.taps == ["square"]
    assert "short-yardage marker" in action
    assert p.run_short_yardage_dives == 1
