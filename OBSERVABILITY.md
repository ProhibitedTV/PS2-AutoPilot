# PS2 AutoPilot observability

PS2 AutoPilot uses two complementary logging layers for unattended Madden runs: a concise event/input black box and a denser, bounded v0.6 telemetry trace. The goal is to reconstruct what AutoPilot believed, which controller command it issued, what spatial evidence it had, and what happened next without writing one JSON document for every 12 Hz video frame.

## Runtime files

All runtime artifacts are local and gitignored.

```text
runtime/
  events.jsonl
  heartbeat.jsonl
  input.jsonl
  verbose.jsonl
  spatial.jsonl
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

### `verbose.jsonl`

v0.6 writes the complete runtime telemetry state once per second by default. This includes semantic state, OCR, gameplay policy, recovery counters, presentation state, and the latest spatial evidence. It is the first file to inspect when a live failure needs more context than `events.jsonl` contains.

### `spatial.jsonl`

A focused localization trace written every 0.75 seconds by default while spatial vision is active. Rows include:

- stabilized player-candidate coordinates/confidence
- likely controlled-player marker evidence when available
- ball hypothesis coordinates/confidence
- action-target coordinates/confidence
- open-space steering estimate/confidence
- active spatial policy mode/reason
- spatial processing time

Spatial output is always confidence-gated. Low-confidence vision is evidence for debugging, not permission to send risky controller inputs.

### `errors.log`

Unhandled Python exceptions and tracebacks. `run24x7.cmd` can then restart the process while preserving the postmortem evidence.

### `failures/`

Semantic recoveries, menu-transaction failures, unknown-state escalation, and exceptions can freeze a diagnostic bundle containing the current/previous frame plus recent decisions and controller inputs.

Failure bundles are capped by `max_failure_bundles` so they cannot grow forever.

## Live console trace

The normal observer prints meaningful semantic/action changes. v0.6 additionally prints a spatial health line every two seconds during field phases when verbose console output is enabled.

```text
[19:42:04] PLAYCALL   PLAYCALL 0.94 | playcall: cross (defense)
[19:42:11] LIVE       UNKNOWN 0.29  | defense: switch nearest ball
[19:42:12] SPATIAL LIVE      role=DEFENSE:0.96 players=14 ball=0.61 target=(+0.18,-0.22)/0.67 open=-0.36/0.55 mode=defense-target cpu=4.8ms
```

This makes it possible to watch the bot's perception quality while the stream is live without opening the JSON files.

## Tail the traces

Meaningful decisions:

```bat
ps2-autopilot-log --last 60 --follow
```

Controller audit:

```bat
ps2-autopilot-log --inputs --last 80 --follow
```

Dense one-second telemetry:

```bat
ps2-autopilot-log --verbose --last 60 --follow
```

Spatial localization only:

```bat
ps2-autopilot-log --spatial --last 80 --follow
```

All streams share `decision_id`, so a suspicious controller command can be matched back to the semantic decision and the spatial evidence that existed at that moment.

## Session report

```bat
ps2-autopilot-report
```

The report summarizes observed uptime, games started/completed, semantic and hard recoveries, unknown captures, failure-bundle count, controller-command counts, common decisions/transitions, and spatial diagnostics including average player candidates, ball/target lock rate, policy overrides, and average spatial processing cost.

## Configuration

`config/madden2005.yaml` contains the defaults:

```yaml
observability:
  enabled: true
  console: true
  verbose_console: true
  heartbeat_seconds: 5.0
  verbose_log_seconds: 1.0
  spatial_log_seconds: 0.75
  spatial_console_seconds: 2.0
  history_size: 240
  input_history_size: 300
  max_log_bytes: 8000000
  failure_bundle_cooldown_seconds: 8.0
  max_failure_bundles: 80
  stick_log_interval_seconds: 0.50
  stick_log_delta: 0.08
```

JSONL logs rotate to a `.1` file at the configured size. Unknown screenshots and failure bundles have independent retention caps.

## Why not log every frame?

At 12 Hz, a single day contains more than one million loop iterations. Full frame-level JSON would make the useful evidence harder to inspect and would create unnecessary disk churn. AutoPilot therefore records:

- every meaningful semantic/action change
- every discrete controller command
- sampled analog commands
- a compact periodic heartbeat
- a full telemetry snapshot every second
- a focused spatial snapshot about once per second
- full frame evidence only around failures

That is intentionally verbose enough for live development while remaining realistic for multi-day unattended operation.
