# Game Profiles

PS2 AutoPilot is a multi-game PCSX2 runtime. Game-specific behavior lives behind a registered profile; capture, controller, supervision, retention, logging, optional semantic telemetry, and overlay plumbing stay shared.

## Registered games

| Profile | Game | Current policy | Maturity | Config |
| --- | --- | --- | --- | --- |
| `madden2005` | Madden NFL 2005 | `Madden2005V23Profile` | soak-tested | `config/madden2005.yaml` |
| `jak_and_daxter` | Jak and Daxter: The Precursor Legacy | `JakAndDaxterV21Profile` | production-candidate | `config/jak_and_daxter.yaml` |
| `generic_chaos` | Generic smoke test | generic | diagnostic | custom |

List the registry at any time:

```bat
ps2-autopilot --list-profiles
```

The Madden and Jak implementations remain independently versioned. `madden2005` resolves to V23 and `jak_and_daxter` resolves to V21; neither policy imports the other game's state machine.

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

The runner clears the previous runtime session once when manually launched, then preserves current-session crash evidence across supervised AutoPilot restarts. It does not yet relaunch PCSX2 itself after emulator process death.

## Validation boundaries

### Madden NFL 2005

Madden is the soak-tested profile. Live evidence has gone beyond a single successful game: an unattended soak completed seven games before exposing an EA SPORTS Bio missing-profile modal. The later V22 handler owns that distinct modal and only confirms when CANCEL is visually verified. V23 additionally prevents low-confidence defensive spatial reads from producing random tackle/strip/play-ball/rush actions; uncertain defense pursues and sprints until contact is justified.

That evidence demonstrates a functioning multi-game lifecycle, but it is not the same as final overnight/cold-restart acceptance. PCSX2 process relaunch and the broad overnight lifecycle gate remain separate open work.

### Jak and Daxter

Jak V21 has crossed the old save-file calibration boundary. Real PCSX2 sessions have exercised:

```text
PRESS START
 -> NEW GAME
 -> save/slot flow
 -> overwrite confirmation when applicable
 -> opening presentation
 -> Geyser Rock gameplay
```

The current profile includes continuous analog navigation, water/shoreline recovery, target/route validation, atomic relocation, optional read-only PINE semantics, and persistent online consequence memory. The PINE bridge can resolve the Jak 1 GOAL runtime schema on the validated retail build, but position/progression values are still subject to end-to-end live validation before every navigation layer may trust them. V21 therefore refuses XYZ learning until coordinates demonstrate plausible movement.

Jak remains a production-candidate because it has **not** yet autonomously graduated Geyser Rock or completed the planned long soaks.

## Why Jak is a separate policy

Madden is dominated by discrete menus, play phases, scoreboard OCR, possession inference and short bounded controller actions. Jak is a continuous third-person platformer with control semantics that change for vehicles, animals and machines. Its perception/policy layers therefore remain Jak-specific:

- traversable-space, ledge and hazard estimation
- continuous locomotion and camera steering
- jump/roll-jump/platforming skills
- enemy perception and attack/evasion policy
- persistent collectible/objective progress
- death/checkpoint recovery
- cutscene/dialog preservation
- persistent route consequence memory
- mode-specific policies for Zoomer, Flut Flut, cannon and minigames

Those systems belong in Jak modules rather than conditionals inside the Madden stack.

## Jak calibration workflow

The production profile still supports observation/capture workflows. Representative templates may use names that carry both phase and special-mode evidence, for example:

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
