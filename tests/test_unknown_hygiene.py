from __future__ import annotations

import numpy as np

from ps2_autopilot.madden_runtime_hygiene import (
    ContextAwareMaddenRuntimeMonitor,
    semantic_context,
)


def test_pick_a_play_is_known_playcall_context():
    state = {
        "phase": "transition",
        "menu_screen": "unknown",
        "game_state": "transition",
        "field_green": 0.162,
        "ocr_text": "OFFENSEPICKAPLAY | STRONGI-NORMAL | ASKMADDEN",
    }
    assert semantic_context(state) == "playcall"


def test_end_of_quarter_is_known_presentation_context():
    state = {
        "phase": "transition",
        "menu_screen": "unknown",
        "game_state": "transition",
        "field_green": 0.053,
        "ocr_text": "NEWYORK | 52 | JETS | BUFFALO | BILLS | ENDOF3RDQUAR",
    }
    assert semantic_context(state) == "presentation"


def test_live_green_field_is_known_field_context():
    state = {
        "phase": "transition",
        "menu_screen": "unknown",
        "game_state": "live_play",
        "field_green": 0.871,
        "ocr_text": "70 | 58",
    }
    assert semantic_context(state) == "field"


def test_genuine_unknown_navigation_stays_unknown():
    state = {
        "phase": "transition",
        "menu_screen": "unknown",
        "game_state": "transition",
        "field_green": 0.02,
        "ocr_text": "EA SPORTS",
    }
    assert semantic_context(state) is None


def test_monitor_does_not_capture_known_presentation_as_unknown(tmp_path):
    monitor = ContextAwareMaddenRuntimeMonitor(
        {
            "unknown_capture_seconds": 0.0,
            "unknown_capture_cooldown_seconds": 0.0,
            "menu_progress_timeout_seconds": 999.0,
        },
        runtime_root=tmp_path,
    )
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    telemetry = {
        "phase": "transition",
        "menu_screen": "unknown",
        "game_state": "transition",
        "field_green": 0.05,
        "ocr_text": "NEW YORK JETS 52 BUFFALO BILLS 6 END OF 3RD QUARTER",
    }

    monitor.update(frame, telemetry, "hold", 10.0)
    monitor.update(frame, telemetry, "hold", 11.0)

    assert monitor.unknown_captures == 0
    assert monitor.unknown_since is None


def test_monitor_still_captures_a_real_unknown_navigation_screen(tmp_path):
    monitor = ContextAwareMaddenRuntimeMonitor(
        {
            "unknown_capture_seconds": 0.0,
            "unknown_capture_cooldown_seconds": 0.0,
            "menu_progress_timeout_seconds": 999.0,
        },
        runtime_root=tmp_path,
    )
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    telemetry = {
        "phase": "transition",
        "menu_screen": "unknown",
        "game_state": "transition",
        "field_green": 0.01,
        "ocr_text": "EA SPORTS",
    }

    monitor.update(frame, telemetry, "hold", 20.0)
    monitor.update(frame, telemetry, "hold", 21.0)

    assert monitor.unknown_captures == 1
