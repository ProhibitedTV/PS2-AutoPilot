from __future__ import annotations

"""Best-effort early preload for ONNX Runtime on Windows.

Some Windows Python/Conda environments fail to initialize ONNX Runtime when
other native extension modules (notably OpenCV and their bundled runtimes) are
loaded first. Importing ORT before those modules gives the Windows loader a
stable copy of its runtime dependencies. This module is deliberately tiny so
it can be imported before cv2/numpy-heavy modules.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ORTPreload:
    available: bool
    version: str | None = None
    error: str | None = None


def preload_onnxruntime() -> ORTPreload:
    try:
        import onnxruntime as ort

        return ORTPreload(True, getattr(ort, "__version__", "unknown"), None)
    except Exception as exc:
        return ORTPreload(False, None, f"{type(exc).__name__}: {exc}")


PRELOAD = preload_onnxruntime()
