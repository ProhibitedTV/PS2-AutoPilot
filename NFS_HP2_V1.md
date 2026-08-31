# Need for Speed: Hot Pursuit 2 — V1 profile

`nfs_hot_pursuit_2` is the first racing profile in PS2 AutoPilot. V1 is deliberately calibration-first: it implements continuous road following, steering, corner braking, bounded road-loss/wrong-way recovery, telemetry, config, tests, and a supervised launcher without pretending the full boot/menu lifecycle has already been live-proven.

## Run

```bat
bootstrap.cmd
.venv\Scripts\activate.bat
ps2-autopilot-doctor --config config\nfs_hot_pursuit_2.yaml --config-only
run-nfs24x7.cmd
```

V1 can be tested before any menu templates exist. Manually navigate PCSX2 into a normal chase-camera race. After several consecutive high-confidence road observations, the profile may enter `racing` and take controller ownership. If road evidence is not strong enough, it remains fail-closed in `calibration`.

## Controller assumptions

The default config assumes:

- Cross: accelerate
- Square: brake/reverse
- left analog stick: steering

These actions are configurable in `config/nfs_hot_pursuit_2.yaml` if the local PCSX2 mapping differs.

## Menu templates

Menu automation only acts on recognized templates. Unknown screens receive no Start/Confirm spam.

Useful labels recognized by V1 include:

```text
nfs_press_start
nfs_title
nfs_main_menu
nfs_quick_race
nfs_event_select
nfs_car_select
nfs_race_setup
nfs_loading
nfs_countdown
nfs_race_hud
nfs_gameplay
nfs_wrong_way
nfs_results
nfs_race_finish
```

The detector uses substring semantics, so numbered capture-series suffixes such as `nfs_main_menu_01` remain valid.

Capture tight, stable visual regions where possible rather than entire dynamic screens. For example:

```bat
ps2-autopilot-capture --config config\nfs_hot_pursuit_2.yaml --label nfs_press_start --roi 0.25,0.65,0.50,0.18
ps2-autopilot-capture --config config\nfs_hot_pursuit_2.yaml --label nfs_main_menu --roi 0.05,0.12,0.42,0.72
ps2-autopilot-capture --config config\nfs_hot_pursuit_2.yaml --label nfs_results --roi 0.10,0.08,0.80,0.84
```

Local PNG templates stay gitignored because they contain copyrighted game imagery.

## Road perception

V1 does not hard-code an asphalt color. Each frame samples the apparent pavement ahead of the car in LAB color space, grows the connected region with bottom-center contact, and measures its center at several look-ahead depths. The resulting observation contains:

- road confidence
- normalized road center (`-1` left, `+1` right)
- estimated curvature
- near-field corridor width
- mask coverage and center contact diagnostics

Steering combines center error and curvature, then applies temporal smoothing. A severe steering command may briefly brake before returning to throttle.

A flat/menu-like frame that color-matches itself across almost the entire lower image is rejected rather than being treated as a giant road.

## Recovery ownership

The profile overrides generic recovery. It never sends the shared Start/Confirm/Cancel unstick transaction during a race. Road loss or a recognized wrong-way HUD starts a bounded reverse/forward steering maneuver. If a coherent corridor is not reacquired afterward, the profile returns to fail-closed calibration instead of continuing random driving.

The shared motion watchdog may still arm this racing-specific recovery and may eventually use the repository's existing savestate/supervisor escalation policy.

## Telemetry

The normal runtime state feed gains:

```text
nfs_phase
nfs_road_confidence
nfs_road_center_x
nfs_road_curvature
nfs_road_width
nfs_steer
nfs_race_evidence_frames
nfs_race_entries
nfs_recoveries
nfs_recovery_reason
nfs_template
nfs_template_score
```

These are intended to make the first live calibration session diagnosable from retained evidence rather than guesswork.

## V1 acceptance boundary

V1 is registered as `diagnostic` until live evidence demonstrates at least:

1. repeatable road acquisition on multiple HP2 tracks/weather palettes;
2. stable steering for a complete race without persistent wall riding;
3. wrong-way/road-loss recovery that improves rather than worsens progress;
4. captured templates for title -> menu -> event/car setup -> loading -> race -> results;
5. repeated autonomous race lifecycle completion;
6. a supervised multi-race soak.

The first live session should focus on road metrics and steering quality before expanding menu automation.
