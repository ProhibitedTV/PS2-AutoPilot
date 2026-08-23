from __future__ import annotations

import json
import os
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

    The live HTTP endpoint is memory-backed. ``state.json`` is still published as a
    compatibility/debug artifact, but a transient Windows file lock must never be
    able to crash the gameplay process.
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
        self._state_payload = "{}"

        # Best-effort disk-publish diagnostics. The overlay continues serving the
        # newest in-memory state even when Windows temporarily refuses os.replace.
        self.state_write_retries = 0
        self.state_write_failures = 0
        self.state_write_last_error: str | None = None

    def state_payload(self) -> str:
        with self._state_lock:
            return self._state_payload

    def _record_state_write_failure(self, exc: OSError) -> None:
        self.state_write_failures += 1
        self.state_write_last_error = f"{type(exc).__name__}: {exc}"
        # Keep the console useful during a long soak without turning a noisy file
        # locker into log spam.
        if self.state_write_failures == 1 or self.state_write_failures % 25 == 0:
            print(
                "[overlay] state.json publish skipped after transient file error; "
                "live overlay remains memory-backed "
                f"(failures={self.state_write_failures}, error={self.state_write_last_error})"
            )

    def _persist_state_file(self, payload: str) -> bool:
        # A per-process/per-thread temporary name avoids collisions with stale
        # ``state.tmp`` handles left behind by a previous supervised process.
        tmp = self.runtime / f".state-{os.getpid()}-{threading.get_ident()}.tmp"
        try:
            tmp.write_text(payload, encoding="utf-8")
            for attempt in range(3):
                try:
                    tmp.replace(self.state_file)
                    self.state_write_last_error = None
                    return True
                except PermissionError as exc:
                    self.state_write_retries += 1
                    self.state_write_last_error = f"{type(exc).__name__}: {exc}"
                    if attempt < 2:
                        # Keep the total retry budget small enough that overlay I/O
                        # cannot materially steal time from PCSX2/controller work.
                        time.sleep(0.01 * (attempt + 1))
                        continue
                    self._record_state_write_failure(exc)
                    return False
        except OSError as exc:
            self._record_state_write_failure(exc)
            return False
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def write_state(self, state: dict, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and now - self._last_state_write < self.state_interval_seconds:
            return False

        # Compact JSON matters here because this state is refreshed throughout a
        # long stream. OBS does not need pretty-printed state.
        payload = json.dumps(state, separators=(",", ":"), ensure_ascii=False, default=str)
        with self._state_lock:
            # Re-check after acquiring the lock in case two callers raced.
            now = time.monotonic()
            if not force and now - self._last_state_write < self.state_interval_seconds:
                return False

            # Update the authoritative live payload first. Disk publication is only
            # a compatibility/debug mirror and is explicitly best-effort.
            self._state_payload = payload
            self._last_state_write = now
            self._persist_state_file(payload)
        return True

    def start(self) -> None:
        root = self.root
        runtime = self.runtime
        owner = self

        class Handler(SimpleHTTPRequestHandler):
            def _serve_state(self, include_body: bool) -> None:
                body = owner.state_payload().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.end_headers()
                if include_body:
                    self.wfile.write(body)

            def do_GET(self) -> None:
                if self.path.split("?", 1)[0].split("#", 1)[0] == "/state.json":
                    self._serve_state(include_body=True)
                    return
                super().do_GET()

            def do_HEAD(self) -> None:
                if self.path.split("?", 1)[0].split("#", 1)[0] == "/state.json":
                    self._serve_state(include_body=False)
                    return
                super().do_HEAD()

            def translate_path(self, path: str) -> str:
                clean_path = path.split("?", 1)[0].split("#", 1)[0]
                if clean_path == "/state.json":
                    # GET/HEAD are served from memory above; keep this mapping for
                    # compatibility with any inherited handler behavior.
                    return str(runtime / "state.json")
                clean = clean_path.lstrip("/")
                if not clean:
                    clean = "index.html"
                return str(root / clean)

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
