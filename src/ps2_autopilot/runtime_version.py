from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def package_version() -> str:
    try:
        return version("ps2-autopilot")
    except PackageNotFoundError:
        return "dev"
