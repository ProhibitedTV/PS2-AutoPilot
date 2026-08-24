from __future__ import annotations

import sys
import types

from ps2_autopilot.window import PCSX2Window


def test_pcsx2_executable_name_detection_supports_qt_and_legacy():
    assert PCSX2Window._looks_like_pcsx2_executable(r"C:\Program Files\PCSX2\pcsx2-qt.exe")
    assert PCSX2Window._looks_like_pcsx2_executable(r"D:\emu\pcsx2.exe")
    assert PCSX2Window._looks_like_pcsx2_executable("pcsx2-nightly.exe")
    assert not PCSX2Window._looks_like_pcsx2_executable(r"C:\Program Files\OBS\obs64.exe")
    assert not PCSX2Window._looks_like_pcsx2_executable(None)


def test_modern_pcsx2_render_window_can_attach_by_process_when_title_has_no_pcsx2(monkeypatch):
    # Modern Qt game window: title is the game, not the emulator.
    windows = {
        10: {"visible": True, "title": "Jak and Daxter - The Precursor Legacy", "pid": 4242, "rect": (0, 0, 1280, 720)},
        11: {"visible": True, "title": "Settings", "pid": 4242, "rect": (0, 0, 640, 480)},
        12: {"visible": True, "title": "Discord", "pid": 9999, "rect": (0, 0, 1600, 900)},
    }

    fake_gui = types.ModuleType("win32gui")
    fake_gui.IsWindowVisible = lambda hwnd: windows[hwnd]["visible"]
    fake_gui.GetWindowText = lambda hwnd: windows[hwnd]["title"]
    fake_gui.GetClientRect = lambda hwnd: windows[hwnd]["rect"]
    fake_gui.EnumWindows = lambda callback, arg: [callback(hwnd, arg) for hwnd in windows]

    fake_process = types.ModuleType("win32process")
    fake_process.GetWindowThreadProcessId = lambda hwnd: (1, windows[hwnd]["pid"])

    monkeypatch.setitem(sys.modules, "win32gui", fake_gui)
    monkeypatch.setitem(sys.modules, "win32process", fake_process)
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr(
        PCSX2Window,
        "_process_image_name",
        staticmethod(lambda pid: r"C:\Program Files\PCSX2\pcsx2-qt.exe" if pid == 4242 else r"C:\Discord\Discord.exe"),
    )

    window = PCSX2Window("PCSX2")
    assert window.locate() == 10
    assert window.pid == 4242


def test_largest_visible_client_window_wins_after_title_seed(monkeypatch):
    # Preserve older behavior: a PCSX2-titled launcher can seed the pid, then the
    # larger title-less render window from that same process becomes the target.
    windows = {
        20: {"visible": True, "title": "PCSX2 v2.6.3", "pid": 5151, "rect": (0, 0, 800, 600)},
        21: {"visible": True, "title": "Jak and Daxter - The Precursor Legacy", "pid": 5151, "rect": (0, 0, 1920, 1080)},
    }

    fake_gui = types.ModuleType("win32gui")
    fake_gui.IsWindowVisible = lambda hwnd: windows[hwnd]["visible"]
    fake_gui.GetWindowText = lambda hwnd: windows[hwnd]["title"]
    fake_gui.GetClientRect = lambda hwnd: windows[hwnd]["rect"]
    fake_gui.EnumWindows = lambda callback, arg: [callback(hwnd, arg) for hwnd in windows]

    fake_process = types.ModuleType("win32process")
    fake_process.GetWindowThreadProcessId = lambda hwnd: (1, windows[hwnd]["pid"])

    monkeypatch.setitem(sys.modules, "win32gui", fake_gui)
    monkeypatch.setitem(sys.modules, "win32process", fake_process)
    monkeypatch.setattr("platform.system", lambda: "Windows")

    window = PCSX2Window("PCSX2")
    assert window.locate() == 21
    assert window.pid == 5151
