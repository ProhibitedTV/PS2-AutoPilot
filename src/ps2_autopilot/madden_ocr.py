from __future__ import annotations

from dataclasses import dataclass
import re
import threading
import time
from typing import Iterable

# Import ONNX Runtime before OpenCV. A subset of Windows/Conda setups can hit
# DLL initialization failures when native CV/runtime libraries win the load
# order first. The preload is best-effort; OCR still degrades cleanly if ORT is
# genuinely unavailable.
from ps2_autopilot.ort_preload import PRELOAD as ORT_PRELOAD

import cv2
import numpy as np


@dataclass(frozen=True)
class OCRLine:
    text: str
    confidence: float
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class OCRSnapshot:
    lines: tuple[OCRLine, ...]
    text: str
    available: bool
    error: str | None = None

    @property
    def mean_confidence(self) -> float:
        if not self.lines:
            return 0.0
        return sum(line.confidence for line in self.lines) / len(self.lines)


class MaddenOCR:
    """Low-frequency semantic OCR with a bounded latest-frame worker.

    The first runtime read remains synchronous. That gives menu/navigation code a
    trustworthy initial semantic snapshot instead of briefly acting on an empty
    OCR result. After bootstrap, expensive RapidOCR/ONNX inference runs on one
    daemon worker and ``read`` immediately returns the newest completed snapshot.

    There is deliberately no unbounded queue. At most one not-yet-started frame is
    retained; if a newer frame arrives while OCR is busy, it replaces the stale
    pending frame. PCSX2, OBS and controller policy therefore never wait behind a
    backlog of screenshots that are already obsolete.
    """

    def __init__(
        self,
        interval_seconds: float = 0.70,
        min_width: int = 960,
        min_confidence: float = 0.42,
        enabled: bool = True,
        max_width: int = 1280,
        intra_op_num_threads: int = 2,
        inter_op_num_threads: int = 1,
        use_orientation_classifier: bool = False,
        async_enabled: bool = True,
        bootstrap_sync: bool = True,
    ) -> None:
        self.interval_seconds = max(0.20, float(interval_seconds))
        self.min_width = max(480, int(min_width))
        self.max_width = max(self.min_width, int(max_width))
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self.enabled = bool(enabled)
        self.intra_op_num_threads = max(1, int(intra_op_num_threads))
        self.inter_op_num_threads = max(1, int(inter_op_num_threads))
        self.use_orientation_classifier = bool(use_orientation_classifier)
        self.async_enabled = bool(async_enabled)
        self.bootstrap_sync = bool(bootstrap_sync)

        self._engine = None
        self._engine_error: str | None = None
        self._engine_lock = threading.Lock()

        self._last_at = -1e9
        self._last_submit_at = -1e9
        self._last_result_frame_at = -1e9
        self._last_result_completed_at = -1e9
        self._last = OCRSnapshot((), "", False, "OCR has not run yet")
        self.last_processing_ms = 0.0
        self.runs = 0

        self._condition = threading.Condition()
        self._pending_frame: np.ndarray | None = None
        self._pending_frame_at = -1e9
        self._worker: threading.Thread | None = None
        self._closed = False
        self._inflight = False
        self.submitted_frames = 0
        self.dropped_frames = 0

        # Async ownership metrics. 1080p capture is larger than the OCR model needs;
        # keep enough evidence to prove that the queued snapshot is materially smaller
        # without putting the full capture on the worker queue first.
        self.submit_downscales = 0
        self.last_submit_source_width = 0
        self.last_submit_source_height = 0
        self.last_submit_source_bytes = 0
        self.last_submit_owned_width = 0
        self.last_submit_owned_height = 0
        self.last_submit_owned_bytes = 0

    @property
    def available(self) -> bool:
        self._ensure_engine()
        return self._engine is not None

    @property
    def engine_error(self) -> str | None:
        self._ensure_engine()
        return self._engine_error

    @property
    def inflight(self) -> bool:
        with self._condition:
            return self._inflight

    def result_age_seconds(self, now: float) -> float | None:
        with self._condition:
            frame_at = self._last_result_frame_at
        if frame_at <= -1e8:
            return None
        return max(0.0, now - frame_at)

    def telemetry(self, now: float) -> dict:
        with self._condition:
            pending = self._pending_frame is not None
            inflight = self._inflight
            submitted = self.submitted_frames
            dropped = self.dropped_frames
            completed_at = self._last_result_completed_at
        age = self.result_age_seconds(now)
        completion_age = None if completed_at <= -1e8 else max(0.0, now - completed_at)
        reduction = 0.0
        if self.last_submit_source_bytes > 0:
            reduction = max(
                0.0,
                1.0 - self.last_submit_owned_bytes / self.last_submit_source_bytes,
            ) * 100.0
        return {
            "ocr_async_enabled": self.async_enabled,
            "ocr_inflight": inflight,
            "ocr_pending": pending,
            "ocr_result_age_ms": None if age is None else round(age * 1000.0, 1),
            "ocr_completion_age_ms": (
                None if completion_age is None else round(completion_age * 1000.0, 1)
            ),
            "ocr_submitted_frames": submitted,
            "ocr_dropped_frames": dropped,
            "ocr_submit_downscales": self.submit_downscales,
            "ocr_submit_source_width": self.last_submit_source_width or None,
            "ocr_submit_source_height": self.last_submit_source_height or None,
            "ocr_submit_source_bytes": self.last_submit_source_bytes or None,
            "ocr_submit_owned_width": self.last_submit_owned_width or None,
            "ocr_submit_owned_height": self.last_submit_owned_height or None,
            "ocr_submit_owned_bytes": self.last_submit_owned_bytes or None,
            "ocr_submit_copy_reduction_pct": round(reduction, 1),
        }

    def _ensure_engine(self) -> None:
        if not self.enabled or self._engine is not None or self._engine_error is not None:
            return
        with self._engine_lock:
            if self._engine is not None or self._engine_error is not None:
                return
            if not ORT_PRELOAD.available:
                self._engine_error = ORT_PRELOAD.error or "ONNX Runtime preload failed"
                return
            try:
                from rapidocr_onnxruntime import RapidOCR

                # Keep OCR to a small fixed pool so it cannot briefly consume every
                # core that PCSX2/OBS could otherwise use.
                self._engine = RapidOCR(
                    intra_op_num_threads=self.intra_op_num_threads,
                    inter_op_num_threads=self.inter_op_num_threads,
                )
            except Exception as exc:  # optional dependency / provider failure
                self._engine_error = f"{type(exc).__name__}: {exc}"

    @staticmethod
    def normalize_text(text: str) -> str:
        text = text.upper().replace("|", "I")
        text = re.sub(r"[^A-Z0-9&:/+\-.' ]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _box_to_norm(
        box: Iterable[Iterable[float]], width: int, height: int
    ) -> tuple[float, float, float, float]:
        pts = np.asarray(list(box), dtype=np.float32).reshape(-1, 2)
        x0, y0 = pts.min(axis=0)
        x1, y1 = pts.max(axis=0)
        return (
            float(((x0 + x1) * 0.5) / max(width, 1)),
            float(((y0 + y1) * 0.5) / max(height, 1)),
            float((x1 - x0) / max(width, 1)),
            float((y1 - y0) / max(height, 1)),
        )

    def _prepare(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if w < self.min_width:
            scale = self.min_width / max(w, 1)
            frame = cv2.resize(
                frame,
                (self.min_width, max(1, int(round(h * scale)))),
                interpolation=cv2.INTER_CUBIC,
            )
        elif w > self.max_width:
            scale = self.max_width / max(w, 1)
            frame = cv2.resize(
                frame,
                (self.max_width, max(1, int(round(h * scale)))),
                interpolation=cv2.INTER_AREA,
            )

        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8)).apply(l)
        return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)

    def _infer(self, frame: np.ndarray) -> tuple[OCRSnapshot, float]:
        self._ensure_engine()
        if self._engine is None:
            return OCRSnapshot((), "", False, self._engine_error or "OCR disabled"), 0.0

        started = time.perf_counter()
        image = self._prepare(frame)
        h, w = image.shape[:2]
        try:
            result, _ = self._engine(image, use_cls=self.use_orientation_classifier)
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000.0
            return OCRSnapshot((), "", False, f"{type(exc).__name__}: {exc}"), elapsed

        lines: list[OCRLine] = []
        for item in result or []:
            if len(item) < 3:
                continue
            box, raw_text, score = item[0], item[1], float(item[2])
            text = self.normalize_text(str(raw_text))
            if not text or score < self.min_confidence:
                continue
            x, y, bw, bh = self._box_to_norm(box, w, h)
            lines.append(OCRLine(text, score, x, y, bw, bh))

        lines.sort(key=lambda line: (line.y, line.x))
        joined = " | ".join(line.text for line in lines)
        elapsed = (time.perf_counter() - started) * 1000.0
        return OCRSnapshot(tuple(lines), joined, True, None), elapsed

    def _publish(self, snapshot: OCRSnapshot, processing_ms: float, frame_at: float) -> None:
        with self._condition:
            self._last = snapshot
            self.last_processing_ms = processing_ms
            self.runs += 1
            self._last_result_frame_at = frame_at
            self._last_result_completed_at = time.monotonic()

    def _start_worker(self) -> None:
        if not self.async_enabled:
            return
        with self._condition:
            if self._worker is not None:
                return
            self._worker = threading.Thread(
                target=self._worker_main,
                name="madden-ocr",
                daemon=True,
            )
            self._worker.start()

    def _worker_main(self) -> None:
        while True:
            with self._condition:
                while self._pending_frame is None and not self._closed:
                    self._condition.wait(timeout=0.5)
                if self._closed:
                    return
                frame = self._pending_frame
                frame_at = self._pending_frame_at
                self._pending_frame = None
                self._pending_frame_at = -1e9
                self._inflight = True

            assert frame is not None
            snapshot, processing_ms = self._infer(frame)
            self._publish(snapshot, processing_ms, frame_at)

            with self._condition:
                self._inflight = False
                self._condition.notify_all()

    def _own_submit_frame(self, frame: np.ndarray) -> np.ndarray:
        """Own the smallest full-view frame the async OCR worker can use safely.

        The capture buffer cannot be retained because the next capture may reuse it.
        For 1080p and other frames wider than ``max_width``, ``cv2.resize`` both creates
        independent storage and performs the downscale the worker would otherwise do
        later. That replaces a full-resolution ``frame.copy()`` with a substantially
        smaller owned frame while preserving every part of the screen for menu OCR.
        """
        h, w = frame.shape[:2]
        self.last_submit_source_width = int(w)
        self.last_submit_source_height = int(h)
        self.last_submit_source_bytes = int(frame.nbytes)

        if w > self.max_width:
            scale = self.max_width / max(w, 1)
            owned = cv2.resize(
                frame,
                (self.max_width, max(1, int(round(h * scale)))),
                interpolation=cv2.INTER_AREA,
            )
            self.submit_downscales += 1
        else:
            # Smaller/native-resolution frames still need independent ownership.
            owned = frame.copy()

        oh, ow = owned.shape[:2]
        self.last_submit_owned_width = int(ow)
        self.last_submit_owned_height = int(oh)
        self.last_submit_owned_bytes = int(owned.nbytes)
        return owned

    def _submit_latest(self, frame: np.ndarray, now: float) -> None:
        self._start_worker()
        owned = self._own_submit_frame(frame)
        with self._condition:
            if self._pending_frame is not None:
                self.dropped_frames += 1
            self._pending_frame = owned
            self._pending_frame_at = now
            self.submitted_frames += 1
            self._condition.notify()
        self._last_submit_at = now
        self._last_at = now

    def read(self, frame: np.ndarray, now: float) -> OCRSnapshot:
        if not self.enabled:
            self._last = OCRSnapshot((), "", False, "OCR disabled")
            return self._last

        # One synchronous bootstrap protects startup/menu navigation from acting on
        # an empty semantic snapshot. Recurring inference is then fully off-path.
        if self.runs == 0 and self.bootstrap_sync:
            snapshot, processing_ms = self._infer(frame)
            self._publish(snapshot, processing_ms, now)
            self._last_at = now
            self._last_submit_at = now
            self._start_worker()
            return snapshot

        if not self.async_enabled:
            if now - self._last_at < self.interval_seconds:
                return self._last
            snapshot, processing_ms = self._infer(frame)
            self._publish(snapshot, processing_ms, now)
            self._last_at = now
            return snapshot

        if now - self._last_submit_at >= self.interval_seconds:
            self._submit_latest(frame, now)
        with self._condition:
            return self._last

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._pending_frame = None
            self._condition.notify_all()
