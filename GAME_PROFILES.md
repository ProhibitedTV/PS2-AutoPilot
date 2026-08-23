# Game Profiles

PS2 AutoPilot is a multi-game PCSX2 runtime. Game-specific behavior lives behind a registered profile; the capture, controller, supervision, retention, logging, and overlay plumbing stay shared.

## Registered games

| Profile | Game | Maturity | Config |
| --- | --- | --- | --- |
| `madden2005` | Madden NFL 2005 | soak-tested | `config/madden2005.yaml` |
| `jak_and_daxter` | Jak and Daxter: The Precursor Legacy | production-candidate | `config/jak_and_daxter.yaml` |
| `generic_chaos` | Generic smoke test | diagnostic | custom |

List the registry at any time:

```bat
ps2-autopilot --list-profiles
```

The Madden implementation remains versioned and intact. `madden2005` continues to resolve to V22; Jak V3 does not reuse or rewrite its football state machine.

## Run one game

Madden remains the backwards-compatible default:

```bat
run24x7.cmd
```

Jak has a dedicated entry point:

```bat
run-jak24x7.cmd
```

Equivalent explicit command:

```bat
run24x7.cmd config\jak_and_daxter.yaml
```

The runner clears the previous runtime session once when manually launched, then preserves current-session crash evidence across supervised restarts.

## Jak V3 production candidate

V1 established a fail-closed game-specific profile. V2 added verified boot navigation for `PRESS START` and the green-highlighted `NEW GAME` item. V3 adds the continuous runtime needed by the actual game while retaining those safety boundaries.

The default config now uses `mode: production`. Production mode has distinct on-foot, A-Grav Zoomer, Flut Flut, cannon and fishing control modes; analog movement/camera policies; short-term visual exploration memory; periodic L2/R2 progress-HUD probes; Power Cell/Orb/Scout Fly telemetry; and gameplay-only stuck recovery.

Unknown menus and story presentation remain hands-off. The original game asks the player to choose a save file after `NEW GAME`, so the first real save-file selector is intentionally still a calibration gate rather than a blind Cross. See `JAK_PRODUCTION.md` for the researched control model, safety boundaries and soak criteria.

## Calibration workflow

The production profile still supports `mode: observe` for capture sessions. Representative templates should use names that carry both phase and special-mode evidence, for example:

```bat
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_gameplay_geyser --series 5
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_cutscene --series 5
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_pause --series 3
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_death --series 3
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_gameplay_zoomer --series 5
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_gameplay_flut_flut --series 5
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_gameplay_cannon --series 5
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_gameplay_fishing --series 5
```

Local PNG templates are gitignored because they contain copyrighted game imagery.

## Why Jak is a separate policy

Madden is dominated by discrete menus, play phases, scoreboard OCR, possession inference and short bounded controller actions. Jak is a continuous third-person platformer with control semantics that change for vehicles, animals and machines. Its perception/policy layers therefore remain Jak-specific:

- traversable-space, ledge and hazard estimation
- continuous locomotion and camera steering
- jump/roll-jump timing
- enemy perception and attack/evasion policy
- persistent collectible/objective progress
- death/checkpoint recovery
- cutscene/dialog preservation
- anti-loop exploration memory
- mode-specific policies for Zoomer, Flut Flut, cannon and minigames

Those systems belong in Jak modules rather than conditionals inside the Madden stack.
