import cv2
import numpy as np

from ps2_autopilot.jak_objectives import GeyserObjective
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v10 import GameplayCue
from ps2_autopilot.profiles.jak_and_daxter_v15 import (
    JakAndDaxterV15Profile,
    LedgeCue,
    VisualGoal,
)
from ps2_autopilot.profiles.registry import build_profile


class FakeController:
    def __init__(self):
        self.left = []
        self.right = []
        self.taps = []
        self.release_all_count = 0

    def tap(self, action, duration=0.08):
        self.taps.append((action, duration))

    def hold(self, action):
        pass

    def release(self, action):
        pass

    def release_all(self):
        self.release_all_count += 1

    def set_left_stick(self, x, y):
        self.left.append((x, y))

    def set_right_stick(self, x, y):
        self.right.append((x, y))

    def neutral_sticks(self):
        self.set_left_stick(0.0, 0.0)
        self.set_right_stick(0.0, 0.0)


def profile(**extra):
    cfg = {
        "mode": "production",
        "ocr_enabled": False,
        "progress_probe_initial_delay_seconds": 999.0,
        "goal_stable_frames_required": 2,
        "ledge_stable_frames_required": 2,
    }
    cfg.update(extra)
    return JakAndDaxterV15Profile(cfg)


def ctx(now=1.0, frame=None, motion=0.02):
    if frame is None:
        frame = np.zeros((240, 360, 3), dtype=np.uint8)
    return ProfileContext(
        frame=frame,
        previous_frame=frame.copy(),
        motion=motion,
        template=None,
        now=now,
    )


def orb_frame():
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    cv2.ellipse(frame, (180, 145), (8, 12), 0, 0, 360, (0, 210, 255), -1)
    return frame


def cell_frame():
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    cv2.ellipse(frame, (180, 135), (18, 25), 0, 0, 360, (245, 245, 245), -1)
    cv2.ellipse(frame, (180, 135), (12, 19), 0, 0, 360, (20, 225, 245), -1)
    return frame


def ledge_frame():
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    # Bright/open terrain above a strong horizontal step boundary, darker face below.
    frame[70:150, 100:260] = (165, 165, 165)
    frame[150:220, 100:260] = (35, 35, 35)
    return frame


def test_orb_detector_creates_centered_reward_cue():
    p = profile()
    cue = p._detect_orb(orb_frame())
    assert cue.kind == "orb"
    assert cue.confidence >= p.orb_cue_min_confidence
    assert abs(cue.x) < 0.1


def test_power_cell_is_higher_priority_than_orb_in_first_cell_objective():
    p = profile()
    assert p.objective.stage == GeyserObjective.FIRST_CELL
    cell = p._score_goal(p._detect_power_cell(cell_frame()))
    orb = p._score_goal(p._detect_orb(orb_frame()))
    assert cell.kind == "power_cell"
    assert cell.score > orb.score
    assert cell.score >= p.goal_min_score


def test_scout_box_remains_positive_progress_even_before_scout_stage():
    p = profile()
    assert p.objective.stage == GeyserObjective.FIRST_CELL
    goal = p._score_goal(GameplayCue("scout_box", 0.1, 0.65, 0.01, 0.75))
    assert goal.score >= p.goal_min_score
    assert p._goal_kind_weight("scout_box") > 0.0


def test_visual_goal_blocks_repetitive_objective_stall_scan():
    p = profile()
    p.objective.replan_due = True
    p.visual_goal = VisualGoal("orb", 0.0, 0.62, 0.002, 0.8, 1.2)
    p.visual_goal_stable_frames = p.goal_stable_frames_required
    p.next_objective_replan_scan_at = 0.0
    assert p._objective_replan_due(ctx(100.0)) is False


def test_route_scoring_prefers_collectible_breadcrumb():
    p = profile()
    blank = np.zeros((240, 360, 3), dtype=np.uint8)
    assert p._land_openness_score(orb_frame()) > p._land_openness_score(blank)


def test_horizontal_step_produces_actionable_ledge_cue_after_stability():
    p = profile(ledge_confidence_min=0.35)
    frame = ledge_frame()
    cue = p._ledge_from_frame(frame)
    assert cue.confidence >= p.ledge_confidence_min
    assert cue.row_coverage >= p.ledge_row_min
    p._refresh_ledge_cue(ctx(1.0, frame))
    p._refresh_ledge_cue(ctx(1.2, frame))
    assert p.ledge_stable_frames >= p.ledge_stable_frames_required
    assert p._ledge_actionable(ctx(1.2, frame)) is True


def test_ledge_skill_jumps_forward_and_can_double_jump():
    p = profile(ledge_double_confidence=0.55)
    c = FakeController()
    p.ledge_cue = LedgeCue(confidence=0.80, y=0.62, row_coverage=0.4, open_above=0.8)
    p.ledge_stable_frames = 3
    action = p._start_ledge_jump(c, ctx(1.0))
    assert "ledge-hop launch" in action
    assert any(name == "cross" for name, _duration in c.taps)
    assert c.left[-1][1] > 0.0
    assert p.ledge_jump_double is True

    action = p._service_v15_ledge_jump(c, ctx(1.3, motion=0.03))
    assert "second jump" in action
    assert sum(1 for name, _duration in c.taps if name == "cross") == 2


def test_registry_promotes_jak_to_v15():
    p = build_profile({"name": "jak_and_daxter", "ocr_enabled": False})
    assert isinstance(p, JakAndDaxterV15Profile)
