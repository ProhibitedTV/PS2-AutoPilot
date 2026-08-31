# Need for Speed: Hot Pursuit 2 — V2 research-driven profile

V2 turns the V1 road follower into a PS2-specific game policy without claiming live evidence that we do not yet have. The active profile remains `diagnostic` until retained PCSX2 runs prove the menu templates, race control and lifecycle.

## Research basis

The PS2 release is materially different from the PC/GameCube/Xbox versions and was developed by EA Black Box. The profile therefore targets PS2 menu/control semantics specifically.

Useful public references used for V2:

- GameFAQs PS2 FAQ by scurty234: menu and driving controls, Hot Pursuit/World Racing structure, options, event information.
- GameFAQs PS2 FAQ by kyleox1: root menu layout, You're The Cop controls and mode descriptions.
- PSX Data Center SLUS-20362 page: PS2 driving, camera, police and replay controls.
- Need for Speed Wiki / Neoseeker: PS2 mode and event-tree structure.
- Silent's Blog HP2 PS2 notes: confirms R3 speed boost is normally specific to You're The Cop and documents modern PCSX2 control patches.

The important policy facts are:

```text
ROOT
  Hot Pursuit
    Quick Race
    You're The Cop
    Challenge
    Ultimate Racer

  World Racing
    Quick Race
    Challenge
    Championship

  Options
```

World Racing Quick Race is the V2 default route because it removes police complexity and gives randomized race/car variety. Championship, Hot Pursuit Quick Race, Ultimate Racer and You're The Cop are also implemented as selectable routes.

## PS2 controls encoded by V2

```text
X       accelerate
Square  brake / reverse
Left    steering (analog)
R1      emergency brake / handbrake
Circle  horn; siren/target speeders in You're The Cop
R3      police speed boost
R2      roadblock or spike-strip support in You're The Cop
L2      helicopter support in You're The Cop
Start   pause; return to menu from replay
```

`VirtualGamepadController` now exposes `l3` and `r3` stick-click actions. The normal NFS config uses the virtual-gamepad backend.

## Safe menu-routing contract

V2 does **not** infer which row is selected from a generic menu template. Directional input requires a selected-row template. Generic menu templates only claim ownership and fail closed.

Capture these selected-state labels while highlighting each row:

```text
nfs_main_menu_hot_pursuit_selected
nfs_main_menu_world_racing_selected
nfs_main_menu_options_selected

nfs_world_racing_quick_race_selected
nfs_world_racing_challenge_selected
nfs_world_racing_championship_selected

nfs_hot_pursuit_quick_race_selected
nfs_hot_pursuit_cop_selected
nfs_hot_pursuit_challenge_selected
nfs_hot_pursuit_ultimate_racer_selected
```

Generic ownership labels are still useful:

```text
nfs_main_menu
nfs_world_racing_menu
nfs_hot_pursuit_menu
```

but they intentionally produce no directional input.

Additional lifecycle labels:

```text
nfs_press_start
nfs_event_select
nfs_car_select
nfs_race_setup
nfs_loading
nfs_countdown
nfs_race_hud
nfs_cop_hud
nfs_wrong_way
nfs_results
nfs_replay
nfs_pause_menu
nfs_autosave
nfs_busted
```

Use tight, stable ROIs. The local PNGs remain gitignored.

## Route planner

Set `profile.menu_route` in `config/nfs_hot_pursuit_2.yaml` to one of:

```text
world_racing_quick_race   # default
championship
hot_pursuit_quick_race
ultimate_racer
be_the_cop
```

The planner walks one row at a time using the observed selected-state template. It never emits two blind D-pad steps in one transaction.

## Race-control improvements over V1

V2 keeps the LAB-space road corridor detector and adds:

- steering reversal damping to reduce left/right oscillation;
- a race-motion stall detector that enters the bounded reverse/forward recovery sooner than the shared watchdog;
- configurable sharp-turn handbrake support using PS2 R1, disabled by default until live evidence proves it is beneficial;
- explicit replay ownership and a broadcast-preserving hold before using Start to return to menu;
- bounded pause ownership and automatic resume;
- save/busted fail-closed ownership so driving input cannot leak into those screens.

## You're The Cop

A dedicated `nfs_cop_hud` template changes driving mode to `cop`. In that mode V2 can:

- send Circle once on entry to enable siren/targeting;
- use R3 speed boost on a bounded interval when the road is confident and steering demand is moderate;
- optionally alternate R2 roadblock/spike-strip and L2 helicopter support calls.

Support calls are disabled by default because we still need live HUD evidence for availability/charge behavior.

## Telemetry added in V2

```text
nfs_policy_version
nfs_screen
nfs_route
nfs_drive_mode
nfs_menu_route_actions
nfs_fail_closed_menu_ticks
nfs_handbrake_taps
nfs_oscillation_damps
nfs_stall_recoveries
nfs_cop_siren_sent
nfs_cop_boosts
nfs_cop_support_calls
```

These supplement the V1 road confidence, center, curvature, width, steering, recovery and template telemetry.

## Recommended first live session

1. Run the branch and manually enter a World Racing Quick Race.
2. Validate road confidence and steering sign on a straight, a left bend and a right bend.
3. Let the car run long enough to expose oscillation, wall riding and stall behavior.
4. Capture the root-menu selected states and World Racing selected states.
5. Validate autonomous root -> World Racing -> Quick Race navigation.
6. Capture results/replay/autosave behavior.
7. Only after racer mode is stable, test `be_the_cop` and capture `nfs_cop_hud`.

Do not promote maturity until the active V2 policy itself earns the repository's live acceptance evidence.
