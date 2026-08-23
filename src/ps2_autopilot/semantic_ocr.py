from __future__ import annotations

from .madden_ocr import MaddenOCR, OCRLine, OCRSnapshot


class SemanticOCR(MaddenOCR):
    """Game-agnostic alias for the existing bounded RapidOCR worker.

    The OCR engine was originally introduced for Madden, but its implementation is
    not football-specific. New game profiles should use this semantic name rather
    than depending on a Madden-branded type. Madden keeps its existing import so its
    validated behavior remains untouched.
    """


__all__ = ["SemanticOCR", "OCRLine", "OCRSnapshot"]
