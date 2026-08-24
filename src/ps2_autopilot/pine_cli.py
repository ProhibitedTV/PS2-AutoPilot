from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from .pine import PineClient, PineError


def _int_address(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid address {value!r}") from exc


def _watch_spec(value: str) -> tuple[str, int, str]:
    # NAME=ADDRESS[:TYPE], for example jak_x=0x123456:f32
    if "=" not in value:
        raise argparse.ArgumentTypeError("watch fields use NAME=ADDRESS[:TYPE]")
    name, raw = value.split("=", 1)
    name = name.strip()
    if not name:
        raise argparse.ArgumentTypeError("watch field name cannot be empty")
    if ":" in raw:
        address_text, kind = raw.rsplit(":", 1)
    else:
        address_text, kind = raw, "u32"
    kind = kind.strip().lower()
    if kind not in {"u8", "u16", "u32", "u64", "s32", "f32"}:
        raise argparse.ArgumentTypeError(f"unsupported watch type {kind!r}")
    return name, _int_address(address_text.strip()), kind


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ps2-autopilot-pine",
        description="Read-only diagnostic and memory-calibration client for PCSX2 PINE IPC.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=28011)
    parser.add_argument("--timeout", type=float, default=0.08)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("info", help="print emulator/game identity and status")
    read = sub.add_parser("read", help="read one EE memory value; never writes")
    read.add_argument("address", type=_int_address)
    read.add_argument(
        "type",
        choices=("u8", "u16", "u32", "u64", "s32", "f32"),
        default="u32",
        nargs="?",
    )
    watch = sub.add_parser(
        "watch",
        help="record named candidate addresses over time as JSONL; read-only",
    )
    watch.add_argument(
        "fields",
        nargs="+",
        type=_watch_spec,
        metavar="NAME=ADDRESS[:TYPE]",
    )
    watch.add_argument("--interval", type=float, default=0.20)
    watch.add_argument("--duration", type=float, default=30.0)
    watch.add_argument("--output", type=Path, default=None)
    watch.add_argument(
        "--changes-only",
        action="store_true",
        help="emit a row only when at least one candidate value changes",
    )
    return parser


def _readers(client: PineClient):
    return {
        "u8": client.read8,
        "u16": client.read16,
        "u32": client.read32,
        "u64": client.read64,
        "s32": client.read_s32,
        "f32": client.read_f32,
    }


def _identity(client: PineClient) -> dict:
    return {
        "emulator_version": client.version(),
        "game_title": client.title(),
        "game_id": client.game_id(),
        "game_crc": client.uuid(),
        "game_version": client.game_version(),
        "status": client.status(),
    }


def _watch(client: PineClient, args) -> int:
    readers = _readers(client)
    interval = max(0.05, float(args.interval))
    duration = max(interval, float(args.duration))
    started = time.monotonic()
    deadline = started + duration
    previous: dict[str, object] | None = None
    output = None
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        output = args.output.open("w", encoding="utf-8")

    identity = _identity(client)
    header = {
        "kind": "pine_watch_header",
        "timestamp": time.time(),
        **identity,
        "fields": [
            {"name": name, "address": f"0x{address:08X}", "type": kind}
            for name, address, kind in args.fields
        ],
    }
    header_line = json.dumps(header, sort_keys=True)
    print(header_line)
    if output is not None:
        output.write(header_line + "\n")
        output.flush()

    try:
        while time.monotonic() < deadline:
            sample_started = time.monotonic()
            values: dict[str, object] = {}
            for name, address, kind in args.fields:
                values[name] = readers[kind](address)
            changed = previous is None or values != previous
            if changed or not args.changes_only:
                row = {
                    "kind": "pine_watch_sample",
                    "timestamp": time.time(),
                    "elapsed_seconds": round(sample_started - started, 4),
                    "values": values,
                }
                line = json.dumps(row, sort_keys=True)
                print(line)
                if output is not None:
                    output.write(line + "\n")
                    output.flush()
            previous = values
            elapsed = time.monotonic() - sample_started
            if elapsed < interval:
                time.sleep(interval - elapsed)
    finally:
        if output is not None:
            output.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = PineClient(args.host, args.port, args.timeout)
    try:
        if args.command == "info":
            print(json.dumps(_identity(client), indent=2, sort_keys=True))
            return 0

        if args.command == "watch":
            return _watch(client, args)

        readers = _readers(client)
        value = readers[args.type](args.address)
        print(
            json.dumps(
                {
                    "address": f"0x{args.address:08X}",
                    "type": args.type,
                    "value": value,
                },
                indent=2,
            )
        )
        return 0
    except (OSError, PineError) as exc:
        print(f"PINE error: {exc}", file=sys.stderr)
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
