from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Callable

from .config import AppConfig
from .profiles.registry import get_profile_spec
from .supervisor import SupervisorConfig


@dataclass(frozen=True)
class PreflightFinding:
    ok: bool
    label: str
    detail: str = ""


def _resolve_cwd(project_root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path


def _resolve_executable(
    executable: str,
    *,
    project_root: Path,
    cwd: Path | None,
    which: Callable[[str], str | None],
) -> Path | None:
    raw = Path(executable).expanduser()

    if raw.is_absolute():
        return raw if raw.is_file() else None

    # Explicit relative paths belong to the configured launch cwd (or project
    # root when no cwd is supplied). Bare command names may be discovered on PATH.
    if raw.parent != Path("."):
        candidate = (cwd or project_root) / raw
        return candidate if candidate.is_file() else None

    discovered = which(executable)
    if discovered:
        path = Path(discovered)
        return path if path.is_file() else path

    candidate = (cwd or project_root) / raw
    return candidate if candidate.is_file() else None


def validate_static_config(
    config: AppConfig,
    project_root: str | Path,
    *,
    which: Callable[[str], str | None] = shutil.which,
) -> tuple[PreflightFinding, ...]:
    """Validate configuration that does not require a live emulator or GPU.

    This intentionally avoids window capture, controller device creation, OCR and
    PINE. It can therefore run before PCSX2 starts and in ordinary CI. The goal is
    to catch configuration/launcher failures before unattended supervision needs
    the recovery path for real.
    """

    root = Path(project_root)
    raw = config.raw
    findings: list[PreflightFinding] = []

    profile_cfg = dict(raw.get("profile", {}) or {})
    profile_name = str(profile_cfg.get("name", "generic_chaos"))
    try:
        spec = get_profile_spec(profile_name)
    except ValueError as exc:
        findings.append(PreflightFinding(False, "registered game profile", str(exc)))
    else:
        findings.append(
            PreflightFinding(
                True,
                "registered game profile",
                f"{spec.display_name} ({spec.maturity})",
            )
        )

    controller_cfg = dict(raw.get("controller", {}) or {})
    backend = str(controller_cfg.get("backend", "keyboard"))
    controller_ok = backend in {"keyboard", "virtual_gamepad"}
    findings.append(
        PreflightFinding(
            controller_ok,
            "controller backend",
            backend if controller_ok else f"unknown backend: {backend}",
        )
    )

    try:
        supervisor = SupervisorConfig.from_app_config(config)
    except (TypeError, ValueError) as exc:
        findings.append(PreflightFinding(False, "supervisor configuration", str(exc)))
        return tuple(findings)

    emulator = supervisor.emulator
    if not emulator.enabled:
        findings.append(
            PreflightFinding(
                True,
                "supervisor emulator launcher",
                "disabled; AutoPilot-only restart mode",
            )
        )
        return tuple(findings)

    cwd = _resolve_cwd(root, emulator.cwd)
    if cwd is None:
        findings.append(
            PreflightFinding(True, "supervisor emulator cwd", "project root")
        )
    else:
        findings.append(
            PreflightFinding(
                cwd.is_dir(),
                "supervisor emulator cwd",
                str(cwd),
            )
        )

    executable = emulator.command[0]
    resolved = _resolve_executable(
        executable,
        project_root=root,
        cwd=cwd,
        which=which,
    )
    findings.append(
        PreflightFinding(
            resolved is not None,
            "supervisor emulator executable",
            str(resolved) if resolved is not None else f"not found: {executable}",
        )
    )

    findings.append(
        PreflightFinding(
            True,
            "supervisor termination policy",
            (
                "explicitly enabled"
                if emulator.terminate_existing_on_escalation
                else "disabled; existing PCSX2 will not be killed on escalation"
            ),
        )
    )
    return tuple(findings)
