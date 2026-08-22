from __future__ import annotations

from dataclasses import dataclass
import time


@dataclass(frozen=True)
class WindowRect:
    left: int
    top: int
    width: int
    height: int


class PCSX2Window:
    """Resolve the actual PCSX2 render window, not just the launcher GUI.

    Older PCSX2 builds can create a second GS/render window whose title does not
    necessarily contain "PCSX2". We first find a seed window by title, remember
    its process id, then choose the largest visible client window owned by that
    process. A light periodic rescan lets AutoPilot follow the render window when
    it appears after a game boots.
    """

    def __init__(self, title_contains: str = "PCSX2", rescan_seconds: float = 2.0) -> None:
        self.title_contains = title_contains.lower()
        self.hwnd: int | None = None
        self.pid: int | None = None
        self.rescan_seconds = max(0.25, float(rescan_seconds))
        self._last_scan = -1e9

    def _require_windows(self) -> None:
        import platform

        if platform.system() != "Windows":
            raise RuntimeError("PCSX2Window currently supports Windows only.")

    @staticmethod
    def _client_area(win32gui, hwnd: int) -> int:
        try:
            rect = win32gui.GetClientRect(hwnd)
            width = max(0, rect[2] - rect[0])
            height = max(0, rect[3] - rect[1])
            return width * height
        except Exception:
            return 0

    @staticmethod
    def _best_candidate(candidates: list[tuple[int, int]]) -> int | None:
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[1])[0]

    def _scan(self) -> int:
        self._require_windows()
        import win32gui
        import win32process

        seed_matches: list[int] = []

        def seed_callback(hwnd: int, _: object) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd).strip().lower()
            if self.title_contains in title:
                seed_matches.append(hwnd)

        win32gui.EnumWindows(seed_callback, None)
        if not seed_matches and self.pid is None:
            raise RuntimeError(
                f"Could not find a visible window containing '{self.title_contains}'."
            )

        if seed_matches:
            seed = self._best_candidate(
                [(hwnd, self._client_area(win32gui, hwnd)) for hwnd in seed_matches]
            )
            if seed is not None:
                _, self.pid = win32process.GetWindowThreadProcessId(seed)

        process_matches: list[tuple[int, int]] = []

        def process_callback(hwnd: int, _: object) -> None:
            if self.pid is None or not win32gui.IsWindowVisible(hwnd):
                return
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                return
            if pid != self.pid:
                return
            area = self._client_area(win32gui, hwnd)
            if area > 0:
                process_matches.append((hwnd, area))

        win32gui.EnumWindows(process_callback, None)
        best = self._best_candidate(process_matches)
        if best is None:
            best = self._best_candidate(
                [(hwnd, self._client_area(win32gui, hwnd)) for hwnd in seed_matches]
            )
        if best is None:
            raise RuntimeError("Found PCSX2 process but no usable visible client window.")

        self.hwnd = best
        self._last_scan = time.monotonic()
        return best

    def locate(self) -> int:
        return self._scan()

    def ensure(self) -> int:
        if self.hwnd is None:
            return self._scan()
        import win32gui

        if not win32gui.IsWindow(self.hwnd):
            return self._scan()
        if time.monotonic() - self._last_scan >= self.rescan_seconds:
            return self._scan()
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
