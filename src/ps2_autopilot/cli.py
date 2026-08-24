from __future__ import annotations

import argparse
from pathlib import Path

from .ort_preload import PRELOAD as _ORT_PRELOAD  # noqa: F401
from .config import load_config
from .managed_app import ManagedAutopilotApp
from .profiles.registry import list_profile_specs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PCSX2 livestream autopilot")
    parser.add_argument(
        "--config",
        default="config/madden2005.yaml",
        help="Path to YAML config (default: config/madden2005.yaml)",
    )
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="List registered game profiles and exit",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.list_profiles:
        for spec in list_profile_specs():
            print(f"{spec.name:18} {spec.maturity:12} {spec.display_name}")
        return
    project_root = Path.cwd()
    cfg = load_config(args.config)
    ManagedAutopilotApp(cfg, project_root).run()


if __name__ == "__main__":
    main()
