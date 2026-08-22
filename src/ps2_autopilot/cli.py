from __future__ import annotations

import argparse
from pathlib import Path

from .app import AutopilotApp
from .config import load_config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PCSX2 livestream autopilot")
    parser.add_argument(
        "--config",
        default="config/madden2005.yaml",
        help="Path to YAML config (default: config/madden2005.yaml)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    project_root = Path.cwd()
    cfg = load_config(args.config)
    AutopilotApp(cfg, project_root).run()


if __name__ == "__main__":
    main()
