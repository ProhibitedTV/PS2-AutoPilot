import cv2
import numpy as np

from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.jak_and_daxter_v10 import GameplayCue
from ps2_autopilot.profiles.jak_and_daxter_v13 import JakAndDaxterV13Profile
from ps2_autopilot.profiles.registry import build_profile


class FakeController:
    def __init__(self):
        self.left = []
        self.right = []
        self.taps = []

    def tap(self, action, duration=0.08):
        self.taps.append((action, duration))

    def hold(self, action):
        pass

    def release(self, action):
        pass

    def release_all(self):
        pass

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
        "water_surface_hue_min": 88,
        "water_surface_hue_max": 118,
        "water_surface_sat_min": 80,
        "water_surface_value_min": 70,
        "water_surface_bottom_fraction": 0.28,
        "water_surface_bottom_min": 0.16,
        "water_surface_candidate_min": 0.055,
        "scout_stable_frames_required": 3,
        "scout_retry_cooldown_seconds": 6.0,
        "land_scan_sweep_seconds": 0.4,
        "land_scan_commit_seconds": 0.8,
    }
    cfg.update(extra)
    return JakAndDaxterV13Profile(cfg)


def context(frame, now=0.0, motion=0.0):
    return ProfileContext(
        frame=frame,
        previous_frame=frame.copy(),
        motion=motion,
        template=None,
        now=now,
    )


def hsv_frame(hue, sat, value, shape=(240, 360)):
    hsv = np.zeros((shape[0], shape[1], 3), dtype=np.uint8)
    hsv[:, :, 0] = hue
    hsv[:, :, 1] = sat
    hsv[:, :, 2] = value
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def test_dark_blue_rock_can_match_legacy_color_without_owning_water_navigation():
    p = profile()
    # Deliberately inside V7's broad hue/saturation/value range, representative of
    # the dry blue-gray cliff failure from the V12 live run.
    wall = hsv_frame(105, 60, 45)

    total, _left, center, _right = p._water_ratios(wall)
    geometry = p._water_geometry(wall)

    assert total > 0.90
    assert center > 0.90
    assert geometry.confirmed is False
    assert p.water_surface_bottom_ratio == 0.0


def test_bright_cyan_surface_touching_bottom_can_own_water_navigation():
    p = profile()
    water = hsv_frame(100, 190, 175)

    geometry = p._water_geometry(water)

    assert geometry.candidate_ratio > 0.90
    assert p.water_surface_bottom_ratio > 0.90
    assert geometry.confirmed is True


def test_distant_water_band_does_not_steal_control_from_dry_foreground():
    p = profile()
    frame = np.zeros((240, 360, 3), dtype=np.uint8)
    cyan = hsv_frame(100, 190, 175)
    # Put a large coherent water-colored band in the upper part of WATER_ROI while
    # leaving the bottom foreground dry. This is the safe "ocean in background" case.
    y0 = int(round(0.54 * frame.shape[0]))
    y1 = int(round(0.72 * frame.shape[0]))
    frame[y0:y1, :] = cyan[y0:y1, :]

    geometry = p._water_geometry(frame)

    assert geometry.candidate_ratio > 0.10
    assert p.water_surface_bottom_ratio < 0.05
    assert geometry.confirmed is False


def test_scout_target_requires_temporal_stability_and_then_cools_down():
    p = profile()
    fixed = GameplayCue("scout_box", 0.08, 0.70, 0.006, 0.72)
    p._detect_scout_box = lambda _frame: fixed
    p._detect_blue_eco = lambda _frame: GameplayCue()
    frame = np.zeros((240, 360, 3), dtype=np.uint8)

    p._refresh_gameplay_cue(context(frame, now=1.0))
    assert p.gameplay_cue.kind == "none"
    p._refresh_gameplay_cue(context(frame, now=1.3))
    assert p.gameplay_cue.kind == "none"
    p._refresh_gameplay_cue(context(frame, now=1.6))
    assert p.gameplay_cue.kind == "scout_box"

    p.skill_active = True
    p.skill_name = "scout_dive"
    p.skill_stage = "slam"
    p._finish_skill(context(frame, now=2.0))
    assert p.scout_retry_cooldown_until == 8.0

    p._refresh_gameplay_cue(context(frame, now=2.3))
    assert p.gameplay_cue.kind == "none"
    assert p.scout_cooldown_suppressions >= 1


def test_land_openness_prefers_bright_ground_and_penalizes_open_water():
    p = profile()
    ground = hsv_frame(55, 150, 150)
    wall = hsv_frame(105, 60, 45)
    water = hsv_frame(100, 190, 175)

    ground_score = p._land_openness_score(ground)
    wall_score = p._land_openness_score(wall)
    water_score = p._land_openness_score(water)

    assert ground_score > wall_score
    assert ground_score > water_score


def test_route_scan_commits_to_more_open_sample_instead_of_random_turn():
    p = profile()
    c = FakeController()
    ground = hsv_frame(55, 150, 150)
    wall = hsv_frame(105, 60, 45)

    action = p._start_land_scan(c, context(ground, now=0.0), reason="test")
    assert "scan left" in action

    # End left sweep on open ground; then end right sweep looking at a dark wall.
    p._service_land_scan(c, context(ground, now=0.5))
    action = p._service_land_scan(c, context(wall, now=1.4))

    assert p.land_scan_stage == "commit"
    assert p.land_scan_choice == -1.0
    assert "choose L corridor" in action
    assert p.land_scan_left_score > p.land_scan_right_score


def test_registry_promotes_jak_to_v13():
    p = build_profile({"name": "jak_and_daxter", "ocr_enabled": False})
    assert isinstance(p, JakAndDaxterV13Profile)
