from __future__ import annotations

from dataclasses import dataclass
import os
import time


@dataclass(frozen=True)
class WindowRect:
    left: int
    top: int
    width: int
    height: int


class PCSX2Window:
    """Resolve the active PCSX2 client/render window on Windows.

    PCSX2 1.x commonly exposed a visible title containing ``PCSX2``. Modern Qt
    builds may instead title the active render window after the running game. To
    remain compatible with both generations we seed attachment from either:

    * a visible window whose title matches ``title_contains``; or
    * a visible window owned by a ``pcsx2*.exe`` process.

    Once a process id is known, the largest visible client window owned by that
    process wins. Periodic rescans allow the resolver to follow render-window
    creation/replacement across boots, loads, fullscreen changes, and game exits.
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

    @staticmethod
    def _looks_like_pcsx2_executable(path: str | None) -> bool:
        if not path:
            return False
        name = os.path.basename(path.replace("\\", "/")).lower()
        return name.startswith("pcsx2") and name.endswith(".exe")

    @staticmethod
    def _process_image_name(pid: int) -> str | None:
        """Return a process image path using query-only Win32 access.

        ``QueryFullProcessImageNameW`` requires no VM_READ permission, which makes
        it a safer/more reliable discriminator than peeking process modules.
        Failures are intentionally soft because title matching remains a fallback.
        """

        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            open_process = kernel32.OpenProcess
            open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            open_process.restype = wintypes.HANDLE
            query_name = kernel32.QueryFullProcessImageNameW
            query_name.argtypes = (
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD),
            )
            query_name.restype = wintypes.BOOL
            close_handle = kernel32.CloseHandle
            close_handle.argtypes = (wintypes.HANDLE,)
            close_handle.restype = wintypes.BOOL

            process_query_limited_information = 0x1000
            handle = open_process(process_query_limited_information, False, int(pid))
            if not handle:
                return None
            try:
                size = wintypes.DWORD(32768)
                buffer = ctypes.create_unicode_buffer(size.value)
                if not query_name(handle, 0, buffer, ctypes.byref(size)):
                    return None
                return buffer.value
            finally:
                close_handle(handle)
        except Exception:
            return None

    def _scan(self) -> int:
        self._require_windows()
        import win32gui
        import win32process

        # Enumerate once. Modern PCSX2 may have no title containing "PCSX2" while
        # a game is running, so executable identity is a first-class seed source.
        visible: list[tuple[int, int, int, str]] = []

        def callback(hwnd: int, _: object) -> None:
            if not win32gui.IsWindowVisible(hwnd):
                return
            area = self._client_area(win32gui, hwnd)
            if area <= 0:
                return
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                return
            title = win32gui.GetWindowText(hwnd).strip().lower()
            visible.append((hwnd, area, int(pid), title))

        win32gui.EnumWindows(callback, None)

        # Preserve a still-running known PCSX2 pid across title/render changes.
        if self.pid is not None:
            known = [(hwnd, area) for hwnd, area, pid, _title in visible if pid == self.pid]
            best = self._best_candidate(known)
            if best is not None:
                self.hwnd = best
                self._last_scan = time.monotonic()
                return best
            self.pid = None

        title_matches = [
            (hwnd, area, pid)
            for hwnd, area, pid, title in visible
            if self.title_contains and self.title_contains in title
        ]

        seed_pid: int | None = None
        if title_matches:
            seed_hwnd = self._best_candidate([(hwnd, area) for hwnd, area, _pid in title_matches])
            if seed_hwnd is not None:
                seed_pid = next(pid for hwnd, _area, pid in title_matches if hwnd == seed_hwnd)

        if seed_pid is None:
            image_by_pid: dict[int, str | None] = {}
            process_matches: list[tuple[int, int, int]] = []
            for hwnd, area, pid, _title in visible:
                if pid not in image_by_pid:
                    image_by_pid[pid] = self._process_image_name(pid)
                if self._looks_like_pcsx2_executable(image_by_pid[pid]):
                    process_matches.append((hwnd, area, pid))
            seed_hwnd = self._best_candidate(
                [(hwnd, area) for hwnd, area, _pid in process_matches]
            )
            if seed_hwnd is not None:
                seed_pid = next(pid for hwnd, _area, pid in process_matches if hwnd == seed_hwnd)

        if seed_pid is None:
            raise RuntimeError(
                "Could not find a visible PCSX2 window by title or pcsx2*.exe process."
            )

        self.pid = seed_pid
        process_windows = [
            (hwnd, area) for hwnd, area, pid, _title in visible if pid == self.pid
        ]
        best = self._best_candidate(process_windows)
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
