from __future__ import annotations

import cv2
import mss
import numpy as np

from .window import PCSX2Window


class FrameGrabber:
    def __init__(self, window: PCSX2Window) -> None:
        self.window = window
        self.sct = mss.mss()

    def grab(self) -> np.ndarray:
        rect = self.window.client_rect()
        shot = self.sct.grab(
            {"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height}
        )
        frame = np.asarray(shot, dtype=np.uint8)
        return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
