from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class GameplayHudObservation:
    confidence: float
    rank_score: float
    status_score: float
    tach_score: float
    map_score: float
    pursuit_score: float = 0.0
    pursuit_icon_score: float = 0.0
    layout: str = "unknown"

    @classmethod
    def unavailable(cls) -> "GameplayHudObservation":
        return cls(0.0, 0.0, 0.0, 0.0, 0.0)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _roi(frame: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    h, w = frame.shape[:2]
    return frame[int(y0 * h) : int(y1 * h), int(x0 * w) : int(x1 * w)]


def _region_stats(region: np.ndarray) -> tuple[float, float, float, float, float]:
    if region.size == 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 80, 160)

    white = float(np.mean((hsv[..., 2] > 165) & (hsv[..., 1] < 95)))
    amber = float(
        np.mean(
            (hsv[..., 0] >= 5)
            & (hsv[..., 0] <= 35)
            & (hsv[..., 1] > 75)
            & (hsv[..., 2] > 115)
        )
    )
    red = float(
        np.mean(
            ((hsv[..., 0] < 8) | (hsv[..., 0] > 170))
            & (hsv[..., 1] > 95)
            & (hsv[..., 2] > 85)
        )
    )
    dark = float(np.mean(hsv[..., 2] < 70))
    edge = float(np.mean(edges > 0))
    return white, amber, red, dark, edge


def _pursuit_icon_score(region: np.ndarray) -> float:
    """Score the fixed neon-green police resource row in You're The Cop mode."""

    if region.size == 0:
        return 0.0
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    neon_green = (
        (hsv[..., 0] >= 40)
        & (hsv[..., 0] <= 85)
        & (hsv[..., 1] > 140)
        & (hsv[..., 2] > 130)
    )
    return _clamp01(float(np.mean(neon_green)) / 0.025)


def estimate_gameplay_hud(frame: np.ndarray) -> GameplayHudObservation:
    """Detect the fixed PS2 Hot Pursuit 2 gameplay HUD without local templates.

    The first overnight live failure corpus showed the car sitting in an active race
    for more than eight hours while road segmentation was unavailable. The HUD was
    nevertheless unmistakable and spatially stable: position block upper-left,
    lap/current panel upper-right, minimap lower-left and tachometer lower-right.

    This detector intentionally uses only normalized screen geometry and broad color /
    edge statistics so it survives track, weather and car changes. It is not OCR and
    does not depend on copyrighted screenshots in the repository.
    """

    if frame is None or frame.size == 0 or frame.ndim != 3:
        return GameplayHudObservation.unavailable()
    h, w = frame.shape[:2]
    if h < 180 or w < 320:
        return GameplayHudObservation.unavailable()

    rank = _region_stats(_roi(frame, 0.01, 0.02, 0.28, 0.36))
    status = _region_stats(_roi(frame, 0.68, 0.03, 0.98, 0.17))
    tach = _region_stats(_roi(frame, 0.76, 0.56, 0.98, 0.91))
    minimap = _region_stats(_roi(frame, 0.02, 0.35, 0.27, 0.58))

    rank_score = (
        0.55 * _clamp01(rank[1] / 0.055)
        + 0.20 * _clamp01(rank[3] / 0.55)
        + 0.25 * _clamp01(rank[4] / 0.025)
    )
    status_score = (
        0.45 * _clamp01(status[1] / 0.045)
        + 0.35 * _clamp01(status[4] / 0.045)
        + 0.20 * _clamp01(status[3] / 0.18)
    )
    tach_score = (
        0.45 * _clamp01(tach[3] / 0.75)
        + 0.35 * _clamp01((tach[1] + tach[2]) / 0.007)
        + 0.20 * _clamp01(tach[4] / 0.006)
    )
    map_score = (
        0.50 * _clamp01(minimap[3] / 0.75)
        + 0.30 * _clamp01(minimap[0] / 0.008)
        + 0.20 * _clamp01(minimap[4] / 0.012)
    )

    racer_confidence = (
        0.30 * rank_score
        + 0.30 * status_score
        + 0.30 * tach_score
        + 0.10 * map_score
    )

    # A dark menu can accidentally look tachometer-ish. Require at least two of the
    # three primary HUD anchors before returning strong gameplay evidence.
    primary_anchors = sum(score >= 0.72 for score in (rank_score, status_score, tach_score))
    if primary_anchors < 2:
        racer_confidence *= 0.45

    # You're The Cop replaces the upper-left rank block with a top-right row of
    # bright-green police resources above the timer. The first V11.1 live run
    # therefore had perfect minimap/tach anchors but was penalized to 0.32 and
    # mistaken for an unknown menu. The resource row is absent from the retained
    # racer/menu corpus, so use it as a strict alternate layout anchor rather than
    # weakening the normal two-of-three rule.
    pursuit_icon_score = _pursuit_icon_score(_roi(frame, 0.66, 0.035, 0.98, 0.13))
    pursuit_score = (
        0.45 * pursuit_icon_score
        + 0.25 * tach_score
        + 0.20 * map_score
        + 0.10 * status_score
    )
    pursuit_owned = (
        pursuit_icon_score >= 0.72
        and tach_score >= 0.72
        and map_score >= 0.72
        and status_score >= 0.50
    )
    if not pursuit_owned:
        pursuit_score *= 0.45

    if pursuit_owned and pursuit_score > racer_confidence:
        layout = "pursuit"
    elif racer_confidence >= 0.55:
        layout = "racer"
    else:
        layout = "unknown"
    confidence = max(racer_confidence, pursuit_score)

    return GameplayHudObservation(
        confidence=_clamp01(confidence),
        rank_score=_clamp01(rank_score),
        status_score=_clamp01(status_score),
        tach_score=_clamp01(tach_score),
        map_score=_clamp01(map_score),
        pursuit_score=_clamp01(pursuit_score),
        pursuit_icon_score=_clamp01(pursuit_icon_score),
        layout=layout,
    )
