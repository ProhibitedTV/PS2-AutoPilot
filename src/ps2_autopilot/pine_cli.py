from __future__ import annotations

import argparse
import json
import sys

from .pine import PineClient, PineError


def _int_address(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid address {value!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ps2-autopilot-pine",
        description="Read-only diagnostic client for PCSX2 PINE IPC.",
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = PineClient(args.host, args.port, args.timeout)
    try:
        if args.command == "info":
            payload = {
                "emulator_version": client.version(),
                "game_title": client.title(),
                "game_id": client.game_id(),
                "game_crc": client.uuid(),
                "game_version": client.game_version(),
                "status": client.status(),
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0

        readers = {
            "u8": client.read8,
            "u16": client.read16,
            "u32": client.read32,
            "u64": client.read64,
            "s32": client.read_s32,
            "f32": client.read_f32,
        }
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
