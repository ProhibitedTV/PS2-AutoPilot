import time

import cv2
import numpy as np

from ps2_autopilot.madden_menu import (
    MaddenMenuNavigator,
    MaddenScreen,
    MenuAssessment,
    MenuHighlight,
    detect_menu_highlight,
)
from ps2_autopilot.madden_ocr import OCRLine, OCRSnapshot
from ps2_autopilot.madden_runtime import MaddenRuntimeMonitor
from ps2_autopilot.profiles.madden2005_v5 import Madden2005V5Profile


class FakeController:
    def __init__(self):
        self.events = []

    def tap(self, action, duration=0.08):
        self.events.append(("tap", action))

    def neutral_sticks(self):
        self.events.append(("neutral", None))


def snapshot(*items):
    lines = tuple(
        OCRLine(text=text, confidence=0.96, x=0.5, y=y, width=0.28, height=0.055)
        for text, y in items
    )
    return OCRSnapshot(lines, " | ".join(line.text for line in lines), True)


def test_highlight_detection_and_play_now_direction():
    frame = np.full((720, 1280, 3), 35, dtype=np.uint8)
    snap = snapshot(("PLAY NOW", 0.30), ("GAME MODES", 0.45), ("MY MADDEN", 0.60))
    y0, y1 = int(720 * 0.415), int(720 * 0.485)
    cv2.rectangle(frame, (60, y0), (1220, y1), (20, 20, 210), thickness=-1)

    highlight = detect_menu_highlight(frame, snap)
    assert highlight is not None
    assert "GAME MODES" in highlight.text

    nav = MaddenMenuNavigator()
    controller = FakeController()
    action = nav.act(
        controller,
        MenuAssessment(MaddenScreen.MAIN_MENU, 0.96, "test"),
        now=10.0,
        snapshot=snap,
        highlight=highlight,
    )
    assert ("tap", "up") in controller.events
    assert "PLAY NOW" in action


def test_confirm_action_is_verified_and_retried():
    nav = MaddenMenuNavigator()
    controller = FakeController()
    title = MenuAssessment(MaddenScreen.TITLE, 0.99, "test")

    nav.act(controller, title, now=1.0)
    assert nav.pending is not None
    assert ("tap", "start") in controller.events

    nav.act(controller, title, now=5.0)
    assert nav.pending is not None
    assert nav.pending.retries == 1
    assert nav.transaction_retries == 1


def test_verified_destination_clears_transaction():
    nav = MaddenMenuNavigator()
    controller = FakeController()
    nav.act(controller, MenuAssessment(MaddenScreen.TITLE, 0.99, "test"), now=1.0)
    nav.act(
        controller,
        MenuAssessment(MaddenScreen.MAIN_MENU, 0.96, "test"),
        now=2.5,
        snapshot=snapshot(("PLAY NOW", 0.30)),
    )
    assert nav.verified_transitions == 1


def test_pause_ocr_cluster_is_recognized_without_header():
    assert Madden2005V5Profile.looks_like_pause_text(
        "RESUME GAME | INSTANT REPLAY | GAME STATS | SETTINGS | QUIT/SAVE"
    )
    assert Madden2005V5Profile.looks_like_pause_text("RESUME | GAME STATS")
    assert not Madden2005V5Profile.looks_like_pause_text("GAME SETTINGS")


def test_pause_recovery_tries_start_three_times_then_seeks_resume():
    profile = Madden2005V5Profile({"ocr_enabled": False})
    controller = FakeController()
    profile.next_action_at = 0.0

    for now in (2.0, 4.0, 6.0):
        profile.next_action_at = 0.0
        profile._paused(controller, now=now)
    assert profile.pause_start_attempts == 3
    assert [(kind, action) for kind, action in controller.events if kind == "tap"].count(
        ("tap", "start")
    ) == 3

    profile.last_ocr = snapshot(("RESUME GAME", 0.18), ("QUIT/SAVE", 0.82))
    profile.menu_highlight = MenuHighlight("QUIT/SAVE", 0.82, 0.8, 0.8)
    profile.next_action_at = 0.0
    action = profile._paused(controller, now=8.0)
    assert ("tap", "up") in controller.events
    assert "toward RESUME" in action


def test_pause_cross_only_when_resume_is_verified_highlight():
    profile = Madden2005V5Profile({"ocr_enabled": False})
    controller = FakeController()
    profile.pause_start_attempts = 3
    profile.last_ocr = snapshot(("RESUME GAME", 0.18), ("QUIT/SAVE", 0.82))
    profile.menu_highlight = MenuHighlight("RESUME GAME", 0.18, 0.8, 0.8)
    profile.next_action_at = 0.0

    action = profile._paused(controller, now=8.0)
    assert ("tap", "cross") in controller.events
    assert "verified RESUME GAME" in action


def test_pause_never_blindly_confirms_quit_save():
    profile = Madden2005V5Profile({"ocr_enabled": False})
    controller = FakeController()
    profile.pause_start_attempts = 3
    profile.last_ocr = snapshot(("RESUME GAME", 0.18), ("QUIT/SAVE", 0.82))
    profile.menu_highlight = MenuHighlight("QUIT/SAVE", 0.82, 0.8, 0.8)
    profile.next_action_at = 0.0

    profile._paused(controller, now=8.0)
    assert ("tap", "cross") not in controller.events


def test_unknown_state_is_captured(tmp_path):
    monitor = MaddenRuntimeMonitor(
        {
            "unknown_capture_seconds": 0.0,
            "unknown_capture_cooldown_seconds": 0.0,
            "menu_progress_timeout_seconds": 100.0,
        },
        runtime_root=tmp_path,
    )
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    telemetry = {
        "phase": "menu",
        "menu_screen": "unknown",
        "menu_confidence": 0.3,
        "menu_reason": "test",
        "ocr_text": "MYSTERY SCREEN",
        "plays_started": 0,
        "plays_completed": 0,
    }
    now = time.monotonic()
    monitor.update(frame, telemetry, "menu: observing", now)
    assert list((tmp_path / "unknown").glob("*.png"))
    assert list((tmp_path / "unknown").glob("*.json"))


def test_semantic_progress_watchdog_requests_recovery(tmp_path):
    monitor = MaddenRuntimeMonitor(
        {
            "menu_progress_timeout_seconds": 0.1,
            "progress_recovery_cooldown_seconds": 0.1,
            "unknown_capture_seconds": 999,
        },
        runtime_root=tmp_path,
    )
    telemetry = {
        "phase": "menu",
        "menu_screen": "main_menu",
        "plays_started": 0,
        "plays_completed": 0,
    }
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    now = time.monotonic()
    assert monitor.update(frame, telemetry, "menu: observing", now) is None
    directive = monitor.update(frame, telemetry, "menu: observing", now + 0.2)
    assert directive is not None
    assert directive.level == 1


def test_session_counts_game_start_and_final(tmp_path):
    monitor = MaddenRuntimeMonitor({}, runtime_root=tmp_path)
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    now = time.monotonic()
    monitor.update(
        frame,
        {"phase": "playcall", "menu_screen": "playcall", "plays_started": 0, "plays_completed": 0},
        "playcall",
        now,
    )
    assert monitor.games_started == 1
    monitor.update(
        frame,
        {"phase": "game_over", "menu_screen": "final", "plays_started": 20, "plays_completed": 20},
        "advance final",
        now + 1,
    )
    assert monitor.games_completed == 1
