# PS2 AutoPilot observability

v0.5.1 adds a structured decision trace designed for unattended Madden runs. The goal is to reconstruct what AutoPilot believed, which controller command it issued, what it expected to happen, and what happened next without logging every 12 Hz video frame.

## Runtime files

All runtime artifacts are local and gitignored.

```text
runtime/
  events.jsonl
  heartbeat.jsonl
  input.jsonl
  errors.log
  session.json
  madden-events.jsonl
  unknown/
  failures/
    20260822-184803-771/
      frame.png
      frame-before.png
      state.json
      ocr.txt
      recent-events.json
      recent-inputs.json
```

### `events.jsonl`

One row is written when the semantic state or high-level action changes. Rows carry a monotonically increasing `decision_id`, allowing them to be correlated with controller inputs.

Example shape:

```json
{
  "kind": "decision",
  "decision_id": 3921,
  "action": "playcall: cross (defense)",
  "state_changed": true,
  "state": {
    "phase": "playcall",
    "menu_screen": "playcall",
    "possession": "defense",
    "menu_confidence": 0.94,
    "ocr_text": "ENGAGE EIGHT ..."
  }
}
```

### `heartbeat.jsonl`

A compact state snapshot every five seconds by default. This is useful for long-run health checks without producing frame-rate-sized logs.

### `input.jsonl`

Every discrete button/trigger command is logged with the current `decision_id`. Analog stick commands are sampled by time/delta because recording 12 near-identical stick positions every second would create millions of low-value rows during a multi-day stream.

### `errors.log`

Unhandled Python exceptions and tracebacks. `run24x7.cmd` can then restart the process while preserving the postmortem evidence.

### `failures/`

Semantic recoveries, menu-transaction failures, unknown-state escalation, and exceptions can freeze a diagnostic bundle containing the current/previous frame plus recent decisions and controller inputs.

Failure bundles are capped by `max_failure_bundles` so they cannot grow forever.

## Live console trace

The normal AutoPilot console now prints only meaningful state/action changes rather than every loop tick.

```text
[18:52:04] PLAYCALL   PLAYCALL 0.94 | playcall: cross (defense)
[18:52:06] PRE_SNAP   UNKNOWN 0.31  | defense: cycle defender / wait snap
[18:52:11] LIVE       UNKNOWN 0.29  | defense: switch nearest ball
[18:52:18] POST_PLAY  UNKNOWN 0.28  | post-play: skip cutscene
```

## Tail the trace

After installing v0.5.1 entry points:

```bat
ps2-autopilot-log --last 60 --follow
```

Controller audit instead:

```bat
ps2-autopilot-log --inputs --last 80 --follow
```

The two logs share `decision_id`, so a suspicious controller command can be matched back to the decision/state that produced it.

## Session report

```bat
ps2-autopilot-report
```

The report summarizes observed uptime, games started/completed, semantic and hard recoveries, unknown captures, failure-bundle count, controller-command counts, common decisions, common semantic transitions, and recent failures.

## Configuration

`config/madden2005.yaml` contains the defaults:

```yaml
observability:
  enabled: true
  console: true
  heartbeat_seconds: 5.0
  history_size: 240
  input_history_size: 300
  max_log_bytes: 8000000
  failure_bundle_cooldown_seconds: 8.0
  max_failure_bundles: 80
  stick_log_interval_seconds: 0.50
  stick_log_delta: 0.08
```

`events.jsonl`, `heartbeat.jsonl`, and `input.jsonl` rotate to a `.1` file at the configured size. Unknown screenshots and failure bundles have independent retention caps.

## Why not log every frame?

At 12 Hz, a single day contains more than one million loop iterations. Full frame-level JSON would make the useful evidence harder to inspect and would create unnecessary disk churn. AutoPilot therefore records:

- every meaningful semantic/action change
- every discrete controller command
- sampled analog commands
- a periodic heartbeat
- full frame evidence only around failures

That keeps a long-running stream diagnosable without turning logging into the workload.
