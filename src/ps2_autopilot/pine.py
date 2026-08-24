from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import socket
import struct
import time
from typing import Any


class PineError(RuntimeError):
    pass


class PineCommand(IntEnum):
    READ8 = 0
    READ16 = 1
    READ32 = 2
    READ64 = 3
    VERSION = 8
    TITLE = 0x0B
    GAME_ID = 0x0C
    UUID = 0x0D
    GAME_VERSION = 0x0E
    STATUS = 0x0F


_STATUS_NAMES = {0: "running", 1: "paused", 2: "shutdown"}


class PineClient:
    """Tiny read-only PCSX2 PINE client.

    PINE frames are little-endian: request = u32 total_size + command payload;
    response = u32 total_size + u8 status + command results. This client deliberately
    implements only reads and identity/status commands. There are no write or savestate
    methods, so callers cannot accidentally turn semantic telemetry into a cheat path.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 28011, timeout: float = 0.05) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = max(0.01, float(timeout))
        self._sock: socket.socket | None = None

    def close(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _connect(self) -> socket.socket:
        if self._sock is not None:
            return self._sock
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
            sock.settimeout(self.timeout)
        except OSError as exc:
            raise PineError(f"PINE connect failed: {exc}") from exc
        self._sock = sock
        return sock

    @staticmethod
    def _recv_exact(sock: socket.socket, size: int) -> bytes:
        parts: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = sock.recv(remaining)
            if not chunk:
                raise PineError("PINE connection closed")
            parts.append(chunk)
            remaining -= len(chunk)
        return b"".join(parts)

    def _request(self, payload: bytes) -> bytes:
        packet = struct.pack("<I", len(payload) + 4) + payload
        try:
            sock = self._connect()
            sock.sendall(packet)
            header = self._recv_exact(sock, 4)
            size = struct.unpack("<I", header)[0]
            if size < 5 or size > 2_000_000:
                raise PineError(f"invalid PINE reply size {size}")
            body = self._recv_exact(sock, size - 4)
            if body[0] != 0:
                raise PineError(f"PINE command failed status=0x{body[0]:02x}")
            return body[1:]
        except (OSError, PineError):
            self.close()
            raise

    def _scalar(self, command: PineCommand, address: int, fmt: str) -> Any:
        data = self._request(bytes((int(command),)) + struct.pack("<I", int(address)))
        return struct.unpack_from(fmt, data, 0)[0]

    def read8(self, address: int) -> int:
        return int(self._scalar(PineCommand.READ8, address, "<B"))

    def read16(self, address: int) -> int:
        return int(self._scalar(PineCommand.READ16, address, "<H"))

    def read32(self, address: int) -> int:
        return int(self._scalar(PineCommand.READ32, address, "<I"))

    def read64(self, address: int) -> int:
        return int(self._scalar(PineCommand.READ64, address, "<Q"))

    def read_s32(self, address: int) -> int:
        return struct.unpack("<i", struct.pack("<I", self.read32(address)))[0]

    def read_f32(self, address: int) -> float:
        return float(struct.unpack("<f", struct.pack("<I", self.read32(address)))[0])

    def _string(self, command: PineCommand) -> str:
        data = self._request(bytes((int(command),)))
        if len(data) < 4:
            raise PineError("short PINE string reply")
        size = struct.unpack_from("<I", data, 0)[0]
        raw = data[4 : 4 + size]
        return raw.rstrip(b"\x00").decode("utf-8", errors="replace")

    def version(self) -> str:
        return self._string(PineCommand.VERSION)

    def title(self) -> str:
        return self._string(PineCommand.TITLE)

    def game_id(self) -> str:
        return self._string(PineCommand.GAME_ID)

    def uuid(self) -> str:
        return self._string(PineCommand.UUID)

    def game_version(self) -> str:
        return self._string(PineCommand.GAME_VERSION)

    def status(self) -> str:
        data = self._request(bytes((int(PineCommand.STATUS),)))
        value = struct.unpack_from("<I", data, 0)[0]
        return _STATUS_NAMES.get(int(value), f"unknown:{value}")


@dataclass
class PineSnapshot:
    available: bool = False
    verified: bool = False
    stale: bool = True
    error: str | None = None
    emulator_version: str | None = None
    game_title: str | None = None
    game_id: str | None = None
    game_crc: str | None = None
    game_version: str | None = None
    emulator_status: str | None = None
    fields: dict[str, Any] | None = None
    sampled_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        data = {
            "pine_available": self.available,
            "pine_verified": self.verified,
            "pine_stale": self.stale,
            "pine_error": self.error,
            "pine_emulator_version": self.emulator_version,
            "pine_game_title": self.game_title,
            "pine_game_id": self.game_id,
            "pine_game_crc": self.game_crc,
            "pine_game_version": self.game_version,
            "pine_status": self.emulator_status,
            "pine_sampled_at": self.sampled_at,
        }
        if self.fields:
            data.update(self.fields)
        return data


class PineTelemetryBridge:
    """Optional, identity-gated, read-only semantic telemetry poller."""

    def __init__(self, cfg: dict[str, Any] | None = None) -> None:
        cfg = dict(cfg or {})
        self.enabled = bool(cfg.get("enabled", False))
        self.interval = max(0.05, float(cfg.get("interval_seconds", 0.25)))
        self.identity_interval = max(1.0, float(cfg.get("identity_interval_seconds", 5.0)))
        self.retry_interval = max(0.5, float(cfg.get("retry_interval_seconds", 2.0)))
        self.expected_ids = {str(v).strip().upper() for v in cfg.get("expected_game_ids", []) if str(v).strip()}
        self.expected_crcs = {str(v).strip().lower() for v in cfg.get("expected_crcs", []) if str(v).strip()}
        self.expected_title_contains = str(cfg.get("expected_title_contains", "")).strip().lower()
        self.fields_cfg = dict(cfg.get("fields", {}))
        self.client = PineClient(
            str(cfg.get("host", "127.0.0.1")),
            int(cfg.get("port", 28011)),
            float(cfg.get("timeout_seconds", 0.05)),
        )
        self.snapshot = PineSnapshot(fields={})
        self._next_poll_at = 0.0
        self._next_identity_at = 0.0
        self._next_retry_at = 0.0

    def close(self) -> None:
        self.client.close()

    def _identity_verified(self) -> bool:
        s = self.snapshot
        gates = 0
        okay = True
        if self.expected_ids:
            gates += 1
            okay = okay and str(s.game_id or "").upper() in self.expected_ids
        if self.expected_crcs:
            gates += 1
            okay = okay and str(s.game_crc or "").lower() in self.expected_crcs
        if self.expected_title_contains:
            gates += 1
            okay = okay and self.expected_title_contains in str(s.game_title or "").lower()
        # No configured identity gate means identity telemetry is still useful, but
        # arbitrary RAM addresses are not trusted yet.
        return bool(gates > 0 and okay)

    def _refresh_identity(self, now: float) -> None:
        self.snapshot.emulator_version = self.client.version()
        self.snapshot.game_title = self.client.title()
        self.snapshot.game_id = self.client.game_id()
        self.snapshot.game_crc = self.client.uuid()
        self.snapshot.game_version = self.client.game_version()
        self.snapshot.emulator_status = self.client.status()
        self.snapshot.verified = self._identity_verified()
        self._next_identity_at = now + self.identity_interval

    def _read_field(self, spec: Any) -> Any:
        if isinstance(spec, int):
            address, kind = spec, "u32"
        else:
            spec = dict(spec)
            raw_address = spec.get("address")
            if isinstance(raw_address, str):
                address = int(raw_address, 0)
            else:
                address = int(raw_address)
            kind = str(spec.get("type", "u32")).lower()
        readers = {
            "u8": self.client.read8,
            "u16": self.client.read16,
            "u32": self.client.read32,
            "u64": self.client.read64,
            "s32": self.client.read_s32,
            "f32": self.client.read_f32,
            "bool32": lambda a: bool(self.client.read32(a)),
        }
        if kind not in readers:
            raise PineError(f"unsupported semantic field type {kind!r}")
        return readers[kind](address)

    def poll(self, now: float | None = None) -> dict[str, Any]:
        now = time.monotonic() if now is None else float(now)
        if not self.enabled:
            return {"pine_enabled": False}
        if now < self._next_poll_at:
            data = self.snapshot.as_dict()
            data["pine_enabled"] = True
            return data
        if now < self._next_retry_at:
            data = self.snapshot.as_dict()
            data["pine_enabled"] = True
            return data

        self._next_poll_at = now + self.interval
        try:
            if now >= self._next_identity_at or not self.snapshot.available:
                self._refresh_identity(now)
            else:
                self.snapshot.emulator_status = self.client.status()
            fields: dict[str, Any] = {}
            if self.snapshot.verified:
                for name, spec in self.fields_cfg.items():
                    fields[str(name)] = self._read_field(spec)
            self.snapshot.fields = fields
            self.snapshot.available = True
            self.snapshot.stale = False
            self.snapshot.error = None
            self.snapshot.sampled_at = now
        except (OSError, PineError, ValueError, TypeError) as exc:
            self.snapshot.available = False
            self.snapshot.stale = True
            self.snapshot.error = str(exc)
            self._next_retry_at = now + self.retry_interval
            self.client.close()

        data = self.snapshot.as_dict()
        data["pine_enabled"] = True
        return data
