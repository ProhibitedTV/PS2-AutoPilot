from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .jak_lab import check_manifest
from .pine_lab import PineSavestateClient


def _raw_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: root must be an object")
    return raw


def reset_episode(
    manifest_path: Path,
    challenge_id: str,
    *,
    allow_savestate_control: bool,
    client: PineSavestateClient | None = None,
    host: str = "127.0.0.1",
    port: int = 28011,
    timeout: float = 0.25,
) -> dict[str, Any]:
    """Request a deterministic episode reset without starting or grading the episode.

    Reset is intentionally a distinct transaction. A successful return means PCSX2
    accepted the slot-load request after all configured identity/readiness gates; it
    does not claim asynchronous restoration completed or that the subsequent episode
    passed. The caller must collect a fresh trace and grade it independently.
    """

    if not allow_savestate_control:
        raise ValueError(
            "refusing savestate mutation without explicit --allow-savestate-control"
        )

    report = check_manifest(manifest_path)
    episode = next(
        (item for item in report["episodes"] if item["challenge_id"] == challenge_id),
        None,
    )
    if episode is None:
        raise ValueError(f"challenge_id not found in lab manifest: {challenge_id}")
    if not episode["ready"]:
        blockers = ", ".join(episode["blockers"]) or "unknown"
        raise ValueError(f"challenge {challenge_id} is not reset-ready: {blockers}")
    slot = episode["pine_slot"]
    if slot is None:
        raise ValueError(f"challenge {challenge_id}: PINE slot is not configured")

    raw = _raw_manifest(manifest_path)
    expected_game_ids = list(raw.get("expected_game_ids") or [])
    expected_crcs = list(raw.get("expected_crcs") or [])
    expected_title = str(raw.get("expected_title_contains") or "")

    pine = client or PineSavestateClient(
        host=host,
        port=port,
        timeout=timeout,
        allow_savestate_control=True,
    )
    request = pine.request_load_state(
        int(slot),
        expected_game_ids=expected_game_ids,
        expected_crcs=expected_crcs,
        expected_title_contains=expected_title,
    )

    return {
        "schema": "jak-lab-reset-v1",
        "challenge_id": challenge_id,
        "manifest": str(manifest_path),
        "pine_slot": int(slot),
        "load_request": request,
        "episode_contract": {
            "max_seconds": episode["max_seconds"],
            "success_rules": episode["success_rules"],
            "failure_rules": episode["failure_rules"],
        },
        "next_step": (
            "verify the restored scene, begin a fresh retained verbose trace, execute the "
            "episode, then grade that trace with ps2-autopilot-jak-lab grade"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ps2-autopilot-jak-lab-reset",
        description=(
            "Request one safety-gated PCSX2 PINE savestate reset for a configured Jak lab episode."
        ),
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument("challenge_id")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=28011)
    parser.add_argument("--timeout", type=float, default=0.25)
    parser.add_argument("--allow-savestate-control", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = reset_episode(
            args.manifest,
            args.challenge_id,
            allow_savestate_control=args.allow_savestate_control,
            host=args.host,
            port=args.port,
            timeout=args.timeout,
        )
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
