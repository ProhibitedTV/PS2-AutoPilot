# Need for Speed: Hot Pursuit 2 — V4

V4 builds on the V3 evidence-stable menu controller and predictive road follower with mechanics specific to the PlayStation 2 release: traffic/roadblock hazards, pursuit-racer ownership, countdown launches, guardrail recovery escalation, BUSTED continuation and multi-suspect You're The Cop behavior.

The profile remains `diagnostic`. V4 deliberately separates features that are safe from positive template evidence from features that still need real PCSX2 calibration.

## What V4 adds

### Countdown launch

A recognized countdown now holds the configured accelerator through a bounded launch window. HP2 is an arcade racer with no clutch/false-start transaction, so this prevents the AI from giving away the first second of every race while waiting for the road detector to take ownership.

Telemetry:

```text
nfs_countdown_preload_ticks
```

### PS2 handling preset

The PS2 release exposes Classic and Extreme handling. Set the *actual in-game choice* in config:

```yaml
handling_mode: classic
```

or:

```yaml
handling_mode: extreme
```

V4 does not navigate Options blindly. The setting only selects a more conservative steering preset when Extreme handling is known to be active.

### Near-field hazard perception

`nfs_hp2_hazards.py` looks for compact non-road components inside a trapezoidal near-field corridor ahead of the car. This is intended to surface candidates such as civilian traffic, police cars, barricades and track furniture.

The detector always emits telemetry, but image-only steering is **off by default**:

```yaml
obstacle_avoid_enabled: false
```

Do not enable it until retained live footage shows that shadows, road edges, shortcuts and the player's own car are not producing harmful false positives.

Telemetry:

```text
nfs_hazard_confidence
nfs_hazard_center_x
nfs_hazard_proximity
nfs_hazard_width
nfs_hazard_candidates
nfs_hazard_avoid_ticks
nfs_hazard_brake_actions
```

### Positive roadblock / spike-strip avoidance templates

Directional templates are safer because the calibration label itself owns the desired escape direction. V4 supports labels such as:

```text
nfs_race_hud_roadblock_avoid_left
nfs_race_hud_roadblock_avoid_right
nfs_race_hud_spike_strip_avoid_left
nfs_race_hud_spike_strip_avoid_right
nfs_race_hud_helicopter_hazard_avoid_left
nfs_race_hud_helicopter_hazard_avoid_right
```

A recognized label latches a short steering bias and releases throttle while the maneuver is active. `avoid_center` is also supported and chooses the side with the best current road prediction.

These template-owned maneuvers are enabled by default; without the templates they never fire.

### Pursuit-racer mode

Templates containing `hot_pursuit_hud` or `pursuit_hud` keep the racing phase but set:

```text
nfs_drive_mode = pursuit_racer
```

This lets retained evidence distinguish ordinary World Racing from a racer being actively pursued, without confusing either with `cop` mode.

### Recovery escalation

Repeating the exact same reverse/forward maneuver can trap a racer against the same guardrail forever. V4 tracks a short recovery streak:

1. first recovery uses the normal bounded maneuver;
2. a nearby repeated recovery alternates steering side;
3. repeated attempts modestly extend reverse/forward duration up to a hard cap;
4. a coherent moving road can end the maneuver early;
5. the streak decays after sustained normal driving.

Telemetry:

```text
nfs_recovery_streak
nfs_recovery_scale
nfs_recovery_escalations
nfs_recovery_early_exits
```

### BUSTED lifecycle

A generic BUSTED screen still fails closed. A positively calibrated continuation label owns one Confirm action:

```text
nfs_busted_continue
nfs_busted_confirm
```

V4 holds the presentation for `busted_hold_seconds`, sends Confirm once, and waits for positive visual progress. It does not periodically spam Confirm through a stale BUSTED frame.

### You're The Cop: multiple suspects and resource readiness

V2 could target a speeder once, but HP2 can require a new target after an arrest. V4 supports explicit HUD evidence:

```text
nfs_cop_hud_target_needed
nfs_cop_hud_siren_off
nfs_cop_hud_next_speeder
```

Those labels authorize a bounded Circle retarget after the configured cooldown.

Automatic support calls remain opt-in:

```yaml
cop_support_enabled: false
cop_support_requires_template: true
```

When enabled with the default template requirement, V4 only presses support controls when a positively recognized HUD state says the resource is ready:

```text
nfs_cop_hud_roadblock_ready
nfs_cop_hud_spike_strip_ready
nfs_cop_hud_helicopter_ready
```

This is intentionally stricter than firing R2/L2 on a timer.

## First live V4 session

Keep the default route `world_racing_quick_race` and manually enter a race if menu templates are not ready yet.

Watch the normal V3 road metrics plus:

```text
nfs_hazard_confidence
nfs_hazard_center_x
nfs_hazard_proximity
nfs_countdown_preload_ticks
nfs_recovery_streak
nfs_recovery_scale
```

The first priority is to decide whether image-only `obstacle_avoid_enabled` is safe. It should remain false unless hazard candidates line up with real traffic/barricades and stay quiet on shadows, scenery and road boundaries.

After normal racing is stable, capture explicit roadblock/spike-strip avoidance states in Hot Pursuit and then exercise You're The Cop retarget/support readiness separately.

## V4 acceptance boundary

V4 remains `diagnostic` until retained evidence demonstrates at least:

1. World Racing Quick Race can launch and complete without harmful countdown/throttle behavior;
2. hazard telemetry is characterized across multiple tracks before image-only avoidance is enabled;
3. explicit roadblock/spike-strip avoidance labels cause the intended steering direction;
4. repeated wall/guardrail recoveries improve rather than oscillate indefinitely;
5. BUSTED continuation is one-shot and positive-evidence owned;
6. You're The Cop can retarget a subsequent suspect without Circle spam;
7. any enabled R2/L2 support call is gated by confirmed HUD readiness;
8. the selected-row menu transaction gates from V3 still pass;
9. multiple consecutive race lifecycles complete under supervision.
