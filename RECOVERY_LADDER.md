# Recovery escalation ladder

PS2 AutoPilot keeps recovery ownership layered so a weak gameplay signal cannot immediately restart the emulator.

For Madden, the normal order is:

1. **Semantic/state-local recovery** — the profile uses the safest action for the known state. Examples include verified pause resume, menu backout, title-route reacquisition, play-call reset, snap probing, and presentation-safe holds.
2. **Motion-watchdog hard recovery** — if the rendered game remains physically stuck, the profile's `recover()` path is invoked. Known menu states still prefer safe navigation rather than blind confirmation.
3. **Savestate recovery** — after the configured number of hard watchdog recoveries, `AutopilotApp` owns the emulator-level F3 reload. Profiles do not press emulator hotkeys themselves.
4. **Supervisor PCSX2 restart** — `ManagedAutopilotApp` observes repeated savestate reloads inside a bounded failure window. It may request the final rung only when all of the following are true:
   - AutoPilot was launched by the Python supervisor (`PS2_AUTOPILOT_SUPERVISED=1`).
   - `supervisor.emulator.enabled` is true.
   - `supervisor.emulator.terminate_existing_on_escalation` is true.

The managed app never kills or launches PCSX2 directly. It exits with the dedicated code `75`; `EscalatingAutopilotSupervisor` promotes that exit to the supervisor's existing failure threshold. The base supervisor still owns process verification, termination, explicit argv launch, render-window readiness, telemetry, and AutoPilot restart.

## Optional tuning

The savestate-to-supervisor gate is intentionally bounded by two watchdog settings:

```yaml
watchdog:
  reload_savestate_after_recoveries: 4
  emulator_restart_after_savestate_reloads: 2
  emulator_restart_failure_window_seconds: 120
```

The latter two values default to `2` reloads within `120` seconds. They have no destructive effect unless the supervisor/emulator opt-ins above are also enabled.

A reload outside the failure window starts a new count, so successful long-running recovery does not accumulate toward a future emulator restart.

## Emulator launch configuration

Machine-specific executable and game-image paths remain local. See `SUPERVISOR.md` for the explicit argv configuration. The repository deliberately does not guess a PCSX2 installation path or game dump path.

## Validation boundary

Regression tests cover the complete ownership handoff and its safety gates, but they do not substitute for the remaining live acceptance criterion: deliberately killing the real PCSX2 process during an unattended exhibition and proving the stream returns to the exhibition lifecycle.
