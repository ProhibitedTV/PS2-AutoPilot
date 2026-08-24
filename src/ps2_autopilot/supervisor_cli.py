from __future__ import annotations

import argparse
from pathlib import Path

from .config import load_config
from .supervisor_escalation import EscalatingAutopilotSupervisor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Supervise PS2 AutoPilot and optionally relaunch PCSX2"
    )
    parser.add_argument(
        "--config",
        default="config/madden2005.yaml",
        help="Path to game/runtime YAML config",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    project_root = Path.cwd()
    config_path = Path(args.config)
    cfg = load_config(config_path)
    supervisor = EscalatingAutopilotSupervisor(config_path, cfg, project_root)
    raise SystemExit(supervisor.run())


if __name__ == "__main__":
    main()
