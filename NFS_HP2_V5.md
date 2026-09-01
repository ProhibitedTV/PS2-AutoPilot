# Need for Speed: Hot Pursuit 2 — V5

V5 is the traffic/shortcut hardening pass for the PlayStation 2 profile. It keeps the V4 safety boundary: unknown menus fail closed, image-only obstacle steering remains opt-in, and high-risk pursuit actions require positive evidence.

## What changed

### Temporally confirmed traffic avoidance

V4 could detect a compact near-field object, but if `obstacle_avoid_enabled` was turned on a single candidate could own steering. V5 requires spatially consistent evidence across multiple frames before image-only traffic avoidance is allowed.

Default confirmation contract:

```text
candidate confidence/proximity passes threshold
  -> same approximate center for 3 frames
  -> confirmed traffic track
  -> choose a pass side
  -> latch that side for 0.75 s
```

A large center jump restarts the candidate track instead of immediately flipping the steering command. When the object disappears for the configured release interval, the track is cleared.

Important telemetry:

```text
nfs_hazard_track_streak
nfs_hazard_track_confirmed
nfs_hazard_track_center_x
nfs_hazard_track_proximity
nfs_hazard_track_direction
nfs_hazard_track_confirmations
nfs_hazard_track_releases
nfs_hazard_track_restarts
```

`obstacle_avoid_enabled` remains **false by default**. The temporal tracker is still useful while disabled because it lets us compare candidate persistence against retained frames before granting it controller authority.

## Shortcut ownership

Hot Pursuit 2 tracks contain shortcuts that can be materially faster than the ordinary road line. V5 does not try to discover them from generic scenery. A local template must explicitly encode the safe direction.

Supported naming patterns include:

```text
nfs_race_hud_shortcut_enter_left
nfs_race_hud_shortcut_enter_right
nfs_race_hud_shortcut_take_left
nfs_race_hud_shortcut_take_right
nfs_race_hud_shortcut_commit_left
nfs_race_hud_shortcut_commit_right
nfs_race_hud_shortcut_exit_left
nfs_race_hud_shortcut_exit_right
```

A recognized shortcut latches a bounded steering bias while leaving the V3/V4 predictive throttle/coast/brake controller intact. `shortcut_cancel` or `shortcut_abort` labels can clear the latch immediately.

Roadblocks, confirmed traffic avoidance, and police-ram evasions have higher priority than shortcut commitment. The profile records `nfs_shortcut_suppressed_ticks` when a shortcut is intentionally deferred to an immediate hazard.

Telemetry:

```text
nfs_shortcut_active
nfs_shortcut_label
nfs_shortcut_events
nfs_shortcut_ticks
nfs_shortcut_suppressed_ticks
```

## Pursuit-racer anti-ram templates

Hot Pursuit racer events can involve police cars trying to shove or box the player. V5 accepts explicit threat-side labels:

```text
nfs_hot_pursuit_hud_police_ram_left
nfs_hot_pursuit_hud_police_ram_right
nfs_hot_pursuit_hud_police_attack_left
nfs_hot_pursuit_hud_police_attack_right
nfs_hot_pursuit_hud_police_box_left
nfs_hot_pursuit_hud_police_box_right
```

The side in the label describes the threat location. A left-side threat therefore biases the car right. The evasive latch uses the same coast-first hazard path as calibrated roadblock avoidance and expires automatically.

Telemetry:

```text
nfs_pursuit_threat_kind
nfs_pursuit_threat_active
nfs_pursuit_threat_events
nfs_pursuit_evasion_ticks
```

## Priority order

During racing, directional ownership is deliberately ordered:

```text
1. calibrated roadblock / spike strip / helicopter hazard
2. calibrated police ram / attack / box threat
3. temporally confirmed image traffic (only if opted in)
4. calibrated shortcut line
5. normal predictive road controller
```

This prevents a shortcut or generic obstacle candidate from fighting a more urgent pursuit hazard.

## First live calibration sequence

1. Keep `obstacle_avoid_enabled: false`.
2. Run ordinary World Racing and inspect temporal hazard telemetry against retained frames with and without traffic.
3. Verify false positives from shadows, trackside signs, tunnels, scenery, and the player's own car do not form stable confirmed tracks.
4. Capture one known shortcut entry on a track and validate steering sign plus latch duration.
5. Run a Hot Pursuit racer event and collect left/right police contact situations for anti-ram templates.
6. Only after temporal traffic tracking looks reliable should image-only avoidance be enabled for a supervised test.

## Acceptance gates

V5 remains `diagnostic` until retained live evidence shows:

- repeated traffic candidates confirm only when an obstacle is actually persistent;
- pass-side latching reduces rather than increases left/right weaving;
- shortcut templates enter and exit the intended route without fighting hazard ownership;
- roadblock and anti-ram hazards preempt shortcuts cleanly;
- Hot Pursuit racer evasions expire and do not become permanent steering bias;
- the existing V3/V4 menu, countdown, results, replay, BUSTED, recovery, and You're The Cop gates still pass;
- a multi-race unattended soak contains no blind menu input, runaway hazard latch, or repeated traffic oscillation.
