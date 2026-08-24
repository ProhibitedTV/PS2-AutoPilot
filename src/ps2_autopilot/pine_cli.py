from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from .jak1_semantic import Jak1GoalResolver, Jak1SemanticError
from .pcsx2_pine_config import (
    Pcsx2ConfigError,
    candidate_pcsx2_ini_paths,
    enable_pine_config,
    read_pine_config,
)
from .pine import PineClient, PineError


def _int_address(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid address {value!r}") from exc


def _watch_spec(value: str) -> tuple[str, int, str]:
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
        description="Read-only PCSX2 PINE setup, diagnostics, and Jak semantic telemetry.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=28011)
    parser.add_argument("--timeout", type=float, default=0.50)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("info", help="print emulator/game identity and status")
    sub.add_parser(
        "doctor",
        help="diagnose PINE connectivity and show local PCSX2.ini PINE state",
    )
    enable = sub.add_parser(
        "enable",
        help="safely enable PINE in PCSX2.ini; creates a timestamped backup",
    )
    enable.add_argument("--ini", type=Path, default=None)

    sub.add_parser(
        "jak-info",
        help="auto-resolve retail Jak 1 GOAL symbols and print live semantic state",
    )
    symbols = sub.add_parser(
        "symbols",
        help="auto-resolve the Jak 1 GOAL symbol table and print selected symbols",
    )
    symbols.add_argument(
        "names",
        nargs="*",
        default=["*target*", "*game-info*", "target", "game-info", "process", "trsqv"],
    )

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


def _config_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in candidate_pcsx2_ini_paths():
        try:
            state = read_pine_config(path)
            rows.append(
                {
                    "path": str(path),
                    "enable_pine": state.enabled,
                    "pine_slot": state.port,
                }
            )
        except Pcsx2ConfigError as exc:
            rows.append({"path": str(path), "error": str(exc)})
    return rows


def _doctor(args) -> int:
    payload: dict[str, object] = {
        "endpoint": f"{args.host}:{args.port}",
        "pcsx2_configs": _config_rows(),
    }
    client = PineClient(args.host, args.port, args.timeout)
    try:
        payload["identity"] = _identity(client)
        payload["connected"] = True
        payload["next_action"] = "PINE is online; run `ps2-autopilot-pine jak-info`."
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    except (OSError, PineError) as exc:
        payload["connected"] = False
        payload["error"] = str(exc)
        configs = payload["pcsx2_configs"]
        enabled = any(
            row.get("enable_pine") is True and row.get("pine_slot") == args.port
            for row in configs
            if isinstance(row, dict)
        )
        if enabled:
            payload["next_action"] = (
                "PCSX2.ini already enables this PINE slot. Restart PCSX2, then rerun doctor."
            )
        elif configs:
            payload["next_action"] = (
                "Run `ps2-autopilot-pine enable`, restart PCSX2, then rerun doctor."
            )
        else:
            payload["next_action"] = (
                "No common PCSX2.ini path was found. Run "
                "`ps2-autopilot-pine enable --ini PATH\\TO\\PCSX2.ini`, restart PCSX2, "
                "then rerun doctor."
            )
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    finally:
        client.close()


def _enable(args) -> int:
    if args.ini is not None:
        path = args.ini
    else:
        candidates = candidate_pcsx2_ini_paths()
        if not candidates:
            raise Pcsx2ConfigError(
                "No PCSX2.ini found in common locations; pass --ini PATH\\TO\\PCSX2.ini"
            )
        if len(candidates) > 1:
            choices = "\n  ".join(str(path) for path in candidates)
            raise Pcsx2ConfigError(
                "Multiple PCSX2.ini files found; choose the active one with --ini:\n  " + choices
            )
        path = candidates[0]
    changed, backup = enable_pine_config(path, port=args.port)
    state = read_pine_config(path)
    print(
        json.dumps(
            {
                "path": str(path),
                "changed": changed,
                "backup": None if backup is None else str(backup),
                "enable_pine": state.enabled,
                "pine_slot": state.port,
                "restart_required": changed,
                "next_action": (
                    "Restart PCSX2, then run `ps2-autopilot-pine doctor` followed by "
                    "`ps2-autopilot-pine jak-info`."
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "enable":
            return _enable(args)
        if args.command == "doctor":
            return _doctor(args)

        client = PineClient(args.host, args.port, args.timeout)
        try:
            if args.command == "info":
                print(json.dumps(_identity(client), indent=2, sort_keys=True))
                return 0

            if args.command == "jak-info":
                identity = _identity(client)
                semantic = Jak1GoalResolver(client).snapshot()
                print(json.dumps({**identity, **semantic}, indent=2, sort_keys=True))
                return 0

            if args.command == "symbols":
                identity = _identity(client)
                resolver = Jak1GoalResolver(client)
                symbols = resolver.build_symbol_map()
                selected = {}
                for name in args.names:
                    symbol = symbols.get(name)
                    selected[name] = None if symbol is None else {
                        "address": f"0x{symbol.address:08X}",
                        "value": f"0x{symbol.value:08X}",
                    }
                print(
                    json.dumps(
                        {
                            **identity,
                            "s7": f"0x{resolver.find_s7():08X}",
                            "symbol_count": len(symbols),
                            "symbols": selected,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                )
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
        finally:
            client.close()
    except (OSError, PineError, Pcsx2ConfigError, Jak1SemanticError) as exc:
        print(f"PINE error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
