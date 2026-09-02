# Need for Speed: Hot Pursuit 2 V10

V10 is the liveness follow-up to the first full overnight V9 run. The process and
controller pipeline survived for 10 hours 36 minutes, but the car remained in the
same race and accumulated 2,160 profile recoveries. Camera motion and intermittent
road-like pixels repeatedly cleared V8's instantaneous stall evidence, so the
four-recovery hard-restart threshold was never reachable.

## What changed

- Template matching is latest-frame-only and asynchronous. The 12 Hz control thread
  no longer waits for a full-resolution scan that measured roughly 1.2 seconds on the
  captured 1080p failure frame.
- Eight HUD-owned recovery starts inside a rolling two-minute window invoke the
  existing bounded Pause -> Restart Race state machine. This recovery-density signal
  survives camera wiggle and intermittent false road coherence.
- `nfs_race_entries` now counts verified launches. Temporary gameplay reacquisitions
  are reported separately as `nfs_gameplay_reacquisitions`.
- Horn showmanship requires 60 recovery-free seconds, preventing entertainment inputs
  from firing while the agent is repeatedly trying to escape.
- Runtime events and failure bundles now retain NFS recovery-storm, restart/quit and
  loop/template timing fields. The first storm, hard restart and hard quit each
  produce a diagnostic bundle without producing one bundle per normal recovery.

## Tonight's acceptance signals

The run does not need to win to validate V10. The first checkpoint is that the loop
stays near its 83 ms budget and that a recovery storm produces a hard restart instead
of another hours-long single-race trap. Useful fields in `runtime/heartbeat.jsonl`:

- `loop_p50_ms`, `loop_p95_ms`, `loop_overrun_ratio`
- `template_call_ms`, `template_scan_ms`, `template_result_age_ms`
- `nfs_verified_race_entries`, `nfs_gameplay_reacquisitions`
- `nfs_recovery_storm_count`, `nfs_recovery_storm_triggers`
- `nfs_hard_restart_attempts`, `nfs_hard_restart_successes`
- `nfs_hard_quit_attempts`, `nfs_hard_quit_successes`

The supervisor still treats PCSX2 relaunch as an explicit operator configuration.
Start PCSX2 and load the game before `run-nfs24x7.cmd` unless a verified local
`emulator_launch_command` has been configured.
