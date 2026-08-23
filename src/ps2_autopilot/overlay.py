from __future__ import annotations

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import time


class OverlayServer:
    """Tiny local HTTP server for the OBS browser source.

    The gameplay loop runs much faster than a broadcast overlay needs to refresh.
    State writes are therefore rate-limited so the overlay does not turn a 12 Hz
    control loop into a 12 Hz disk-write loop. Explicit startup/shutdown writes can
    bypass the limiter with ``force=True``.
    """

    def __init__(
        self,
        host: str,
        port: int,
        root: Path,
        runtime: Path,
        state_hz: float = 4.0,
    ) -> None:
        self.host = host
        self.port = port
        self.root = root
        self.runtime = runtime
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.state_file = self.runtime / "state.json"
        self.server: ThreadingHTTPServer | None = None
        hz = max(0.5, min(10.0, float(state_hz)))
        self.state_interval_seconds = 1.0 / hz
        self._last_state_write = -1e9
        self._state_lock = threading.Lock()

    def write_state(self, state: dict, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and now - self._last_state_write < self.state_interval_seconds:
            return False

        # Compact JSON matters here because this file is rewritten throughout a
        # long stream. OBS does not need pretty-printed state.
        payload = json.dumps(state, separators=(",", ":"), ensure_ascii=False, default=str)
        with self._state_lock:
            # Re-check after acquiring the lock in case two callers raced.
            now = time.monotonic()
            if not force and now - self._last_state_write < self.state_interval_seconds:
                return False
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(self.state_file)
            self._last_state_write = now
        return True

    def start(self) -> None:
        root = self.root
        runtime = self.runtime

        class Handler(SimpleHTTPRequestHandler):
            def translate_path(self, path: str) -> str:
                clean_path = path.split("?", 1)[0].split("#", 1)[0]
                if clean_path == "/state.json":
                    return str(runtime / "state.json")
                clean = clean_path.lstrip("/")
                if not clean:
                    clean = "index.html"
                return str(root / clean)

            def end_headers(self) -> None:
                if self.path.split("?", 1)[0] == "/state.json":
                    self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Expires", "0")
                super().end_headers()

            def log_message(self, fmt: str, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.server.daemon_threads = True
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
