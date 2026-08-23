import cv2
import numpy as np

from ps2_autopilot.madden_spatial import MaddenSpatialTracker


def field_frame(offset=0, ball_x=None):
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    frame[:] = (42, 118, 42)

    # Two rough teams distributed across the playable area. These are deliberately
    # simple rectangles so the test validates geometry/temporal logic rather than
    # depending on copyrighted game imagery.
    left_team = [(205, 165), (240, 145), (275, 175), (310, 150), (345, 178)]
    right_team = [(220, 215), (255, 205), (290, 225), (325, 205), (360, 220)]
    for x, y in left_team:
        cv2.rectangle(frame, (x + offset, y), (x + 14 + offset, y + 27), (25, 35, 210), -1)
    for x, y in right_team:
        cv2.rectangle(frame, (x + offset, y), (x + 14 + offset, y + 27), (220, 220, 225), -1)

    # Bright yellow marker beneath one candidate to exercise conservative
    # controlled-player evidence.
    cv2.ellipse(frame, (297 + offset, 254), (10, 3), 0, 0, 360, (0, 230, 250), -1)

    if ball_x is not None:
        # Slightly exaggerated synthetic football: the real detector downsamples
        # the field and rejects tiny morphology noise, so the fixture must remain a
        # coherent moving component after that same processing path.
        cv2.circle(frame, (ball_x, 190), 5, (245, 245, 245), -1)
    return frame


def test_spatial_tracker_finds_stable_players_and_open_space():
    tracker = MaddenSpatialTracker(sample_width=480, min_player_confidence=0.24)
    previous = field_frame(offset=0)
    current = field_frame(offset=2)

    first = tracker.observe(current, previous, now=1.0)
    assert first.available
    assert first.player_count >= 6
    assert first.open_space_confidence > 0.20
    assert -1.0 <= first.open_space_x <= 1.0
    assert first.processing_ms >= 0.0

    later = tracker.observe(field_frame(offset=4), current, now=1.2)
    assert later.player_count >= 6
    first_ids = {player.track_id for player in first.players}
    later_ids = {player.track_id for player in later.players}
    assert first_ids & later_ids


def test_spatial_tracker_can_form_moving_ball_hypothesis():
    tracker = MaddenSpatialTracker(sample_width=480, min_player_confidence=0.24)
    previous = field_frame(ball_x=315)
    current = field_frame(ball_x=342)

    snapshot = tracker.observe(current, previous, now=2.0)
    assert snapshot.available
    assert snapshot.ball is not None
    assert snapshot.ball.confidence >= 0.45
    assert snapshot.target_confidence >= 0.45
    assert -1.0 <= snapshot.target_x <= 1.0
    assert -1.0 <= snapshot.target_y <= 1.0


def test_spatial_tracker_fails_conservative_off_field():
    tracker = MaddenSpatialTracker(sample_width=320)
    previous = np.zeros((360, 640, 3), dtype=np.uint8)
    current = previous.copy()
    snapshot = tracker.observe(current, previous, now=3.0)

    assert snapshot.available
    assert snapshot.player_count == 0
    assert snapshot.ball is None
    assert snapshot.target_confidence == 0.0
    assert snapshot.open_space_confidence == 0.0
