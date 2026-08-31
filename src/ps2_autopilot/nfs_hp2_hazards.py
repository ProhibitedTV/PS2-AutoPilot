from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class HazardObservation:
    """A conservative near-field obstacle candidate inside the road funnel."""

    confidence: float
    center_x: float
    proximity: float
    width: float
    height: float
    area: float

    @classmethod
    def unavailable(cls) -> "HazardObservation":
        return cls(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def estimate_near_hazard(
    frame: np.ndarray,
    *,
    sample_width: int = 360,
    color_distance: float = 52.0,
) -> HazardObservation:
    """Estimate a vehicle/barricade-like obstacle in the near road corridor.

    Hot Pursuit 2 mixes civilian traffic, police cars, roadblocks, spike-strip
    setups and destructible track furniture into an otherwise strongly coherent
    road surface. This detector intentionally does *not* try to classify those
    objects. It only looks for a compact non-road component inside a trapezoidal
    near-field funnel ahead of the player's car.

    The result is designed as calibration telemetry first. V4 can optionally use
    it for bounded steering avoidance, but that behavior is disabled by default
    until live PCSX2 footage proves the detector is stable across tracks.
    """

    if frame is None or frame.size == 0 or frame.ndim != 3:
        return HazardObservation.unavailable()

    height, width = frame.shape[:2]
    if height < 160 or width < 240:
        return HazardObservation.unavailable()

    target_w = max(240, min(int(sample_width), width))
    target_h = max(160, int(round(height * target_w / width)))
    small = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)

    y0 = int(target_h * 0.47)
    y1 = int(target_h * 0.86)
    roi = small[y0:y1]
    rh, rw = roi.shape[:2]
    if rh < 50 or rw < 120:
        return HazardObservation.unavailable()

    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB).astype(np.float32)

    # Sample pavement below the normal obstacle search band. Keep this patch away
    # from the extreme bottom where the player's car body may dominate the frame.
    sx0, sx1 = int(rw * 0.45), int(rw * 0.55)
    sy0, sy1 = int(rh * 0.72), int(rh * 0.88)
    seed = lab[sy0:sy1, sx0:sx1]
    if seed.size == 0:
        return HazardObservation.unavailable()
    seed_color = np.median(seed.reshape(-1, 3), axis=0)

    delta = lab - seed_color
    distance = np.sqrt((delta[..., 0] * 0.72) ** 2 + delta[..., 1] ** 2 + delta[..., 2] ** 2)
    non_road = (distance >= float(color_distance)).astype(np.uint8) * 255

    # The funnel is intentionally narrower near the horizon. Components touching
    # the funnel edge are commonly scenery/road shoulders, so those are rejected.
    funnel = np.zeros((rh, rw), dtype=np.uint8)
    for y in range(rh):
        t = y / max(1, rh - 1)
        half = 0.15 + 0.20 * t
        left = int(rw * (0.5 - half))
        right = int(rw * (0.5 + half))
        funnel[y, max(0, left) : min(rw, right)] = 255

    # Ignore the bottom-most strip where the player's own car is most likely.
    funnel[int(rh * 0.90) :, :] = 0
    candidate = cv2.bitwise_and(non_road, funnel)
    candidate = cv2.morphologyEx(
        candidate,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
        iterations=1,
    )
    candidate = cv2.morphologyEx(
        candidate,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 7)),
        iterations=1,
    )

    count, labels, stats, centroids = cv2.connectedComponentsWithStats(candidate, connectivity=8)
    if count <= 1:
        return HazardObservation.unavailable()

    best: HazardObservation | None = None
    roi_area = float(rw * rh)
    min_area = max(40, int(roi_area * 0.0025))

    for label in range(1, count):
        x = int(stats[label, cv2.CC_STAT_LEFT])
        y = int(stats[label, cv2.CC_STAT_TOP])
        w = int(stats[label, cv2.CC_STAT_WIDTH])
        h = int(stats[label, cv2.CC_STAT_HEIGHT])
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area:
            continue

        width_ratio = w / max(1.0, float(rw))
        height_ratio = h / max(1.0, float(rh))
        area_ratio = area / roi_area
        if width_ratio < 0.025 or width_ratio > 0.34:
            continue
        if height_ratio < 0.035 or height_ratio > 0.58:
            continue

        cx, cy = centroids[label]
        center_x = _clamp((float(cx) - (rw - 1) * 0.5) / max(1.0, (rw - 1) * 0.5))
        proximity = _clamp(float(cy) / max(1.0, rh - 1), 0.0, 1.0)

        # Reject components that hug the expected funnel boundary. Those are much
        # more likely to be verge/scenery than something directly in the lane.
        t = float(cy) / max(1.0, rh - 1)
        half = 0.15 + 0.20 * t
        allowed_left = rw * (0.5 - half)
        allowed_right = rw * (0.5 + half)
        margin = max(3.0, rw * 0.012)
        if x <= allowed_left + margin or x + w >= allowed_right - margin:
            continue

        centrality = 1.0 - min(1.0, abs(center_x) / 0.72)
        size_score = min(1.0, area_ratio / 0.035)
        width_score = min(1.0, width_ratio / 0.18)
        proximity_score = min(1.0, max(0.0, (proximity - 0.20) / 0.65))
        confidence = (
            0.30 * size_score
            + 0.18 * width_score
            + 0.32 * proximity_score
            + 0.20 * centrality
        )

        observation = HazardObservation(
            confidence=_clamp(confidence, 0.0, 1.0),
            center_x=center_x,
            proximity=proximity,
            width=_clamp(width_ratio, 0.0, 1.0),
            height=_clamp(height_ratio, 0.0, 1.0),
            area=_clamp(area_ratio, 0.0, 1.0),
        )
        if best is None or observation.confidence > best.confidence:
            best = observation

    return best or HazardObservation.unavailable()
