from __future__ import annotations

import argparse
from pathlib import Path
import re
import time

import cv2

from .capture import FrameGrabber
from .config import load_config
from .window import PCSX2Window


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture a PCSX2 calibration template")
    parser.add_argument("--config", default="config/madden2005.yaml")
    parser.add_argument("--label", required=True)
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()
    label = re.sub(r"[^a-z0-9_-]+", "_", args.label.strip().lower()).strip("_")
    if not label:
        raise SystemExit("label must contain letters/numbers")
    cfg = load_config(args.config)
    window = PCSX2Window(cfg.window_title_contains)
    window.focus()
    time.sleep(max(0.0, args.delay))
    frame = FrameGrabber(window).grab()
    profile_name = str(cfg.raw.get("profile", {}).get("name", "generic_chaos"))
    destination = Path("profiles") / profile_name / "templates" / f"{label}.png"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination), frame):
        raise SystemExit(f"failed to write {destination}")
    print(destination)


if __name__ == "__main__":
    main()
