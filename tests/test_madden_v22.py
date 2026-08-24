import cv2
import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.profiles.madden2005_v22 import Madden2005V22Profile


class RecordingController(Controller):
    def __init__(self) -> None:
        self.taps: list[str] = []

    def tap(self, action: str, duration: float = 0.08) -> None:
        del duration
        self.taps.append(action)

    def hold(self, action: str) -> None:
        del action

    def release(self, action: str) -> None:
        del action

    def release_all(self) -> None:
        pass


def profile(**overrides):
    cfg = {
        "ocr_enabled": False,
        "random_seed": 22,
    }
    cfg.update(overrides)
    return Madden2005V22Profile(cfg)


def frame_with_red_bar(center_y: float) -> np.ndarray:
    frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    cy = int(center_y * frame.shape[0])
    cv2.rectangle(frame, (820, cy - 27), (1130, cy + 27), (0, 0, 190), -1)
    return frame


def test_captured_ea_bio_ocr_is_recognized_as_distinct_modal():
    text = (
        "MADDEN | NOEASPORTSRMBIOFOUNDONTHEMEMORYCARD | "
        "PS2 INMEMORYCARDSLOT1.INORDERTOUSEEA | "
        "EXISTINGEASPORTSRMBIOORSAVEONENOW | RETRY | SAVE"
    )
    assert Madden2005V22Profile._looks_like_ea_bio_modal(text)
    assert not Madden2005V22Profile._looks_like_ea_bio_modal(
        "SAVE YOUR PROFILE | MEMORY CARD SLOT 1 | CONTINUE WITHOUT SAVING"
    )


def test_red_bar_detector_distinguishes_retry_save_and_cancel_rows():
    assert Madden2005V22Profile._detect_ea_bio_highlight(frame_with_red_bar(0.545)) == "retry"
    assert Madden2005V22Profile._detect_ea_bio_highlight(frame_with_red_bar(0.608)) == "save"
    assert Madden2005V22Profile._detect_ea_bio_highlight(frame_with_red_bar(0.674)) == "cancel"


def test_save_row_can_only_move_toward_cancel_not_confirm():
    p = profile()
    controller = RecordingController()
    p.ea_bio_modal_visible = True
    p.ea_bio_selected_row = "save"
    p.next_action_at = 0.0

    action = p._ea_bio_cancel(controller, 10.0)

    assert controller.taps == ["down"]
    assert "DOWN toward CANCEL" in action
    assert p.ea_bio_cancel_confirms == 0


def test_cross_is_only_sent_after_cancel_is_visually_verified():
    p = profile()
    controller = RecordingController()
    p.ea_bio_modal_visible = True
    p.ea_bio_selected_row = "cancel"
    p.next_action_at = 0.0

    action = p._ea_bio_cancel(controller, 10.0)

    assert controller.taps == ["cross"]
    assert "verified CANCEL" in action
    assert p.ea_bio_cancel_confirms == 1


def test_unverified_highlight_uses_one_probe_and_keeps_cross_locked():
    p = profile()
    controller = RecordingController()
    p.ea_bio_modal_visible = True
    p.ea_bio_selected_row = None
    p.ea_bio_first_seen_at = 0.0
    p.next_action_at = 0.0

    first = p._ea_bio_cancel(controller, 2.0)
    p.next_action_at = 0.0
    second = p._ea_bio_cancel(controller, 3.0)

    assert controller.taps == ["down"]
    assert "Cross locked" in first
    assert "Cross locked" in second
