from __future__ import annotations

from pathlib import Path
import time

import cv2

from .capture import FrameGrabber
from .config import AppConfig
from .controllers.keyboard import KeyboardController
from .controllers.virtual_gamepad import VirtualGamepadController
from .loop_health import RollingLoopHealth
from .observability import RuntimeObserver, TracingController
from .overlay import OverlayServer
from .pine import PineTelemetryBridge
from .profiles.base import ProfileContext
from .profiles.registry import build_profile, get_profile_spec
from .runtime_retention import RuntimeRetention
from .runtime_version import package_version
from .verbose_trace import VerboseRuntimeTrace
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

        performance_cfg = dict(raw.get("performance", {}))
        self.opencv_threads = max(1, int(performance_cfg.get("opencv_threads", 2)))
        cv2.setUseOptimized(True)
        cv2.setNumThreads(self.opencv_threads)
        self._last_loop_ms = 0.0
        self._loop_overruns = 0
        self.loop_health = RollingLoopHealth(
            int(performance_cfg.get("rolling_window_cycles", 120))
        )

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
        runtime_root = project_root / "runtime"
        retention_cfg = dict(raw.get("runtime_retention", {}))
        self.retention = RuntimeRetention(
            runtime_root,
            max_total_mb=float(retention_cfg.get("max_total_mb", 300.0)),
            max_failure_bundles=int(retention_cfg.get("max_failure_bundles", 30)),
            max_unknown_captures=int(retention_cfg.get("max_unknown_captures", 60)),
            prune_interval_seconds=float(retention_cfg.get("prune_interval_seconds", 60.0)),
        )
        self.observer = RuntimeObserver(observability_cfg, runtime_root)
        self.verbose_trace = VerboseRuntimeTrace(observability_cfg, runtime_root)
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

        profile_cfg = dict(raw.get("profile", {}))
        requested_name = str(profile_cfg.get("name", "generic_chaos"))
        self.profile_spec = get_profile_spec(requested_name)
        self.profile = build_profile(profile_cfg)
        self.detector = TemplateDetector(
            project_root / "profiles" / self.profile_spec.template_namespace / "templates",
            asynchronous=bool(performance_cfg.get("template_matching_async", False)),
            scan_interval_seconds=float(
                performance_cfg.get("template_scan_interval_seconds", 0.10)
            ),
            result_max_age_seconds=float(
                performance_cfg.get("template_result_max_age_seconds", 1.25)
            ),
        )

        # Optional read-only semantic side channel. The bridge is intentionally
        # identity-gated and never exposes writes; unavailable PINE simply produces
        # stale/unavailable telemetry and leaves screenshot-only profiles unchanged.
        self.semantic_bridge = PineTelemetryBridge(dict(raw.get("semantic_telemetry", {})))

        self.overlay: OverlayServer | None = None
        overlay_cfg = raw.get("overlay", {})
        if overlay_cfg.get("enabled", True):
            self.overlay = OverlayServer(
                host=str(overlay_cfg.get("host", "127.0.0.1")),
                port=int(overlay_cfg.get("port", 8765)),
                root=project_root / "overlay",
                runtime=runtime_root,
                state_hz=float(overlay_cfg.get("state_hz", 4.0)),
                index_file=str(overlay_cfg.get("index_file", "index.html")),
            )

    def _load_savestate(self) -> None:
        import pydirectinput

        self.controller.release_all()
        pydirectinput.press(self.load_state_key)

    def _identity_state(self) -> dict:
        return {
            "version": package_version(),
            "profile": self.profile.name,
            "game_id": self.profile_spec.name,
            "game_display_name": self.profile_spec.display_name,
            "profile_maturity": self.profile_spec.maturity,
        }

    def run(self) -> None:
        period = 1.0 / max(self.config.loop_hz, 1.0)
        budget_ms = period * 1000.0
        previous = None
        current_frame = None
        previous_for_ctx = None
        last_state: dict = {}
        last_action = "boot"
        decision_id: int | None = None

        if self.focus_window:
            self.window.focus()
        if self.overlay:
            self.overlay.write_state(
                {"status": "starting", **self._identity_state()}, force=True
            )
            self.overlay.start()

        print(
            f"PS2 AutoPilot v{package_version()} running "
            f"game={self.profile_spec.display_name} profile={self.profile.name} "
            f"maturity={self.profile_spec.maturity}. Ctrl+C to stop. "
            f"OpenCV threads={self.opencv_threads}.",
            flush=True,
        )
        if self.verbose_trace.enabled:
            print(
                "Verbose telemetry: runtime\\verbose.jsonl | runtime\\spatial.jsonl | "
                "runtime\\events.jsonl | runtime\\input.jsonl",
                flush=True,
            )
        try:
            while True:
                started = time.monotonic()
                decision_id = self.observer.next_decision_id()
                if isinstance(self.controller, TracingController):
                    self.controller.set_decision_id(decision_id)

                capture_started = time.perf_counter()
                current_frame = self.grabber.grab()
                capture_ms = (time.perf_counter() - capture_started) * 1000.0

                motion = motion_score(previous, current_frame)
                previous_for_ctx = previous
                previous = current_frame
                template_started = time.perf_counter()
                template = self.detector.best_match(current_frame)
                template_call_ms = (time.perf_counter() - template_started) * 1000.0
                status = self.watchdog.update(motion)
                semantic = self.semantic_bridge.poll(started)
                performance = self.loop_health.snapshot(budget_ms).as_dict()
                ctx = ProfileContext(
                    frame=current_frame,
                    motion=motion,
                    template=template,
                    now=started,
                    previous_frame=previous_for_ctx,
                    semantic=semantic,
                    performance=performance,
                )

                policy_started = time.perf_counter()
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
                policy_ms = (time.perf_counter() - policy_started) * 1000.0

                state = {
                    "status": "running",
                    **self._identity_state(),
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
                    "capture_ms": round(capture_ms, 2),
                    "template_call_ms": round(template_call_ms, 2),
                    "policy_ms": round(policy_ms, 2),
                    "last_loop_ms": round(self._last_loop_ms, 2),
                    "loop_budget_ms": round(budget_ms, 2),
                    "loop_overruns": self._loop_overruns,
                    "opencv_threads": self.opencv_threads,
                    **self.detector.telemetry(),
                    **performance,
                    **semantic,
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
                self.verbose_trace.record(decision_id, state, started)
                retention_result = self.retention.maybe_prune(started)
                if retention_result is not None and retention_result.removed_items:
                    print(
                        f"[runtime] pruned {retention_result.removed_items} old artifacts; "
                        f"size={retention_result.total_bytes / (1024 * 1024):.1f} MB",
                        flush=True,
                    )
                last_state = state
                last_action = action

                if self.overlay:
                    self.overlay.write_state(state)

                elapsed = time.monotonic() - started
                self._last_loop_ms = elapsed * 1000.0
                if elapsed > period:
                    self._loop_overruns += 1
                self.loop_health.record(
                    capture_ms=capture_ms,
                    policy_ms=policy_ms,
                    loop_ms=self._last_loop_ms,
                    budget_ms=budget_ms,
                )
                if elapsed <= period:
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
            self.detector.close()
            self.semantic_bridge.close()
            if self.overlay:
                self.overlay.write_state(
                    {"status": "stopped", **self._identity_state()}, force=True
                )
                self.overlay.stop()
