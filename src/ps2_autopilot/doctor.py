from __future__ import annotations

import argparse
import platform
from pathlib import Path
import time

from .capture import FrameGrabber
from .config import load_config
from .madden_ocr import MaddenOCR
from .madden_vision import MaddenVision
from .vision import TemplateDetector
from .window import PCSX2Window


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate PS2 AutoPilot runtime prerequisites")
    parser.add_argument("--config", default="config/madden2005.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    raw = cfg.raw
    failures = 0

    def report(ok: bool, label: str, detail: str = "") -> None:
        nonlocal failures
        if not ok:
            failures += 1
        suffix = f" — {detail}" if detail else ""
        print(f"[{'OK' if ok else 'FAIL'}] {label}{suffix}")

    is_windows = platform.system() == "Windows"
    report(is_windows, "Windows runtime", platform.system())
    if not is_windows:
        raise SystemExit(1)

    try:
        window = PCSX2Window(cfg.window_title_contains)
        rect = window.client_rect()
        report(True, "PCSX2 render window", f"{rect.width}x{rect.height} at {rect.left},{rect.top}")
    except Exception as exc:
        report(False, "PCSX2 render window", str(exc))
        raise SystemExit(1) from exc

    try:
        frame = FrameGrabber(window).grab()
        report(True, "Frame capture", f"{frame.shape[1]}x{frame.shape[0]}")
    except Exception as exc:
        report(False, "Frame capture", str(exc))
        raise SystemExit(1) from exc

    controller_cfg = raw.get("controller", {})
    backend = str(controller_cfg.get("backend", "keyboard"))
    if backend == "virtual_gamepad":
        try:
            import vgamepad  # noqa: F401

            report(True, "vgamepad / ViGEm backend")
        except Exception as exc:
            report(False, "vgamepad / ViGEm backend", str(exc))
    else:
        report(True, "controller backend", backend)

    profile_cfg = raw.get("profile", {})
    profile_name = str(profile_cfg.get("name", "generic_chaos"))
    root = Path(__file__).resolve().parents[2]
    template_dir = root / "profiles" / profile_name / "templates"
    detector = TemplateDetector(template_dir)
    report(True, "calibration templates", f"{len(detector.templates)} loaded")

    if profile_name == "madden2005":
        vision = MaddenVision(
            field_green_threshold=float(profile_cfg.get("field_green_threshold", 0.20)),
            live_motion_threshold=float(profile_cfg.get("live_motion_threshold", 0.020)),
            template_threshold=float(profile_cfg.get("template_threshold", 0.84)),
        )
        green, center = vision.field_features(frame)
        report(True, "Madden field probe", f"green={green:.3f} center={center:+.3f}")

        ocr = MaddenOCR(
            enabled=bool(profile_cfg.get("ocr_enabled", True)),
            interval_seconds=0.2,
            min_width=int(profile_cfg.get("ocr_min_width", 960)),
            min_confidence=float(profile_cfg.get("ocr_min_confidence", 0.42)),
        )
        snapshot = ocr.read(frame, time.monotonic())
        if snapshot.available:
            preview = snapshot.text[:110] if snapshot.text else "(no readable text on current frame)"
            report(True, "RapidOCR semantic vision", preview)
        else:
            report(
                False,
                "RapidOCR semantic vision",
                snapshot.error or "install with: pip install -e \".[full]\"",
            )

    if failures:
        print(f"\nDoctor found {failures} blocking issue(s).")
        raise SystemExit(1)
    print("\nPS2 AutoPilot runtime looks ready.")


if __name__ == "__main__":
    main()
