from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class RoadObservation:
    """Compact vision estimate for the drivable corridor ahead of the car."""

    confidence: float
    center_x: float
    curvature: float
    width: float
    coverage: float
    center_contact: float

    @classmethod
    def unavailable(cls) -> "RoadObservation":
        return cls(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


def _clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _band_center(mask: np.ndarray, y_fraction: float) -> tuple[float, float] | None:
    h, w = mask.shape[:2]
    y = max(0, min(h - 1, int(round((h - 1) * y_fraction))))
    y0 = max(0, y - 2)
    y1 = min(h, y + 3)
    band = mask[y0:y1]
    xs = np.flatnonzero(np.any(band, axis=0))
    if xs.size < max(6, int(w * 0.025)):
        return None
    center = float(xs.mean())
    width = float(xs[-1] - xs[0] + 1)
    center_norm = _clamp((center - (w - 1) * 0.5) / max(1.0, (w - 1) * 0.5))
    return center_norm, width / max(1.0, float(w))


def estimate_road(
    frame: np.ndarray,
    *,
    sample_width: int = 360,
    color_distance: float = 42.0,
) -> RoadObservation:
    """Estimate road center and bend from a single chase-camera frame.

    Hot Pursuit 2 usually keeps the player's car on a visually coherent road surface.
    Instead of relying on a track-specific color, this detector samples the pavement
    currently beneath/ahead of the car, finds the connected region with the strongest
    bottom-center contact, then measures that corridor at several look-ahead bands.

    The method is deliberately calibration-friendly: it is fast, uses only OpenCV and
    NumPy already required by AutoPilot, and fails closed when the scene does not look
    like one continuous drivable corridor.
    """

    if frame is None or frame.size == 0 or frame.ndim != 3:
        return RoadObservation.unavailable()

    height, width = frame.shape[:2]
    if height < 120 or width < 180:
        return RoadObservation.unavailable()

    target_w = max(180, min(int(sample_width), width))
    target_h = max(120, int(round(height * target_w / width)))
    small = cv2.resize(frame, (target_w, target_h), interpolation=cv2.INTER_AREA)

    y0 = int(target_h * 0.43)
    y1 = max(y0 + 30, int(target_h * 0.94))
    roi = small[y0:y1]
    rh, rw = roi.shape[:2]
    if rh < 30 or rw < 80:
        return RoadObservation.unavailable()

    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB).astype(np.float32)

    # Sample a broad central patch ahead of the car rather than the very bottom,
    # where the vehicle body/HUD can dominate the image.
    sx0 = int(rw * 0.43)
    sx1 = max(sx0 + 4, int(rw * 0.57))
    sy0 = int(rh * 0.62)
    sy1 = max(sy0 + 4, int(rh * 0.78))
    seed = lab[sy0:sy1, sx0:sx1]
    if seed.size == 0:
        return RoadObservation.unavailable()

    seed_color = np.median(seed.reshape(-1, 3), axis=0)
    delta = lab - seed_color
    # Luminance changes with shadows more aggressively than chroma, so weight it
    # slightly less while still preventing grass/sky from joining the road region.
    distance = np.sqrt((delta[..., 0] * 0.72) ** 2 + delta[..., 1] ** 2 + delta[..., 2] ** 2)
    candidate = (distance <= float(color_distance)).astype(np.uint8) * 255

    kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 7))
    kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 3))
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, kernel_close, iterations=2)
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_OPEN, kernel_open, iterations=1)

    count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, connectivity=8)
    if count <= 1:
        return RoadObservation.unavailable()

    ax0 = int(rw * 0.34)
    ax1 = max(ax0 + 1, int(rw * 0.66))
    ay0 = int(rh * 0.70)
    anchor = labels[ay0:, ax0:ax1]
    anchor_area = max(1, anchor.size)

    best_label = 0
    best_score = 0.0
    best_contact = 0.0
    min_area = max(24, int(rw * rh * 0.025))
    for label in range(1, count):
        area = int(stats[label, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        contact = float(np.count_nonzero(anchor == label)) / anchor_area
        area_ratio = area / float(rw * rh)
        score = contact * 2.4 + min(0.8, area_ratio)
        if score > best_score:
            best_score = score
            best_label = label
            best_contact = contact

    if best_label == 0 or best_contact < 0.035:
        return RoadObservation.unavailable()

    road = labels == best_label
    coverage = float(np.mean(road))
    # A flat loading/menu/solid-color frame can perfectly match its own seed and
    # otherwise masquerade as a giant road. Chase-camera pavement should leave a
    # meaningful amount of non-road scene visible, so reject near-full-frame masks.
    if coverage > 0.84:
        return RoadObservation.unavailable()

    samples: list[tuple[float, float, float]] = []
    for fraction, weight in ((0.28, 0.34), (0.46, 0.30), (0.66, 0.22), (0.84, 0.14)):
        measured = _band_center(road, fraction)
        if measured is not None:
            samples.append((measured[0], measured[1], weight))

    if len(samples) < 2:
        return RoadObservation.unavailable()

    weight_sum = sum(item[2] for item in samples)
    center_x = sum(item[0] * item[2] for item in samples) / max(1e-6, weight_sum)

    far = samples[0][0]
    near = samples[-1][0]
    curvature = _clamp(far - near)
    near_width = samples[-1][1]

    band_score = len(samples) / 4.0
    coverage_score = min(1.0, coverage / 0.34)
    contact_score = min(1.0, best_contact / 0.34)
    width_score = min(1.0, near_width / 0.45)

    # Penalize wildly discontinuous center samples; a coherent road should move
    # progressively across the image rather than teleporting between bands.
    centers = [item[0] for item in samples]
    jumps = [abs(b - a) for a, b in zip(centers, centers[1:])]
    continuity = 1.0 - min(1.0, (sum(jumps) / max(1, len(jumps))) / 0.70)

    confidence = (
        0.28 * band_score
        + 0.24 * coverage_score
        + 0.26 * contact_score
        + 0.10 * width_score
        + 0.12 * continuity
    )

    return RoadObservation(
        confidence=_clamp(confidence, 0.0, 1.0),
        center_x=_clamp(center_x),
        curvature=curvature,
        width=_clamp(near_width, 0.0, 1.0),
        coverage=_clamp(coverage, 0.0, 1.0),
        center_contact=_clamp(best_contact, 0.0, 1.0),
    )
