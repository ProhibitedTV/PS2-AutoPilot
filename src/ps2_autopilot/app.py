from __future__ import annotations

from pathlib import Path
import time

from .capture import FrameGrabber
from .config import AppConfig
from .controllers.keyboard import KeyboardController
from .controllers.virtual_gamepad import VirtualGamepadController
from .observability import RuntimeObserver, TracingController
from .overlay import OverlayServer
from .profiles import GenericChaosProfile, Madden2005Profile
from .profiles.base import ProfileContext
from .vision import TemplateDetector, motion_score
from .watchdog import MotionWatchdog
from .window import PCSX2Window


DEFAULT_KEYS = {
    "confirm": "x",
    "cancel": "a",
    "start": "enter",
    "select": "backspace",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "l1": "q",
    "r1": "e",
    "l2": "1",
    "r2": "3",
    "triangle": "a",
    "square": "s",
    "circle": "z",
    "cross": "x",
}


class AutopilotApp:
    def __init__(self, config: AppConfig, project_root: Path) -> None:
        self.config = config
        raw = config.raw
        self.window = PCSX2Window(config.window_title_contains)
        self.grabber = FrameGrabber(self.window)

        controller_cfg = raw.get("controller", {})
        backend = str(controller_cfg.get("backend", "keyboard"))
        if backend == "virtual_gamepad":
            base_controller = VirtualGamepadController()
        elif backend == "keyboard":
            keys = DEFAULT_KEYS | dict(controller_cfg.get("keys", {}))
            base_controller = KeyboardController(keys)
        else:
            raise RuntimeError(f"Unknown controller backend: {backend}")
        self.focus_window = bool(controller_cfg.get("focus_window", True))

        observability_cfg = dict(raw.get("observability", {}))
        self.observer = RuntimeObserver(observability_cfg, project_root / "runtime")
        if self.observer.enabled:
            self.controller = TracingController(
                base_controller,
                self.observer.record_input,
                stick_interval_seconds=float(
                    observability_cfg.get("stick_log_interval_seconds", 0.50)
                ),
                stick_delta=float(observability_cfg.get("stick_log_delta", 0.08)),
            )
        else:
            self.controller = base_controller

        wd = raw.get("watchdog", {})
        self.watchdog = MotionWatchdog(
            threshold=float(wd.get("motion_threshold", 0.012)),
            stuck_seconds=float(wd.get("stuck_seconds", 20)),
            cooldown_seconds=float(wd.get("recovery_cooldown_seconds", 8)),
        )
        self.reload_after = int(wd.get("reload_savestate_after_recoveries", 3))
        self.load_state_key = str(wd.get("load_state_key", "f3"))

        profile_cfg = raw.get("profile", {})
        name = str(profile_cfg.get("name", "generic_chaos"))
        if name == "generic_chaos":
            self.profile = GenericChaosProfile(float(profile_cfg.get("action_seconds", 1.25)))
        elif name == "madden2005":
            self.profile = Madden2005Profile(dict(profile_cfg))
        else:
            raise RuntimeError(f"Unknown profile: {name}")

        self.detector = TemplateDetector(project_root / "profiles" / name / "templates")
        self.overlay: OverlayServer | None = None
        overlay_cfg = raw.get("overlay", {})
        if overlay_cfg.get("enabled", True):
            self.overlay = OverlayServer(
                host=str(overlay_cfg.get("host", "127.0.0.1")),
                port=int(overlay_cfg.get("port", 8765)),
                root=project_root / "overlay",
                runtime=project_root / "runtime",
            )

    def _load_savestate(self) -> None:
        import pydirectinput

        self.controller.release_all()
        pydirectinput.press(self.load_state_key)

    def run(self) -> None:
        period = 1.0 / max(self.config.loop_hz, 1.0)
        previous = None
        current_frame = None
        previous_for_ctx = None
        last_state: dict = {}
        last_action = "boot"
        decision_id: int | None = None

        if self.focus_window:
            self.window.focus()
        if self.overlay:
            self.overlay.write_state({"status": "starting"})
            self.overlay.start()

        print(f"PS2 AutoPilot running profile={self.profile.name}. Ctrl+C to stop.")
        try:
            while True:
                started = time.monotonic()
                decision_id = self.observer.next_decision_id()
                if isinstance(self.controller, TracingController):
                    self.controller.set_decision_id(decision_id)

                current_frame = self.grabber.grab()
                motion = motion_score(previous, current_frame)
                previous_for_ctx = previous
                previous = current_frame
                template = self.detector.best_match(current_frame)
                status = self.watchdog.update(motion)
                ctx = ProfileContext(
                    frame=current_frame,
                    motion=motion,
                    template=template,
                    now=started,
                    previous_frame=previous_for_ctx,
                )

                watchdog_recovery = False
                savestate_reload = False
                if status.stuck:
                    watchdog_recovery = True
                    action = self.profile.recover(self.controller)
                    self.watchdog.mark_recovery()
                    if self.watchdog.recoveries >= self.reload_after:
                        self._load_savestate()
                        self.watchdog.reset_recoveries()
                        savestate_reload = True
                        action += " -> load savestate"
                else:
                    action = self.profile.tick(self.controller, ctx)

                state = {
                    "status": "running",
                    "profile": self.profile.name,
                    "decision_id": decision_id,
                    "motion": round(motion, 4),
                    "still_seconds": round(status.still_seconds, 1),
                    "recoveries": self.watchdog.recoveries,
                    "watchdog_recovery": watchdog_recovery,
                    "savestate_reload": savestate_reload,
                    "template": None
                    if template is None
                    else {"name": template.name, "score": round(template.score, 3)},
                    "action": action,
                    "timestamp": time.time(),
                }
                telemetry = getattr(self.profile, "telemetry", None)
                if callable(telemetry):
                    state.update(telemetry(ctx))

                self.observer.record_cycle(
                    decision_id=decision_id,
                    frame=current_frame,
                    previous_frame=previous_for_ctx,
                    state=state,
                    action=action,
                    now=started,
                )
                last_state = state
                last_action = action

                if self.overlay:
                    self.overlay.write_state(state)

                elapsed = time.monotonic() - started
                if elapsed < period:
                    time.sleep(period - elapsed)
        except KeyboardInterrupt:
            print("Stopping...")
        except Exception as exc:
            self.observer.record_exception(
                exc,
                frame=current_frame,
                previous_frame=previous_for_ctx,
                state=last_state,
                decision_id=decision_id,
                action=last_action,
            )
            raise
        finally:
            self.controller.release_all()
            if self.overlay:
                self.overlay.write_state({"status": "stopped"})
                self.overlay.stop()
