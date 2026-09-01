from __future__ import annotations

import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.nfs_hp2_vision import RoadObservation
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.nfs_hot_pursuit_2 import NfsPhase
from ps2_autopilot.profiles.nfs_hot_pursuit_2_v2 import NfsScreen
from ps2_autopilot.profiles.nfs_hot_pursuit_2_v3 import NfsHotPursuit2V3Profile
from ps2_autopilot.vision import TemplateMatch


class FakeController(Controller):
    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.left_stick = (0.0, 0.0)

    def tap(self, action: str, duration: float = 0.08) -> None:
        self.events.append(("tap", action, duration))

    def hold(self, action: str) -> None:
        self.events.append(("hold", action))

    def release(self, action: str) -> None:
        self.events.append(("release", action))

    def release_all(self) -> None:
        self.events.append(("release_all",))

    def set_left_stick(self, x: float, y: float) -> None:
        self.left_stick = (x, y)
        self.events.append(("left_stick", x, y))

    def set_right_stick(self, x: float, y: float) -> None:
        self.events.append(("right_stick", x, y))


def _road_frame(*, curve_right: bool = False) -> np.ndarray:
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    frame[:] = (45, 125, 45)
    if curve_right:
        polygon = np.array([[345, 155], [465, 155], [610, 350], [25, 350]], dtype=np.int32)
    else:
        polygon = np.array([[250, 155], [390, 155], [610, 350], [25, 350]], dtype=np.int32)
    cv2.fillConvexPoly(frame, polygon, (112, 112, 112))
    cv2.polylines(frame, [polygon], True, (195, 195, 195), 4)
    return frame


def _flat() -> np.ndarray:
    return np.full((360, 640, 3), 90, dtype=np.uint8)


def _ctx(
    label: str | None,
    *,
    now: float,
    frame: np.ndarray | None = None,
    motion: float = 0.0,
) -> ProfileContext:
    template = None if label is None else TemplateMatch(label, 0.96)
    return ProfileContext(
        frame=_flat() if frame is None else frame,
        motion=motion,
        template=template,
        now=now,
    )


def _taps(controller: FakeController) -> list[str]:
    return [event[1] for event in controller.events if event and event[0] == "tap"]


def test_selected_menu_row_requires_stable_evidence_before_input():
    profile = NfsHotPursuit2V3Profile({"menu_stability_frames": 2})
    controller = FakeController()

    first = profile.tick(controller, _ctx("nfs_main_menu_hot_pursuit_selected", now=10.0))
    assert "fail-closed" in first
    assert not _taps(controller)
    assert profile.raw_screen is NfsScreen.MAIN_HOT_PURSUIT

    controller.events.clear()
    second = profile.tick(controller, _ctx("nfs_main_menu_hot_pursuit_selected", now=10.2))
    assert "tap down" in second
    assert _taps(controller) == ["down"]


def test_menu_transaction_does_not_spam_same_selected_row():
    profile = NfsHotPursuit2V3Profile(
        {
            "menu_stability_frames": 1,
            "menu_progress_timeout_seconds": 1.0,
            "menu_max_retries": 1,
            "menu_action_seconds": 0.1,
        }
    )
    controller = FakeController()

    first = profile.tick(controller, _ctx("nfs_main_menu_hot_pursuit_selected", now=20.0))
    assert "tap down" in first
    assert _taps(controller) == ["down"]

    controller.events.clear()
    waiting = profile.tick(controller, _ctx("nfs_main_menu_hot_pursuit_selected", now=20.5))
    assert "transaction waiting" in waiting
    assert not _taps(controller)

    controller.events.clear()
    retry = profile.tick(controller, _ctx("nfs_main_menu_hot_pursuit_selected", now=21.1))
    assert "bounded retry" in retry
    assert _taps(controller) == ["down"]

    controller.events.clear()
    stalled = profile.tick(controller, _ctx("nfs_main_menu_hot_pursuit_selected", now=22.2))
    assert "stalled" in stalled
    assert not _taps(controller)
    assert profile.blocked_menu_screen is NfsScreen.MAIN_HOT_PURSUIT

    controller.events.clear()
    progress = profile.tick(controller, _ctx("nfs_main_menu_world_racing_selected", now=22.4))
    assert "tap confirm" in progress
    assert _taps(controller) == ["confirm"]
    assert profile.blocked_menu_screen is None


def test_ambiguous_frame_never_authorizes_menu_retry():
    profile = NfsHotPursuit2V3Profile(
        {
            "menu_stability_frames": 1,
            "menu_progress_timeout_seconds": 1.0,
            "menu_action_seconds": 0.1,
        }
    )
    controller = FakeController()
    profile.tick(controller, _ctx("nfs_main_menu_hot_pursuit_selected", now=30.0))

    controller.events.clear()
    action = profile.tick(controller, _ctx(None, now=31.2))
    assert "positive destination evidence" in action
    assert not _taps(controller)
    assert profile.menu_retry_actions == 0


def test_replay_exit_is_one_shot_until_positive_screen_change():
    profile = NfsHotPursuit2V3Profile(
        {
            "replay_hold_seconds": 1.0,
            "menu_action_seconds": 0.1,
            "menu_stability_frames": 1,
        }
    )
    controller = FakeController()

    assert profile.tick(controller, _ctx("nfs_replay", now=40.0)) == "replay: preserve broadcast"
    controller.events.clear()

    sent = profile.tick(controller, _ctx("nfs_replay", now=41.1))
    assert "tap start" in sent
    assert _taps(controller) == ["start"]

    controller.events.clear()
    held = profile.tick(controller, _ctx("nfs_replay", now=42.5))
    assert "exit sent" in held
    assert not _taps(controller)

    # Positive main-menu evidence resets the one-shot guard.
    profile.tick(controller, _ctx("nfs_main_menu_world_racing_selected", now=43.0))
    assert not profile.replay_exit_sent


def test_results_confirm_is_one_shot_while_results_template_persists():
    profile = NfsHotPursuit2V3Profile(
        {"results_hold_seconds": 1.0, "menu_action_seconds": 0.1}
    )
    controller = FakeController()

    profile.tick(controller, _ctx("nfs_results", now=50.0))
    controller.events.clear()
    sent = profile.tick(controller, _ctx("nfs_results", now=51.1))
    assert "tap confirm" in sent
    assert _taps(controller) == ["confirm"]

    controller.events.clear()
    held = profile.tick(controller, _ctx("nfs_results", now=52.3))
    assert "confirm sent" in held
    assert not _taps(controller)
    assert profile.results_confirm_actions == 1


def test_manual_race_takeover_from_menu_needs_unknown_escape_window():
    profile = NfsHotPursuit2V3Profile(
        {
            "menu_stability_frames": 1,
            "race_enter_frames": 1,
            "race_enter_confidence": 0.30,
            "drive_confidence": 0.20,
            "menu_escape_takeover_seconds": 1.5,
            "corner_brake_threshold": 1.0,
            "corner_coast_threshold": 1.0,
        }
    )
    controller = FakeController()
    profile.phase = NfsPhase.MAIN_MENU

    early = profile.tick(controller, _ctx(None, now=60.0, frame=_road_frame(), motion=0.02))
    assert profile.phase is NfsPhase.MAIN_MENU
    assert "fail-closed" in early

    controller.events.clear()
    late = profile.tick(controller, _ctx(None, now=61.6, frame=_road_frame(), motion=0.02))
    assert profile.phase is NfsPhase.RACING
    assert "racing:" in late


def test_predictive_speed_control_coasts_before_brake_threshold():
    profile = NfsHotPursuit2V3Profile(
        {
            "drive_confidence": 0.20,
            "corner_coast_threshold": 0.45,
            "corner_brake_threshold": 1.0,
            "low_confidence_coast_threshold": 0.20,
            "steering_smoothing": 0.0,
            "steering_prediction_seconds": 0.0,
        }
    )
    controller = FakeController()
    profile.road = RoadObservation(
        confidence=0.85,
        center_x=0.45,
        curvature=0.30,
        width=0.55,
        coverage=0.35,
        center_contact=0.40,
    )

    action = profile._drive(controller, _ctx(None, now=70.0, motion=0.02))
    assert "coast" in action
    assert any(event[:2] == ("release", "cross") for event in controller.events)
    assert not any(event[:2] == ("hold", "cross") for event in controller.events)


def test_single_bad_road_frame_can_use_short_motion_guarded_grace():
    profile = NfsHotPursuit2V3Profile(
        {
            "drive_confidence": 0.30,
            "road_grace_seconds": 0.30,
            "race_enter_confidence": 0.30,
        }
    )

    good_ctx = _ctx(None, now=80.0, frame=_road_frame(), motion=0.02)
    profile._observe_road(good_ctx)
    assert profile.last_good_road.confidence >= profile.drive_confidence

    bad_ctx = _ctx(None, now=80.1, frame=_flat(), motion=0.02)
    profile._observe_road(bad_ctx)
    assert profile.road.confidence >= profile.drive_confidence
    assert profile.road_grace_fills == 1
