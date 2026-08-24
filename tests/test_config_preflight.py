from __future__ import annotations

from pathlib import Path

import pytest

from ps2_autopilot.config import AppConfig
from ps2_autopilot.config_preflight import validate_static_config
from ps2_autopilot.doctor import main as doctor_main


def app_config(*, profile="generic_chaos", backend="keyboard", supervisor=None) -> AppConfig:
    return AppConfig(
        raw={
            "profile": {"name": profile},
            "controller": {"backend": backend},
            "supervisor": supervisor or {},
        }
    )


def failures(findings):
    return {finding.label: finding.detail for finding in findings if not finding.ok}


def test_static_preflight_accepts_safe_default_without_live_emulator(tmp_path):
    findings = validate_static_config(app_config(), tmp_path)

    assert failures(findings) == {}
    launcher = next(item for item in findings if item.label == "supervisor emulator launcher")
    assert "disabled" in launcher.detail


def test_static_preflight_reports_unknown_profile_and_controller(tmp_path):
    findings = validate_static_config(
        app_config(profile="not-real", backend="mystery-controller"),
        tmp_path,
    )
    broken = failures(findings)

    assert "registered game profile" in broken
    assert "controller backend" in broken


def test_static_preflight_reports_enabled_launcher_without_command(tmp_path):
    findings = validate_static_config(
        app_config(supervisor={"emulator": {"enabled": True}}),
        tmp_path,
    )

    broken = failures(findings)
    assert "supervisor configuration" in broken
    assert "explicit command list" in broken["supervisor configuration"]


def test_static_preflight_resolves_explicit_launcher_and_cwd(tmp_path):
    emulator_dir = tmp_path / "pcsx2"
    emulator_dir.mkdir()
    executable = emulator_dir / "pcsx2-qt.exe"
    executable.write_text("placeholder", encoding="utf-8")

    findings = validate_static_config(
        app_config(
            supervisor={
                "emulator": {
                    "enabled": True,
                    "command": [str(executable), "game.iso"],
                    "cwd": str(emulator_dir),
                    "terminate_existing_on_escalation": True,
                }
            }
        ),
        tmp_path,
        which=lambda _name: None,
    )

    assert failures(findings) == {}
    resolved = next(item for item in findings if item.label == "supervisor emulator executable")
    assert resolved.detail == str(executable)


def test_static_preflight_accepts_bare_launcher_from_path(tmp_path):
    discovered = tmp_path / "pcsx2-qt.exe"
    discovered.write_text("placeholder", encoding="utf-8")

    findings = validate_static_config(
        app_config(
            supervisor={
                "emulator": {
                    "enabled": True,
                    "command": ["pcsx2-qt.exe", "game.iso"],
                }
            }
        ),
        tmp_path,
        which=lambda name: str(discovered) if name == "pcsx2-qt.exe" else None,
    )

    assert failures(findings) == {}


def test_static_preflight_reports_missing_cwd_and_executable(tmp_path):
    findings = validate_static_config(
        app_config(
            supervisor={
                "emulator": {
                    "enabled": True,
                    "command": ["bin/pcsx2-qt.exe", "game.iso"],
                    "cwd": "missing-dir",
                }
            }
        ),
        tmp_path,
        which=lambda _name: None,
    )
    broken = failures(findings)

    assert "supervisor emulator cwd" in broken
    assert "supervisor emulator executable" in broken


def test_doctor_config_only_does_not_require_windows_or_pcsx2(tmp_path, capsys):
    config = tmp_path / "static.yaml"
    config.write_text(
        "profile:\n  name: generic_chaos\ncontroller:\n  backend: keyboard\n",
        encoding="utf-8",
    )

    doctor_main(["--config", str(config), "--config-only"])

    output = capsys.readouterr().out
    assert "static configuration looks ready" in output
    assert "Windows runtime" not in output


def test_doctor_config_only_exits_nonzero_for_invalid_static_config(tmp_path):
    config = tmp_path / "bad.yaml"
    config.write_text(
        "profile:\n  name: missing-profile\ncontroller:\n  backend: keyboard\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc:
        doctor_main(["--config", str(config), "--config-only"])

    assert exc.value.code == 1
