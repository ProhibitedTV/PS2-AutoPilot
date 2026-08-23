from __future__ import annotations

from dataclasses import dataclass
import math
import time

import cv2
import numpy as np

from .madden_vision import MaddenVision


@dataclass(frozen=True)
class SpatialCandidate:
    """One temporally stabilized field entity candidate in normalized coordinates."""

    track_id: int
    x: float
    y: float
    confidence: float
    area: float
    motion: float
    marker: float = 0.0

    def compact(self) -> dict:
        return {
            "id": self.track_id,
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "confidence": round(self.confidence, 3),
            "area": round(self.area, 1),
            "motion": round(self.motion, 3),
            "marker": round(self.marker, 3),
        }


@dataclass(frozen=True)
class SpatialSnapshot:
    available: bool
    players: tuple[SpatialCandidate, ...] = ()
    controlled: SpatialCandidate | None = None
    ball: SpatialCandidate | None = None
    target_x: float = 0.0
    target_y: float = 0.0
    target_confidence: float = 0.0
    open_space_x: float = 0.0
    open_space_confidence: float = 0.0
    processing_ms: float = 0.0
    reason: str = "not sampled"

    @property
    def player_count(self) -> int:
        return len(self.players)

    def telemetry(self, max_players: int = 8) -> dict:
        return {
            "spatial_enabled": self.available,
            "spatial_reason": self.reason,
            "spatial_players": self.player_count,
            "spatial_player_candidates": [p.compact() for p in self.players[:max_players]],
            "spatial_controlled_x": None if self.controlled is None else round(self.controlled.x, 3),
            "spatial_controlled_y": None if self.controlled is None else round(self.controlled.y, 3),
            "spatial_controlled_confidence": 0.0
            if self.controlled is None
            else round(self.controlled.confidence, 3),
            "spatial_ball_x": None if self.ball is None else round(self.ball.x, 3),
            "spatial_ball_y": None if self.ball is None else round(self.ball.y, 3),
            "spatial_ball_confidence": 0.0 if self.ball is None else round(self.ball.confidence, 3),
            "spatial_target_x": round(self.target_x, 3),
            "spatial_target_y": round(self.target_y, 3),
            "spatial_target_confidence": round(self.target_confidence, 3),
            "spatial_open_x": round(self.open_space_x, 3),
            "spatial_open_confidence": round(self.open_space_confidence, 3),
            "spatial_processing_ms": round(self.processing_ms, 2),
        }


@dataclass
class _Track:
    track_id: int
    x: float
    y: float
    confidence: float
    area: float
    motion: float
    marker: float
    last_seen: float
    hits: int = 1


@dataclass
class _RawCandidate:
    x: float
    y: float
    confidence: float
    area: float
    motion: float
    marker: float


class MaddenSpatialTracker:
    """Lightweight OpenCV/temporal field-localization layer.

    This intentionally does not pretend to know exact roster identity. It finds
    player-like non-grass blobs embedded in the turf, stabilizes them across frames,
    looks for a likely control marker, and maintains a conservative tiny moving-object
    hypothesis for the football. Every output has a confidence and the gameplay policy
    must fall back to the older motion policy when confidence is weak.
    """

    def __init__(
        self,
        sample_width: int = 480,
        min_player_confidence: float = 0.30,
        max_players: int = 22,
        track_ttl_seconds: float = 0.90,
        ball_ttl_seconds: float = 0.70,
    ) -> None:
        self.sample_width = max(240, int(sample_width))
        self.min_player_confidence = max(0.05, min(0.95, float(min_player_confidence)))
        self.max_players = max(4, min(32, int(max_players)))
        self.track_ttl_seconds = max(0.25, float(track_ttl_seconds))
        self.ball_ttl_seconds = max(0.20, float(ball_ttl_seconds))
        self._tracks: dict[int, _Track] = {}
        self._next_track_id = 1
        self._ball: _Track | None = None
        self._ball_last_seen = -1e9

    @staticmethod
    def empty(reason: str = "not sampled") -> SpatialSnapshot:
        return SpatialSnapshot(False, reason=reason)

    @staticmethod
    def _normalize(x: float, extent: int) -> float:
        if extent <= 1:
            return 0.0
        return max(-1.0, min(1.0, x / (extent - 1) * 2.0 - 1.0))

    def _prepare(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        crop, _, _ = MaddenVision._field_crop(frame)
        h, w = crop.shape[:2]
        if h < 8 or w < 8:
            raise ValueError("field crop too small")
        target_w = self.sample_width
        target_h = max(120, int(round(h * target_w / w)))
        small = cv2.resize(crop, (target_w, target_h), interpolation=cv2.INTER_AREA)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        green = cv2.inRange(
            hsv,
            np.array([28, 35, 25], dtype=np.uint8),
            np.array([100, 255, 255], dtype=np.uint8),
        )
        support = cv2.dilate(green, np.ones((13, 13), np.uint8), iterations=2)
        return small, hsv, support

    @staticmethod
    def _motion_mask(previous: np.ndarray | None, current: np.ndarray) -> np.ndarray:
        if previous is None or previous.shape != current.shape:
            return np.zeros(current.shape[:2], dtype=np.uint8)
        a = cv2.cvtColor(previous, cv2.COLOR_BGR2GRAY)
        b = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
        diff = cv2.absdiff(a, b)
        _, mask = cv2.threshold(diff, 20, 255, cv2.THRESH_BINARY)
        return cv2.medianBlur(mask, 3)

    @staticmethod
    def _marker_score(hsv: np.ndarray, x: int, y: int, w: int, h: int) -> float:
        hh, ww = hsv.shape[:2]
        x0 = max(0, x - 3)
        x1 = min(ww, x + w + 3)
        y0 = max(0, y + max(0, h - 3))
        y1 = min(hh, y + h + 8)
        if x1 <= x0 or y1 <= y0:
            return 0.0
        band = hsv[y0:y1, x0:x1]
        hue = band[:, :, 0]
        sat = band[:, :, 1]
        val = band[:, :, 2]
        bright_color = (sat >= 135) & (val >= 150)
        yellow = bright_color & (hue >= 12) & (hue <= 42)
        cyan = bright_color & (hue >= 78) & (hue <= 112)
        hot = bright_color & ((hue <= 8) | (hue >= 168))
        ratio = float(
            np.count_nonzero(yellow | cyan | hot)
            / max(band.shape[0] * band.shape[1], 1)
        )
        return max(0.0, min(1.0, ratio * 3.8))

    def _player_candidates(
        self,
        current: np.ndarray,
        hsv: np.ndarray,
        support: np.ndarray,
        motion: np.ndarray,
    ) -> list[_RawCandidate]:
        green = cv2.inRange(
            hsv,
            np.array([28, 35, 25], dtype=np.uint8),
            np.array([100, 255, 255], dtype=np.uint8),
        )
        foreground = cv2.bitwise_and(cv2.bitwise_not(green), support)
        foreground = cv2.morphologyEx(
            foreground, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)
        )
        foreground = cv2.morphologyEx(
            foreground, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)
        )

        count, _, stats, centroids = cv2.connectedComponentsWithStats(foreground, 8)
        out: list[_RawCandidate] = []
        height, width = foreground.shape
        for idx in range(1, count):
            x, y, w, h, area = [int(v) for v in stats[idx]]
            if area < 9 or area > 780:
                continue
            if w < 2 or h < 3 or w > 36 or h > 48:
                continue
            aspect = w / max(h, 1)
            if aspect < 0.12 or aspect > 3.2:
                continue
            compactness = area / max(w * h, 1)
            if compactness < 0.18:
                continue

            cx, cy = centroids[idx]
            box_motion = motion[y : y + h, x : x + w]
            motion_score = float(
                np.count_nonzero(box_motion) / max(box_motion.size, 1)
            )
            sat = hsv[y : y + h, x : x + w, 1]
            val = hsv[y : y + h, x : x + w, 2]
            color_score = float(np.mean(sat) / 255.0)
            dark_score = max(
                0.0, min(1.0, (165.0 - float(np.mean(val))) / 135.0)
            )

            pad = 5
            ex0, ex1 = max(0, x - pad), min(width, x + w + pad)
            ey0, ey1 = max(0, y - pad), min(height, y + h + pad)
            surround = green[ey0:ey1, ex0:ex1]
            surround_score = float(
                np.count_nonzero(surround) / max(surround.size, 1)
            )

            size_score = math.exp(
                -abs(math.log(max(area, 1) / 72.0)) * 0.55
            )
            marker = self._marker_score(hsv, x, y, w, h)
            confidence = (
                0.31 * size_score
                + 0.22 * compactness
                + 0.22 * surround_score
                + 0.12 * max(color_score, dark_score)
                + 0.13 * motion_score
                + 0.10 * marker
            )
            confidence = max(0.0, min(0.99, confidence))
            if confidence < self.min_player_confidence:
                continue
            out.append(
                _RawCandidate(
                    x=self._normalize(float(cx), width),
                    y=self._normalize(float(cy), height),
                    confidence=confidence,
                    area=float(area),
                    motion=motion_score,
                    marker=marker,
                )
            )

        out.sort(
            key=lambda c: c.confidence + c.motion * 0.12 + c.marker * 0.10,
            reverse=True,
        )
        return out[: self.max_players]

    def _associate(
        self, candidates: list[_RawCandidate], now: float
    ) -> tuple[SpatialCandidate, ...]:
        stale = [
            tid
            for tid, track in self._tracks.items()
            if now - track.last_seen > self.track_ttl_seconds
        ]
        for tid in stale:
            self._tracks.pop(tid, None)

        unused = set(self._tracks)
        resolved: list[SpatialCandidate] = []
        for raw in candidates:
            best_id = None
            best_distance = 999.0
            for tid in unused:
                track = self._tracks[tid]
                distance = math.hypot(raw.x - track.x, raw.y - track.y)
                if distance < best_distance and distance <= 0.18:
                    best_id = tid
                    best_distance = distance
            if best_id is None:
                tid = self._next_track_id
                self._next_track_id += 1
                track = _Track(
                    tid,
                    raw.x,
                    raw.y,
                    raw.confidence,
                    raw.area,
                    raw.motion,
                    raw.marker,
                    now,
                )
                self._tracks[tid] = track
            else:
                tid = best_id
                unused.discard(tid)
                track = self._tracks[tid]
                alpha = 0.42
                track.x = track.x * (1.0 - alpha) + raw.x * alpha
                track.y = track.y * (1.0 - alpha) + raw.y * alpha
                track.area = track.area * 0.60 + raw.area * 0.40
                track.motion = track.motion * 0.45 + raw.motion * 0.55
                track.marker = max(raw.marker, track.marker * 0.72)
                track.confidence = max(
                    0.0,
                    min(
                        0.99,
                        raw.confidence + min(0.18, track.hits * 0.018),
                    ),
                )
                track.last_seen = now
                track.hits += 1
            resolved.append(
                SpatialCandidate(
                    track_id=track.track_id,
                    x=track.x,
                    y=track.y,
                    confidence=track.confidence,
                    area=track.area,
                    motion=track.motion,
                    marker=track.marker,
                )
            )
        resolved.sort(key=lambda c: c.confidence, reverse=True)
        return tuple(resolved[: self.max_players])

    @staticmethod
    def _controlled_candidate(
        players: tuple[SpatialCandidate, ...]
    ) -> SpatialCandidate | None:
        marked = [
            p for p in players if p.marker >= 0.22 and p.confidence >= 0.42
        ]
        if not marked:
            return None
        return max(
            marked,
            key=lambda p: p.marker * 0.70 + p.confidence * 0.30,
        )

    def _ball_candidate(
        self,
        current: np.ndarray,
        support: np.ndarray,
        motion: np.ndarray,
        players: tuple[SpatialCandidate, ...],
        now: float,
    ) -> SpatialCandidate | None:
        moving = cv2.bitwise_and(motion, support)
        moving = cv2.morphologyEx(
            moving, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8)
        )
        count, _, stats, centroids = cv2.connectedComponentsWithStats(moving, 8)
        height, width = moving.shape
        gray = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
        best: tuple[float, SpatialCandidate] | None = None
        for idx in range(1, count):
            x, y, w, h, area = [int(v) for v in stats[idx]]
            if area < 2 or area > 72 or w > 13 or h > 13:
                continue
            cx, cy = centroids[idx]
            nx = self._normalize(float(cx), width)
            ny = self._normalize(float(cy), height)
            patch = gray[y : y + h, x : x + w]
            mean_v = float(np.mean(patch) / 255.0) if patch.size else 0.5
            contrast = abs(mean_v - 0.50) * 2.0
            smallness = math.exp(
                -abs(math.log(max(area, 1) / 11.0)) * 0.65
            )
            continuity = 0.0
            if self._ball is not None:
                d = math.hypot(nx - self._ball.x, ny - self._ball.y)
                continuity = max(0.0, 1.0 - d / 0.32)
            nearest_player = min(
                (math.hypot(nx - p.x, ny - p.y) for p in players),
                default=0.30,
            )
            separation = max(
                0.0, min(1.0, nearest_player / 0.18)
            )
            score = (
                0.36 * smallness
                + 0.25 * contrast
                + 0.25 * continuity
                + 0.14 * separation
            )
            if self._ball is None:
                score *= 0.90

            # A weak first sighting is allowed to seed a *hypothesis* for telemetry,
            # but it remains below the gameplay policy's normal >=.50 action gate.
            # Subsequent samples must supply temporal continuity before the ball can
            # become steering-grade evidence. This is safer than lowering the policy
            # threshold just to acquire a first lock.
            threshold = 0.36 if self._ball is None else 0.40
            if score < threshold:
                continue
            candidate = SpatialCandidate(
                track_id=0,
                x=nx,
                y=ny,
                confidence=max(0.0, min(0.92, score)),
                area=float(area),
                motion=1.0,
                marker=0.0,
            )
            if best is None or score > best[0]:
                best = (score, candidate)

        if best is not None:
            candidate = best[1]
            if self._ball is None:
                self._ball = _Track(
                    0,
                    candidate.x,
                    candidate.y,
                    candidate.confidence,
                    candidate.area,
                    1.0,
                    0.0,
                    now,
                )
            else:
                alpha = 0.55
                self._ball.x = self._ball.x * (1.0 - alpha) + candidate.x * alpha
                self._ball.y = self._ball.y * (1.0 - alpha) + candidate.y * alpha
                continuity_boost = min(0.12, self._ball.hits * 0.025)
                self._ball.confidence = max(
                    candidate.confidence + continuity_boost,
                    self._ball.confidence * 0.78,
                )
                self._ball.confidence = min(0.94, self._ball.confidence)
                self._ball.area = candidate.area
                self._ball.last_seen = now
                self._ball.hits += 1
            self._ball_last_seen = now
        elif self._ball is not None:
            age = now - self._ball_last_seen
            if age > self.ball_ttl_seconds:
                self._ball = None
            else:
                self._ball.confidence *= 0.82

        if self._ball is None:
            return None
        return SpatialCandidate(
            track_id=0,
            x=self._ball.x,
            y=self._ball.y,
            confidence=self._ball.confidence,
            area=self._ball.area,
            motion=1.0,
            marker=0.0,
        )

    @staticmethod
    def _open_space(
        players: tuple[SpatialCandidate, ...]
    ) -> tuple[float, float]:
        if len(players) < 3:
            return 0.0, 0.0
        centers = np.linspace(-0.72, 0.72, 9)
        costs: list[float] = []
        for center in centers:
            occupancy = 0.0
            for p in players:
                depth_weight = 0.65 + max(0.0, p.y) * 0.35
                occupancy += (
                    p.confidence
                    * depth_weight
                    * math.exp(-((p.x - center) ** 2) / 0.055)
                )
            sideline_penalty = abs(float(center)) * 0.42
            costs.append(occupancy + sideline_penalty)
        order = np.argsort(np.asarray(costs))
        best = int(order[0])
        second = int(order[1]) if len(order) > 1 else best
        margin = max(0.0, costs[second] - costs[best])
        confidence = min(
            0.90, 0.18 + len(players) * 0.045 + margin * 0.20
        )
        return float(centers[best]), float(confidence)

    @staticmethod
    def _fallback_target(
        players: tuple[SpatialCandidate, ...]
    ) -> tuple[float, float, float]:
        if not players:
            return 0.0, 0.0, 0.0
        target = max(
            players,
            key=lambda p: p.motion * 0.62 + p.confidence * 0.38,
        )
        confidence = min(
            0.72, target.confidence * 0.52 + target.motion * 0.30
        )
        return target.x, target.y, confidence

    def observe(
        self,
        frame: np.ndarray,
        previous_frame: np.ndarray | None,
        now: float,
    ) -> SpatialSnapshot:
        started = time.perf_counter()
        try:
            current, hsv, support = self._prepare(frame)
            previous_small = None
            if previous_frame is not None and previous_frame.shape == frame.shape:
                previous_crop, _, _ = MaddenVision._field_crop(previous_frame)
                previous_small = cv2.resize(
                    previous_crop,
                    (current.shape[1], current.shape[0]),
                    interpolation=cv2.INTER_AREA,
                )
            motion = self._motion_mask(previous_small, current)
            raw_players = self._player_candidates(
                current, hsv, support, motion
            )
            players = self._associate(raw_players, now)
            controlled = self._controlled_candidate(players)
            ball = self._ball_candidate(
                current, support, motion, players, now
            )
            open_x, open_conf = self._open_space(players)

            if ball is not None and ball.confidence >= 0.45:
                target_x, target_y, target_conf = (
                    ball.x,
                    ball.y,
                    ball.confidence,
                )
                reason = "ball hypothesis"
            else:
                target_x, target_y, target_conf = self._fallback_target(players)
                reason = (
                    "player-motion target"
                    if target_conf > 0
                    else "no stable target"
                )

            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return SpatialSnapshot(
                available=True,
                players=players,
                controlled=controlled,
                ball=ball,
                target_x=target_x,
                target_y=target_y,
                target_confidence=target_conf,
                open_space_x=open_x,
                open_space_confidence=open_conf,
                processing_ms=elapsed_ms,
                reason=reason,
            )
        except Exception as exc:
            return SpatialSnapshot(
                available=False,
                processing_ms=(time.perf_counter() - started) * 1000.0,
                reason=f"{type(exc).__name__}: {exc}",
            )
