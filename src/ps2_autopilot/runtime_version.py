from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from . import __version__


def package_version() -> str:
    try:
        installed = version("ps2-autopilot")
    except PackageNotFoundError:
        return __version__
    # Editable installs remain linked to source after ``git pull`` but their package
    # metadata is not regenerated. Prefer the moving source version so runtime logs
    # identify the policy actually executing rather than yesterday's wheel metadata.
    return __version__ if installed != __version__ else installed
