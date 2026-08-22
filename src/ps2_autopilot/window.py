from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WindowRect:
    left: int
    top: int
    width: int
    height: int


class PCSX2Window:
    def __init__(self, title_contains: str = "PCSX2") -> None:
        self.title_contains = title_contains.lower()
        self.hwnd: int | None = None

    def _require_windows(self) -> None:
        import platform

        if platform.system() != "Windows":
            raise RuntimeError("PCSX2Window currently supports Windows only.")

    def locate(self) -> int:
        self._require_windows()
        import win32gui

        matches: list[int] = []

        def callback(hwnd: int, _: object) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).strip().lower()
            if self.title_contains in title:
                matches.append(hwnd)

        win32gui.EnumWindows(callback, None)
        if not matches:
            raise RuntimeError(
                f"Could not find a visible window containing '{self.title_contains}'."
            )
        self.hwnd = matches[0]
        return self.hwnd

    def ensure(self) -> int:
        if self.hwnd is None:
            return self.locate()
        import win32gui

        if not win32gui.IsWindow(self.hwnd):
            return self.locate()
        return self.hwnd

    def client_rect(self) -> WindowRect:
        import win32gui

        hwnd = self.ensure()
        left_top = win32gui.ClientToScreen(hwnd, (0, 0))
        rect = win32gui.GetClientRect(hwnd)
        width = rect[2] - rect[0]
        height = rect[3] - rect[1]
        if width <= 0 or height <= 0:
            raise RuntimeError("PCSX2 client area has invalid dimensions.")
        return WindowRect(left_top[0], left_top[1], width, height)

    def focus(self) -> None:
        import win32con
        import win32gui

        hwnd = self.ensure()
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
