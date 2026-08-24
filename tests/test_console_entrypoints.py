from __future__ import annotations

import importlib
from pathlib import Path
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _project_scripts() -> dict[str, str]:
    data = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return dict(data["project"]["scripts"])


def test_all_ps2_autopilot_console_entrypoints_are_importable_callables():
    scripts = _project_scripts()
    managed = {
        name: target
        for name, target in scripts.items()
        if name == "ps2-autopilot" or name.startswith("ps2-autopilot-")
    }

    assert managed, "expected at least one PS2 AutoPilot console entry point"

    failures: list[str] = []
    for name, target in sorted(managed.items()):
        module_name, separator, attribute_name = target.partition(":")
        if not separator or not module_name or not attribute_name:
            failures.append(f"{name}: invalid target {target!r}")
            continue
        try:
            module = importlib.import_module(module_name)
            entrypoint = getattr(module, attribute_name)
        except (ImportError, AttributeError) as exc:
            failures.append(f"{name}: {target} -> {type(exc).__name__}: {exc}")
            continue
        if not callable(entrypoint):
            failures.append(f"{name}: {target} is not callable")

    assert not failures, "console entrypoint contract failures:\n" + "\n".join(failures)
