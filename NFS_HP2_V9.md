# Need for Speed: Hot Pursuit 2 — V9

V9 builds on the live-evidence hardening from V6-V8. The goal is no longer merely to recognize gameplay and avoid an infinite wall park; the active profile should behave more like a durable arcade-racing agent while still being safe enough for unattended 24/7 streaming.

## What V9 changes

### HUD-backed road memory

Hot Pursuit 2 changes pavement texture, lighting and scenery aggressively enough that the LAB road segmenter can briefly lose a perfectly valid road. V3 already had a 0.22 second generic grace window. V9 adds a longer, more selective bridge:

- fixed gameplay HUD must be strongly owned;
- the scene must still have meaningful motion;
- a coherent road observation must have been seen recently;
- the remembered center/curvature decays toward straight as it ages;
- a stationary wall trap cannot use the memory path because motion is below the gate.

Default window: 0.85 seconds.

Telemetry: `nfs_hud_road_memory_fills`.

### Center-guided wall recovery

The first recovery attempts now use the most recent coherent road center. If the road was last seen to the right, the forward half of the recovery steers right; if it was last seen to the left, it steers left.

Only the first two nearby attempts trust this hint. Later attempts return to the established alternating-side escalation so stale geometry cannot repeat one bad escape forever.

Telemetry: `nfs_recovery_center_guided`.

### Restart Race acknowledgement is now post-confirm

V8 captured its visual reference before opening Pause. That meant the pause-menu animation itself could mark the restart transaction as having made visual progress even if Restart Race never happened.

V9 resets the reference after the final restart confirmation. Only a subsequent loading/countdown/gameplay change can count as restart progress.

### Terminal Quit Race escalation

A 24/7 stream needs a final escape if Restart Race itself is ineffective. After two consecutive restart attempts fail to produce post-confirm progress, V9 can arm a bounded Quit Race sequence:

1. Start opens Pause.
2. Five Down presses move from Resume Race to Quit Race using the documented PS2 pause-menu order.
3. Confirm selects Quit Race.
4. One bounded second Confirm handles a possible confirmation dialog.
5. The profile waits for visual progress and then hands ownership back to the normal unattended lifecycle.

The sequence has a cooldown and cannot loop continuously. Restart Race remains the preferred recovery; Quit Race is terminal escalation only.

Telemetry: `nfs_hard_quit_armed`, `nfs_hard_quit_stage`, `nfs_hard_quit_attempts`, `nfs_hard_quit_inputs`, `nfs_hard_quit_successes`, `nfs_hard_quit_failures`.

### Broadcast replay cadence

A stream that always holds every replay for exactly the same duration feels mechanical. V9 cycles replay presentation through 6, 10 and 14 second holds by default. The existing one-shot replay exit safety remains intact.

Telemetry: `nfs_broadcast_replay_hold`.

### Safe showmanship horn

Racer modes may occasionally tap Circle for a horn flourish when all of these conditions are true:

- the race has been stable for at least 12 seconds;
- road confidence is strong and based on a fresh real observation;
- steering and curvature are both small;
- scene motion indicates the car is moving;
- no shortcut, roadblock, police-ram or confirmed obstacle maneuver owns steering;
- at least 45 seconds have passed since the previous flourish.

You're The Cop is explicitly excluded because Circle has target/siren semantics there. V9 does not switch camera views because that would invalidate the chase-camera perception geometry.

Telemetry: `nfs_showmanship_horns`.

## Safety hierarchy

V9 keeps the existing priority structure:

```text
explicit lifecycle/menu semantic
    -> hard restart / terminal quit transaction
    -> racing recovery
    -> roadblock / spike strip / helicopter hazard
    -> police ram / pursuit evasion
    -> confirmed traffic avoidance
    -> calibrated shortcut
    -> normal predictive road following
    -> optional safe horn flourish
```

The entertainment layer never overrides a recovery, hazard or menu transaction.

## Live acceptance targets

V9 remains `diagnostic` until real PCSX2 evidence establishes the following:

- HUD road memory bridges short texture/lighting dropouts without extending wall stalls.
- Center-guided recovery improves first-attempt wall escape on both left and right barriers.
- Restart Race only reports success after actual post-confirm visual progress.
- Two failed restart attempts can reach Quit Race and return to the main lifecycle.
- Quit Race never fires during ordinary crashes or brief stalls.
- Showmanship horn occurs only on clean straights and never in cop mode.
- Replay cadence exits cleanly at all configured hold lengths.
- Cold boot -> race -> finish/replay -> next race works unattended.
- Three consecutive races complete under supervision.
- A multi-hour soak has no permanent UNKNOWN state, wall park, pause loop or runaway input stream.

Issue #129 tracks the live evidence.