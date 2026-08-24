from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any

from .jak1_semantic import Jak1GoalResolver, Jak1SemanticError
from .pine import PineClient, PineError


# OpenGOAL Jak 1 layout provenance (pinned during the V21 calibration pass):
# - `trsqv` ends at +136 after the basic header, trs vectors, velocity vectors,
#   dir-targ quaternion, angle-change-time, and old-y-angle-diff.
# - `collide-shape` appends process/settings/references and two uint64 collide-kind
#   fields, ending at +184.
# - `collide-shape-moving` then appends time-frame @ +184, rider-last-move @ +192,
#   trans-old[3] @ +208, three uint32 pat-surface values @ +256/+260/+264, and
#   aligns the uint64 `status` field to +272 = 0x110.
# OpenGOAL's TypeSystem::add_field_to_type aligns each field against the current
# in-memory type size. This is a relative GOAL structure offset, not a retail-build
# absolute RAM address.
CSHAPE_MOVING_STATUS_OFFSET = 0x110
CSHAPE_MOVING_STATUS_HIGH_OFFSET = CSHAPE_MOVING_STATUS_OFFSET + 4
CSHAPE_ONSURF = 1 << 0
CSHAPE_ONGROUND = 1 << 1
CSHAPE_ON_WATER = 1 << 10
KNOWN_LOW_STATUS_MASK = (1 << 30) - 1
SEMANTIC_SCHEMA = "jak1-goal-symbols-v1"
CONTACT_SCHEMA = "jak1-cshape-moving-status-v1"


class JakContactProbeError(RuntimeError):
    pass


def read_contact_fields(resolver: Jak1GoalResolver) -> dict[str, Any]:
    """Read Jak contact flags from a structurally verified collide-shape-moving root.

    The target root must prove, through the retail GOAL Type.parent chain, that its
    runtime type descends from `collide-shape-moving`. If the symbol or ancestry is
    unavailable, this fails closed instead of applying the relative offset to an
    unrelated trsqv descendant.
    """

    symbols = resolver.build_symbol_map()
    moving = symbols.get("collide-shape-moving")
    target = symbols.get("*target*")
    if moving is None:
        raise JakContactProbeError("GOAL type symbol 'collide-shape-moving' was not found")
    if target is None or not resolver._valid_ee_ptr(int(target.value)):
        raise JakContactProbeError("*target* is not a valid EE pointer")

    root_ptr, root_offset = resolver._find_target_root(int(target.value))
    if root_ptr < 4:
        raise JakContactProbeError("target root pointer is invalid")
    try:
        runtime_type = int(resolver.reader.read32(root_ptr - 4))
    except Exception as exc:
        raise JakContactProbeError("could not read target root runtime type") from exc

    descendants = resolver._type_tags_descending_from([runtime_type], int(moving.value))
    if runtime_type not in descendants:
        raise JakContactProbeError(
            "target root runtime type does not descend from collide-shape-moving"
        )

    try:
        low = int(resolver.reader.read32(root_ptr + CSHAPE_MOVING_STATUS_OFFSET)) & 0xFFFFFFFF
        high = int(resolver.reader.read32(root_ptr + CSHAPE_MOVING_STATUS_HIGH_OFFSET)) & 0xFFFFFFFF
    except Exception as exc:
        raise JakContactProbeError("could not read collide-shape-moving.status") from exc

    # The declared cshape-moving-flags occupy bits 0..29 of a uint64. A non-zero
    # high word is therefore strong evidence that the expected layout is not what
    # this runtime object contains; reject it rather than laundering a bad offset.
    if high != 0:
        raise JakContactProbeError(
            f"implausible collide-shape-moving.status high word 0x{high:08X}"
        )
    if low & ~KNOWN_LOW_STATUS_MASK:
        raise JakContactProbeError(
            f"implausible collide-shape-moving.status low word 0x{low:08X}"
        )

    status = low
    on_surface = bool(status & CSHAPE_ONSURF)
    on_ground = bool(status & CSHAPE_ONGROUND)
    on_water = bool(status & CSHAPE_ON_WATER)
    return {
        "pine_contact_schema": CONTACT_SCHEMA,
        "pine_contact_verified": True,
        "pine_contact_status_offset": CSHAPE_MOVING_STATUS_OFFSET,
        "pine_contact_root_offset": root_offset,
        "pine_contact_status": status,
        "pine_contact_status_hex": f"0x{status:016X}",
        "jak_on_surface": on_surface,
        "jak_on_ground": on_ground,
        "jak_grounded": on_ground,
        "jak_contact": on_surface,
        "jak_on_water": on_water,
    }


def sample(client: PineClient, *, expected_game_id: str | None, expected_crc: str | None) -> dict[str, Any]:
    title = client.title()
    game_id = client.game_id()
    crc = client.uuid()
    status = client.status()
    title_ok = "jak and daxter" in title.lower()
    game_id_ok = not expected_game_id or game_id.upper() == expected_game_id.upper()
    crc_ok = not expected_crc or crc.lower() == expected_crc.lower()
    identity_ok = bool(title_ok and game_id_ok and crc_ok)
    if not identity_ok:
        raise JakContactProbeError(
            "PINE identity gate failed: "
            f"title={title!r}, game_id={game_id!r}, crc={crc!r}"
        )
    if status != "running":
        raise JakContactProbeError(f"PCSX2 is not running gameplay state: {status}")

    resolver = Jak1GoalResolver(client)
    semantic = resolver.snapshot()
    if semantic.get("pine_semantic_schema") != SEMANTIC_SCHEMA:
        raise JakContactProbeError("Jak semantic resolver did not return the verified GOAL schema")
    contact = read_contact_fields(resolver)
    return {
        "timestamp": time.monotonic(),
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pine_available": True,
        "pine_verified": True,
        "pine_stale": False,
        "pine_game_title": title,
        "pine_game_id": game_id,
        "pine_game_crc": crc,
        "pine_status": status,
        **semantic,
        **contact,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ps2-autopilot-jak-contact",
        description=(
            "Record verified Jak GOAL XYZ/velocity/progression plus structurally derived "
            "collide-shape-moving contact flags."
        ),
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=28011)
    parser.add_argument("--timeout", type=float, default=0.50)
    parser.add_argument("--expected-game-id")
    parser.add_argument("--expected-crc")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval", type=float, default=0.25)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    if not 1 <= args.samples <= 100000:
        parser.error("--samples must be between 1 and 100000")
    if not 0.01 <= args.interval <= 60.0:
        parser.error("--interval must be between 0.01 and 60 seconds")

    client = PineClient(args.host, args.port, args.timeout)
    output = None
    try:
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            output = args.output.open("a", encoding="utf-8")
        for index in range(args.samples):
            row = sample(
                client,
                expected_game_id=args.expected_game_id,
                expected_crc=args.expected_crc,
            )
            line = json.dumps(row, sort_keys=True)
            print(line)
            if output is not None:
                output.write(line + "\n")
                output.flush()
            if index + 1 < args.samples:
                time.sleep(args.interval)
        return 0
    except (JakContactProbeError, Jak1SemanticError, PineError, OSError) as exc:
        parser.error(str(exc))
    finally:
        if output is not None:
            output.close()
        client.close()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
