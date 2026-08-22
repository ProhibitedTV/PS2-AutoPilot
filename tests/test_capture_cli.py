import numpy as np
import pytest

from ps2_autopilot.capture_cli import crop_roi, parse_roi


def test_parse_and_crop_roi():
    roi = parse_roi("0.25,0.25,0.5,0.5")
    frame = np.zeros((100, 200, 3), dtype=np.uint8)
    cropped = crop_roi(frame, roi)
    assert cropped.shape == (50, 100, 3)


def test_invalid_roi_is_rejected():
    with pytest.raises(Exception):
        parse_roi("0.8,0.8,0.4,0.4")
