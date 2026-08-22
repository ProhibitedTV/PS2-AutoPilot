from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AppConfig:
    raw: dict[str, Any]

    @property
    def window_title_contains(self) -> str:
        return str(self.raw.get("window_title_contains", "PCSX2"))

    @property
    def loop_hz(self) -> float:
        return float(self.raw.get("loop_hz", 8))


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return AppConfig(raw=data)
