from __future__ import annotations

import numpy as np

from .guitar_hero_vision import LANE_NAMES
from .guitar_hero_vision_v10 import GuitarHeroVisionV10


class GuitarHeroVisionV11(GuitarHeroVisionV10):
    """V11 performance layer for the Guitar Hero highway tracker.

    The V9 live bundle showed the runtime spending roughly 45-50 ms in policy code
    while targeting a 16.7 ms loop.  V8/V10 recompute a combinatorial five-lane
    receptor layout every frame even after a stable highway has already been locked,
    and they continue running menu morphology while actively playing.

    V11 keeps the same geometry and temporal note semantics but adds two safe fast
    paths:

    * once a receptor layout is locked, validate it with tiny local color windows at
      the known receptor centers instead of re-running the full candidate product;
    * allow the active profile to suppress menu scoring during song launch/gameplay.

    Any weak local support falls straight back to V8's full reacquisition path, so the
    optimization cannot keep stale highway ownership across a results/menu screen.
    """

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.fast_lock_min_ratio = float(cfg.get("fast_lock_min_ratio", 0.035))
        self.fast_lock_half_width = float(cfg.get("fast_lock_half_width", 0.022))
        self.fast_lock_half_height = float(cfg.get("fast_lock_half_height", 0.026))
        self.skip_menu_scores = False
        self.fast_layout_frames = 0
        self.full_layout_frames = 0
        self.menu_score_frames_skipped = 0
        self._fast_layout_used = False

    def _locked_pixel_support(self, masks: dict[str, np.ndarray]) -> int:
        centers = self._locked_centers
        if centers is None:
            return 0
        support = 0
        for lane_name, center in zip(LANE_NAMES, centers, strict=True):
            mask = masks[lane_name]
            h, w = mask.shape
            cx = int(center[0] * w)
            cy = int(center[1] * h)
            xhalf = max(4, int(w * self.fast_lock_half_width))
            yhalf = max(4, int(h * self.fast_lock_half_height))
            x0 = max(0, cx - xhalf)
            x1 = min(w, cx + xhalf + 1)
            y0 = max(0, cy - yhalf)
            y1 = min(h, cy + yhalf + 1)
            roi = mask[y0:y1, x0:x1]
            if roi.size == 0:
                continue
            ratio = float(np.count_nonzero(roi) / roi.size)
            if ratio >= self.fast_lock_min_ratio:
                support += 1
        return support

    def _joint_layout(self, masks):
        if self._locked_centers is not None:
            support = self._locked_pixel_support(masks)
            if support >= 3:
                self._fast_layout_used = True
                self.layout_locked = True
                self.lock_support = support
                self.fast_layout_frames += 1
                confidence = min(1.0, 0.64 + support * 0.07)
                return self._locked_centers, confidence

        self._fast_layout_used = False
        self.full_layout_frames += 1
        return super()._joint_layout(masks)

    def _lock_or_current(self, masks, centers, confidence):
        if self._fast_layout_used and self._locked_centers is not None:
            self.layout_locked = True
            return self._locked_centers, confidence
        return super()._lock_or_current(masks, centers, confidence)

    def _menu_scores(self, frame):
        if self.skip_menu_scores:
            self.menu_score_frames_skipped += 1
            return 0.0, 0.0, 0.0, 0.0, 0.0, None, None
        return super()._menu_scores(frame)
