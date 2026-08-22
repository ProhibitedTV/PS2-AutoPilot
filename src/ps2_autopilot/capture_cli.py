from __future__ import annotations

import argparse
from pathlib import Path
import re
import time

import cv2
import numpy as np

from .config import load_config


def parse_roi(value: str) -> tuple[float, float, float, float]:
    """Parse normalized x,y,width,height ROI."""
    try:
        parts = tuple(float(part.strip()) for part in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("ROI must be x,y,width,height using 0..1 fractions") from exc
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("ROI must have exactly four comma-separated values")
    x, y, w, h = parts
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > 1 or y + h > 1:
        raise argparse.ArgumentTypeError("ROI must fit inside normalized frame bounds 0..1")
    return x, y, w, h


def crop_roi(frame: np.ndarray, roi: tuple[float, float, float, float] | None) -> np.ndarray:
    if roi is None:
        return frame
    x, y, w, h = roi
    height, width = frame.shape[:2]
    x0, y0 = int(round(x * width)), int(round(y * height))
    x1, y1 = int(round((x + w) * width)), int(round((y + h) * height))
    return frame[y0:y1, x0:x1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture PCSX2 calibration templates")
    parser.add_argument("--config", default="config/madden2005.yaml")
    parser.add_argument("--label", required=True)
    parser.add_argument("--delay", type=float, default=1.0, help="Delay before first capture")
    parser.add_argument("--series", type=int, default=1, help="Capture N samples")
    parser.add_argument("--interval", type=float, default=0.35, help="Seconds between series samples")
    parser.add_argument(
        "--roi",
        type=parse_roi,
        default=None,
        metavar="X,Y,W,H",
        help="Optional normalized crop. Example: 0.0,0.55,1.0,0.45",
    )
    args = parser.parse_args()

    label = re.sub(r"[^a-z0-9_-]+", "_", args.label.strip().lower()).strip("_")
    if not label:
        raise SystemExit("label must contain letters/numbers")
    if args.series < 1 or args.series > 50:
        raise SystemExit("--series must be between 1 and 50")

    from .capture import FrameGrabber
    from .window import PCSX2Window

    cfg = load_config(args.config)
    window = PCSX2Window(cfg.window_title_contains)
    window.focus()
    grabber = FrameGrabber(window)
    time.sleep(max(0.0, args.delay))

    profile_name = str(cfg.raw.get("profile", {}).get("name", "generic_chaos"))
    destination_dir = Path("profiles") / profile_name / "templates"
    destination_dir.mkdir(parents=True, exist_ok=True)

    for index in range(args.series):
        frame = crop_roi(grabber.grab(), args.roi)
        suffix = "" if args.series == 1 else f"_{index + 1:02d}"
        destination = destination_dir / f"{label}{suffix}.png"
        if not cv2.imwrite(str(destination), frame):
            raise SystemExit(f"failed to write {destination}")
        print(destination)
        if index + 1 < args.series:
            time.sleep(max(0.0, args.interval))


if __name__ == "__main__":
    main()
