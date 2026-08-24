from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
import re
import shutil


class Pcsx2ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class PineConfigState:
    path: Path
    enabled: bool | None
    port: int | None


_ENABLE_RE = re.compile(r"^\s*EnablePINE\s*=\s*(true|false)\s*$", re.IGNORECASE)
_SLOT_RE = re.compile(r"^\s*PINESlot\s*=\s*(\d+)\s*$", re.IGNORECASE)
_SECTION_RE = re.compile(r"^\s*\[([^]]+)\]\s*$")


def candidate_pcsx2_ini_paths() -> list[Path]:
    """Return existing likely PCSX2.ini paths without searching the whole disk."""

    home = Path.home()
    cwd = Path.cwd()
    roots: list[Path] = [
        cwd / "inis" / "PCSX2.ini",
        cwd / "PCSX2.ini",
        home / "Documents" / "PCSX2" / "inis" / "PCSX2.ini",
        home / "Documents" / "PCSX2" / "PCSX2.ini",
    ]
    for env_name in ("APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(env_name)
        if value:
            root = Path(value)
            roots.extend(
                [
                    root / "PCSX2" / "inis" / "PCSX2.ini",
                    root / "PCSX2" / "PCSX2.ini",
                ]
            )
    seen: set[str] = set()
    result: list[Path] = []
    for path in roots:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.is_file():
            result.append(path)
    return result


def read_pine_config(path: Path) -> PineConfigState:
    if not path.is_file():
        raise Pcsx2ConfigError(f"PCSX2 settings file not found: {path}")
    enabled: bool | None = None
    port: int | None = None
    in_emucore = False
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        section = _SECTION_RE.match(line)
        if section:
            in_emucore = section.group(1).strip().lower() == "emucore"
            continue
        if not in_emucore:
            continue
        match = _ENABLE_RE.match(line)
        if match:
            enabled = match.group(1).lower() == "true"
            continue
        match = _SLOT_RE.match(line)
        if match:
            port = int(match.group(1))
    return PineConfigState(path, enabled, port)


def enable_pine_config(path: Path, *, port: int = 28011) -> tuple[bool, Path | None]:
    """Enable PCSX2 PINE while preserving every unrelated setting.

    A timestamped backup is made before writing. Only keys inside [EmuCore] are
    changed/added. PCSX2 should be restarted after this operation so it reloads the
    settings and opens the PINE socket.
    """

    if not path.is_file():
        raise Pcsx2ConfigError(f"PCSX2 settings file not found: {path}")
    if not 1 <= int(port) <= 65535:
        raise Pcsx2ConfigError("PINE port must be between 1 and 65535")

    original = path.read_text(encoding="utf-8-sig")
    lines = original.splitlines(keepends=True)
    emucore_index: int | None = None
    emucore_end = len(lines)
    for index, line in enumerate(lines):
        section = _SECTION_RE.match(line.strip("\r\n"))
        if not section:
            continue
        if section.group(1).strip().lower() == "emucore":
            emucore_index = index
            for later in range(index + 1, len(lines)):
                if _SECTION_RE.match(lines[later].strip("\r\n")):
                    emucore_end = later
                    break
            break
    if emucore_index is None:
        raise Pcsx2ConfigError(
            "PCSX2.ini has no [EmuCore] section; refusing to guess where PINE keys belong"
        )

    newline = "\r\n" if "\r\n" in original else "\n"
    enable_index: int | None = None
    slot_index: int | None = None
    for index in range(emucore_index + 1, emucore_end):
        text = lines[index].strip("\r\n")
        if _ENABLE_RE.match(text):
            enable_index = index
        elif _SLOT_RE.match(text):
            slot_index = index

    additions: list[str] = []
    if enable_index is not None:
        ending = "\r\n" if lines[enable_index].endswith("\r\n") else "\n"
        lines[enable_index] = f"EnablePINE = true{ending}"
    else:
        additions.append(f"EnablePINE = true{newline}")
    if slot_index is not None:
        ending = "\r\n" if lines[slot_index].endswith("\r\n") else "\n"
        lines[slot_index] = f"PINESlot = {int(port)}{ending}"
    else:
        additions.append(f"PINESlot = {int(port)}{newline}")
    if additions:
        lines[emucore_end:emucore_end] = additions

    updated = "".join(lines)
    if updated == original:
        return False, None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.name}.backup_pine_{stamp}")
    shutil.copy2(path, backup)
    temporary = path.with_name(f"{path.name}.ps2-autopilot.tmp")
    temporary.write_text(updated, encoding="utf-8")
    temporary.replace(path)
    return True, backup
