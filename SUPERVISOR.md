# 24/7 supervisor

`run24x7.cmd` launches the Python supervisor rather than implementing restart logic in batch.
The supervisor keeps AutoPilot crash recovery lightweight, detects loss of the PCSX2 render
window/process, and can optionally relaunch PCSX2 plus the configured game command.

Emulator launching is **disabled by default**. Without a `supervisor` section the behavior is
conservative: AutoPilot is restarted after a nonzero exit, PCSX2 window loss is detected and
recorded, but the supervisor will not start or kill an emulator process on its own.

## Safe default

No configuration is required for ordinary AutoPilot-only supervision. The implicit defaults are:

```yaml
supervisor:
  restart_delay_seconds: 5.0
  poll_seconds: 1.0
  window_loss_grace_seconds: 8.0
  stable_run_seconds: 120.0
  emulator:
    enabled: false
    command: []
    cwd: null
    wait_seconds: 45.0
    failure_escalation_count: 3
    terminate_existing_on_escalation: false
```

`window_loss_grace_seconds` prevents a short fullscreen/window transition from immediately being
treated as PCSX2 death. `stable_run_seconds` resets the consecutive AutoPilot failure streak after
a sufficiently long successful run before the next crash.

## Enable PCSX2 relaunch

Use an explicit argv list. Do not provide one shell command string. The supervisor deliberately
uses `shell=False` semantics.

Example shape:

```yaml
supervisor:
  restart_delay_seconds: 5.0
  poll_seconds: 1.0
  window_loss_grace_seconds: 8.0
  stable_run_seconds: 120.0
  emulator:
    enabled: true
    command:
      - 'C:\\Path\\To\\PCSX2\\pcsx2-qt.exe'
      - 'C:\\Path\\To\\Your\\Game.iso'
    cwd: 'C:\\Path\\To\\PCSX2'
    wait_seconds: 45.0
    failure_escalation_count: 3
    terminate_existing_on_escalation: false
```

Replace both paths with your local installation and legally obtained game dump. Nothing in the
repository assumes a PCSX2 path, ISO path, game ID, or command-line layout.

With `enabled: true`, the supervisor can launch PCSX2 when no usable PCSX2 render/process is
visible. It waits for the modern/legacy PCSX2 window resolver to confirm a usable client window
before starting AutoPilot again.

## Repeated-failure escalation

A visible PCSX2 process is **not** terminated merely because AutoPilot repeatedly crashes.
Termination requires this separate opt-in:

```yaml
supervisor:
  emulator:
    enabled: true
    command:
      - 'C:\\Path\\To\\PCSX2\\pcsx2-qt.exe'
      - 'C:\\Path\\To\\Your\\Game.iso'
    failure_escalation_count: 3
    terminate_existing_on_escalation: true
```

When the consecutive failure threshold is reached, the supervisor records the escalation,
terminates the currently verified PCSX2 PID, launches the explicit command, waits for a new render
window, resets the failure streak after successful relaunch, and re-enters the normal game
lifecycle.

If `terminate_existing_on_escalation` remains false, the same condition is logged as
`emulator-escalation-blocked` and only AutoPilot is restarted.

## Telemetry

Supervisor state survives AutoPilot restarts:

```text
runtime/supervisor.json
runtime/supervisor.jsonl
```

Important fields include:

- `supervisor_status`
- `supervisor_autopilot_starts`
- `supervisor_autopilot_restarts`
- `supervisor_consecutive_autopilot_failures`
- `supervisor_emulator_launch_attempts`
- `supervisor_emulator_restarts`
- `supervisor_emulator_pid`
- `supervisor_last_restart_reason`
- `supervisor_emulator_launch_enabled`
- `supervisor_emulator_terminate_opt_in`

When AutoPilot is launched by the supervisor, these prefixed fields are also merged into the normal
OBS/state payload. A manually launched `ps2-autopilot` process ignores stale supervisor files.

## Stop behavior

`Ctrl+C` remains a clean stop. The existing `runtime/STOP24X7` marker is also supported and is
consumed when the supervisor exits, preserving the old runner semantics.
