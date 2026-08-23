from __future__ import annotations

from dataclasses import dataclass
import re
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
    """Low-frequency OCR for Madden menus/HUD.

    RapidOCR is loaded lazily so AutoPilot remains usable without OCR installed.
    Small PCSX2 renders are enlarged, while large 1080p captures are reduced before
    inference. That preserves readable menu text without asking ONNX Runtime to
    process the full emulator framebuffer every OCR cycle.
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
    ) -> None:
        self.interval_seconds = max(0.20, float(interval_seconds))
        self.min_width = max(480, int(min_width))
        self.max_width = max(self.min_width, int(max_width))
        self.min_confidence = max(0.0, min(1.0, float(min_confidence)))
        self.enabled = bool(enabled)
        self.intra_op_num_threads = max(1, int(intra_op_num_threads))
        self.inter_op_num_threads = max(1, int(inter_op_num_threads))
        self.use_orientation_classifier = bool(use_orientation_classifier)
        self._engine = None
        self._engine_error: str | None = None
        self._last_at = -1e9
        self._last = OCRSnapshot((), "", False, "OCR has not run yet")
        self.last_processing_ms = 0.0
        self.runs = 0

    @property
    def available(self) -> bool:
        self._ensure_engine()
        return self._engine is not None

    @property
    def engine_error(self) -> str | None:
        self._ensure_engine()
        return self._engine_error

    def _ensure_engine(self) -> None:
        if not self.enabled or self._engine is not None or self._engine_error is not None:
            return
        if not ORT_PRELOAD.available:
            self._engine_error = ORT_PRELOAD.error or "ONNX Runtime preload failed"
            return
        try:
            from rapidocr_onnxruntime import RapidOCR

            # RapidOCR 1.4.x exposes these ONNX Runtime thread controls directly.
            # Keeping OCR to a small fixed pool prevents it from briefly consuming
            # every core that PCSX2/OBS could otherwise use.
            self._engine = RapidOCR(
                intra_op_num_threads=self.intra_op_num_threads,
                inter_op_num_threads=self.inter_op_num_threads,
            )
        except Exception as exc:  # optional dependency / runtime provider failure
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

        # Local contrast boost helps the red/white Madden menu fonts after the
        # controlled resize. CLAHE runs on the reduced frame instead of 1080p.
        lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        l = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8)).apply(l)
        return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)

    def read(self, frame: np.ndarray, now: float) -> OCRSnapshot:
        if now - self._last_at < self.interval_seconds:
            return self._last

        self._last_at = now
        self._ensure_engine()
        if self._engine is None:
            self._last = OCRSnapshot((), "", False, self._engine_error or "OCR disabled")
            return self._last

        started = time.perf_counter()
        image = self._prepare(frame)
        h, w = image.shape[:2]
        try:
            result, _ = self._engine(image, use_cls=self.use_orientation_classifier)
        except Exception as exc:
            self.last_processing_ms = (time.perf_counter() - started) * 1000.0
            self._last = OCRSnapshot((), "", False, f"{type(exc).__name__}: {exc}")
            return self._last

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
        self.runs += 1
        self.last_processing_ms = (time.perf_counter() - started) * 1000.0
        self._last = OCRSnapshot(tuple(lines), joined, True, None)
        return self._last
