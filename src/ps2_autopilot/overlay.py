from __future__ import annotations

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading


class OverlayServer:
    def __init__(self, host: str, port: int, root: Path, runtime: Path) -> None:
        self.host = host
        self.port = port
        self.root = root
        self.runtime = runtime
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.state_file = self.runtime / "state.json"
        self.server: ThreadingHTTPServer | None = None

    def write_state(self, state: dict) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(self.state_file)

    def start(self) -> None:
        root = self.root
        runtime = self.runtime

        class Handler(SimpleHTTPRequestHandler):
            def translate_path(self, path: str) -> str:
                if path == "/state.json":
                    return str(runtime / "state.json")
                clean = path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
                if not clean:
                    clean = "index.html"
                return str(root / clean)

            def log_message(self, fmt: str, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer((self.host, self.port), Handler)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
