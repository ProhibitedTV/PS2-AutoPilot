# Need for Speed: Hot Pursuit 2 — V3 profile

V3 keeps the researched PS2 menu topology and police-mode controls from V2, but hardens the two areas most likely to fail during live unattended play: menu input races and corner-speed control.

## Active policy

`nfs_hot_pursuit_2` now resolves to `NfsHotPursuit2V3Profile`.

V3 remains `diagnostic` until live PCSX2 evidence proves the selected-row template corpus, a complete race loop and repeated unattended lifecycle completion.

## Menu actions are transactions

V2 knew which row was selected, but a slow/stale capture could still leave the same selected-row template visible after an input. Repeating the same D-pad action every timeout can overshoot the intended selection.

V3 changes the contract:

1. a menu row must be positively recognized for `menu_stability_frames` consecutive frames;
2. V3 emits one route action;
3. the originating row becomes a pending transaction;
4. no second input is sent while the same row remains visible during the progress window;
5. if the exact same positively recognized row still remains after the timeout, V3 may perform `menu_max_retries` bounded retries (default: one);
6. if that retry still produces no positive screen change, the row is blocked and the policy fails closed until another recognized row appears;
7. UNKNOWN or generic menu frames never authorize a retry.

This makes selected-row templates useful as visual acknowledgements rather than merely action triggers.

Relevant telemetry:

```text
nfs_raw_screen
nfs_screen_candidate
nfs_screen_candidate_frames
nfs_pending_menu_action
nfs_pending_menu_screen
nfs_pending_menu_age
nfs_pending_menu_retries
nfs_blocked_menu_screen
nfs_menu_progress_events
nfs_menu_retry_actions
nfs_menu_stalled_transactions
nfs_menu_unknown_waits
```

## One-shot lifecycle exits

Replay, pause and results screens now use one-shot exit transactions. After the configured presentation hold, V3 sends Start/Confirm once and then waits for a different positive screen semantic.

A temporary UNKNOWN frame does not reset the one-shot guard. This prevents template flicker from causing repeated Start/Confirm input.

Telemetry:

```text
nfs_replay_exit_actions
nfs_pause_resume_actions
nfs_results_confirm_actions
```

## Manual race takeover is safer

The calibration workflow still allows an operator to navigate manually into a race before menu templates are complete. However, a profile that was recently on a known menu will not immediately treat road-like moving imagery as gameplay.

When the previous phase is `main_menu` or `race_setup`, V3 requires an UNKNOWN-screen escape window (`menu_escape_takeover_seconds`, default two seconds) plus the existing repeated road-confidence evidence before entering `racing`.

Known menu semantics always beat road inference.

## Short road-dropout grace

Single-frame road segmentation failures are common around shadows, flashes, traffic and camera shake. V3 may reuse the most recent good road observation for a very short `road_grace_seconds` window when:

- the last road observation was drivable;
- current frame motion still looks like gameplay; and
- the grace window has not expired.

This is intentionally measured in fractions of a second. It is not a substitute for positive road evidence and cannot keep driving indefinitely through a lost scene.

Telemetry: `nfs_road_grace_fills`.

## Predictive steering

V3 tracks the temporal rate of change for road center and curvature. A short prediction horizon estimates where the visible corridor is moving before the bend reaches the near field.

```text
current road center/curvature
        + temporal rate
        + prediction horizon
                 ↓
predicted center / predicted curvature
                 ↓
       steering controller
```

The rate signals are smoothed to avoid amplifying one-frame noise.

Telemetry:

```text
nfs_center_rate
nfs_curvature_rate
nfs_predicted_center_x
nfs_predicted_curvature
nfs_turn_demand
```

## Coast before brake

V1/V2 mostly held full throttle until a hard turn crossed the braking threshold. V3 adds a lower `corner_coast_threshold`.

- low turn demand: hold throttle;
- moderate/high turn demand: release throttle and coast;
- severe turn demand: use the existing bounded brake pulse;
- road confidence that is technically drivable but weak: coast rather than accelerate aggressively.

R1 handbrake remains disabled by default until live evidence proves it useful.

Telemetry:

```text
nfs_coast_ticks
nfs_low_confidence_coast_ticks
nfs_edge_corrections
```

## First live validation order

1. Manually enter World Racing -> Quick Race.
2. Confirm road confidence and steering sign.
3. Watch `nfs_turn_demand`, coast/brake tags and steering through several left/right bends.
4. Tune prediction/coast thresholds only from retained evidence.
5. Capture selected-row templates.
6. Validate one menu action -> positive destination acknowledgement at a time.
7. Validate results/replay one-shot exits.
8. Run three consecutive Quick Races.
9. Only then expand to Hot Pursuit / You're The Cop and a longer soak.

Do not promote maturity from synthetic tests alone.
