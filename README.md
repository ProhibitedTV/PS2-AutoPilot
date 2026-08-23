# PS2 AutoPilot

**Windows-first, multi-game PCSX2 automation for unattended gameplay and livestreaming.**

PS2 AutoPilot captures the active PCSX2 render, interprets game-specific visual state, drives a virtual controller, records enough evidence to debug unattended failures, and publishes a lightweight state feed for OBS. The shared runtime handles capture, supervision, logging, retention, controller I/O and overlays; each game gets its own perception and policy stack.

> Current release: **v0.9.1**

## Game status

| Profile | Game | Current policy | Maturity |
| --- | --- | --- | --- |
| `madden2005` | Madden NFL 2005 | `Madden2005V22Profile` | soak-tested |
| `jak_and_daxter` | Jak and Daxter: The Precursor Legacy | `JakAndDaxterV4Profile` | production-candidate |
| `generic_chaos` | Generic controller/capture smoke test | generic | diagnostic |

Madden and Jak are deliberately isolated. Shared infrastructure is reused; football assumptions never leak into the platformer policy.

List the profiles available in your checkout:

```bat
ps2-autopilot --list-profiles
```

## Design goals

PS2 AutoPilot is built around a few rules that matter more than raw button volume:

- **Fail closed on unknown screens.** An unrecognized menu or dialog should produce evidence, not random confirms.
- **Preserve presentation.** Replays, celebrations, cutscenes and story sequences are part of the stream and should not be skipped just because they are temporarily non-interactive.
- **Use game-specific semantics.** Madden needs possession, play clocks and menu transactions; Jak needs continuous analog traversal, camera control, scene memory and platforming recovery.
- **Treat recovery as evidence-driven.** The runtime keeps screenshots, recent decisions and recent inputs around failures so each bad unattended state can become a regression test.
- **Stay broadcast-friendly.** Public overlays expose useful game state rather than internal bot/debug vocabulary; deeper telemetry remains available separately.

## Architecture

```text
                           +-----------------------+
PCSX2 render ------------> | frame capture         |
                           +-----------+-----------+
                                       |
                    +------------------+------------------+
                    |                                     |
                    v                                     v
             shared vision/OCR                    template evidence
                    |                                     |
                    +------------------+------------------+
                                       |
                                       v
                              registered game profile
                         +-------------+-------------+
                         |                           |
                    Madden V22                    Jak V4
                 football state/policy      platformer state/policy
                         |                           |
                         +-------------+-------------+
                                       |
                                       v
                               virtual controller
                                       |
                                       v
                                     PCSX2

state + inputs + frames ---> observability / failure bundles / OBS overlay
```

## Requirements

- Windows 10 or 11
- PCSX2 with your own legally obtained game dump
- Python **3.11 or 3.12** recommended for the full local OCR stack
- a virtual Xbox 360 controller through `vgamepad` / ViGEm for the normal Windows setup

PCSX2 controller port 1 should map the virtual pad like this:

| Virtual pad | PS2 |
| --- | --- |
| A | Cross |
| B | Circle |
| X | Square |
| Y | Triangle |
| LB / RB | L1 / R1 |
| LT / RT | L2 / R2 |
| Left stick | Left analog |
| Right stick | Right analog |

## Install / update

Fresh checkout:

```bat
bootstrap.cmd
```

Existing checkout after pulling a new release:

```bat
git pull --ff-only
.venv\Scripts\activate.bat
python -m pip install -e ".[full]"
```

Check the installed package version:

```bat
python -c "from importlib.metadata import version; print(version('ps2-autopilot'))"
```

## Validate the runtime

For Madden:

```bat
ps2-autopilot-doctor --config config\madden2005.yaml
```

For Jak and Daxter:

```bat
ps2-autopilot-doctor --config config\jak_and_daxter.yaml
```

The doctor checks the Windows runtime, PCSX2 window discovery/capture, controller backend, template namespace and local OCR dependencies relevant to the selected profile.

## Run a game

### Madden NFL 2005

Interactive:

```bat
ps2-autopilot --config config\madden2005.yaml
```

Supervised 24/7 wrapper:

```bat
run24x7.cmd config\madden2005.yaml
```

### Jak and Daxter: The Precursor Legacy

Interactive:

```bat
ps2-autopilot --config config\jak_and_daxter.yaml
```

Dedicated supervised wrapper:

```bat
run-jak24x7.cmd
```

`Ctrl+C` is an intentional clean shutdown. The wrapper only restarts AutoPilot after an unexpected nonzero process exit.

## Jak and Daxter production status

Jak is the second real-game integration and intentionally looks very different from Madden.

### What already works

- OCR-verified **PRESS START** boot transaction
- main-menu recognition and safe **NEW GAME** confirmation
- V4 fallback recognition for the real lime-green NEW GAME selection when OCR is incomplete
- fail-closed unknown menus/save screens
- continuous on-foot analog exploration and camera steering
- jump / double-jump behavior and anti-loop scene memory
- separate control modes for on-foot gameplay, A-Grav Zoomer, Flut Flut, cannon and fishing
- periodic progress-HUD probes with Power Cell / Precursor Orb / Scout Fly telemetry
- gameplay-only recovery ladder
- preservation of unknown presentation/cutscene states instead of generic button mashing

### Current calibration frontier

The boot path is now expected to progress:

```text
PRESS START
   -> MAIN MENU / NEW GAME
   -> SAVE-FILE SELECTOR        <-- current calibration frontier
   -> OPENING PRESENTATION
   -> GEYSER ROCK GAMEPLAY
```

The save-file selector is intentionally still fail-closed until its exact visual and selection semantics are captured from the live game. After that, the next major work is representative gameplay/cutscene/death calibration, ledge and hazard perception, and longer unattended soaks.

### Jak failure behavior

| Failure shape | Current behavior |
| --- | --- |
| main menu OCR misses one or more labels | V4 combines partial OCR with normalized lime-highlight geometry |
| static unknown screen | hold neutral; watchdog/failure evidence remains available for calibration |
| save/menu words detected after New Game | do **not** promote the frame to gameplay |
| opening presentation / unknown cinematic | preserve presentation and hold inputs |
| brief gameplay classifier miss | gameplay grace latch avoids instantly surrendering controller ownership |
| gameplay stops making progress | bounded Jak-specific recovery ladder |
| fishing/minigame state lacks dedicated perception | fail closed rather than guessing |

This distinction is important: a screenshot bundle from an unknown screen is not necessarily a crash. Often it is AutoPilot deliberately refusing to invent semantics it has not learned yet.

See [`JAK_PRODUCTION.md`](JAK_PRODUCTION.md) and [`GAME_PROFILES.md`](GAME_PROFILES.md) for the detailed Jak policy and profile boundaries.

## Madden NFL 2005 status

Madden remains the most mature integration. Its V22 stack includes deterministic menu navigation, team/matchup variety, offense/defense role inference, game-situation OCR, spatial player/ball hypotheses, deliberate passing/running/defense behavior, replay/presentation preservation, postgame lifecycle handling and unattended supervision.

The versioned Madden modules remain in the repository so improvements to later games do not erase the working football stack.

## Runtime evidence and debugging

Runtime artifacts are written under the gitignored `runtime/` directory. Depending on the active profile and event type you may see:

```text
runtime/
  state.json
  session.json
  events.jsonl
  input.jsonl
  verbose.jsonl
  spatial.jsonl
  failures/
    <timestamp>/
      frame.png
      frame-before.png
      state.json
      recent-events.json
      recent-inputs.json
```

Useful live views:

```bat
ps2-autopilot-log --last 80 --follow
ps2-autopilot-log --inputs --last 80 --follow
ps2-autopilot-log --verbose --last 60 --follow
ps2-autopilot-log --spatial --last 80 --follow
```

Generate a summary report with:

```bat
ps2-autopilot-report
```

Failure bundles are intentionally self-contained: the frame that triggered recovery, the previous frame, current semantic state, and the recent decision/input window travel together. Runtime retention caps old bundles and unknown captures so unattended operation cannot grow the folder without bound.

## OBS overlay

The built-in state server defaults to:

```text
http://127.0.0.1:8765/
```

The active config chooses the game-specific overlay. Debug-only fields can be viewed with:

```text
http://127.0.0.1:8765/?debug=1
```

## Calibration workflow

Templates are useful for stable visual states while OCR and CV provide semantic context. Captured game imagery is gitignored and should not be committed.

Example Jak captures:

```bat
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_main_menu --series 3
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_save_select --series 5
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_gameplay_geyser --series 5
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_cutscene --series 5
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_death --series 3
```

Example Madden captures:

```bat
ps2-autopilot-capture --config config\madden2005.yaml --label playcall_offense --series 5
ps2-autopilot-capture --config config\madden2005.yaml --label pre_snap_defense --series 5
ps2-autopilot-capture --config config\madden2005.yaml --label game_over --series 3
```

## Repository map

```text
config/                     game/runtime configuration
overlay/                    OBS browser-source front ends
profiles/<game>/templates/  local calibration templates (game imagery ignored)
src/ps2_autopilot/
  app.py                    shared runtime loop
  observability.py          events, inputs and failure evidence
  runtime_retention.py      bounded runtime storage
  profiles/registry.py      game registry
  profiles/madden2005_v*.py versioned Madden policies
  profiles/jak_and_daxter*.py versioned Jak policies
  jak_perception.py         Jak scene/progress perception
  jak_knowledge.py          Jak control/progression knowledge
```

## Roadmap

Near-term work is evidence-driven rather than a fixed feature dump:

1. calibrate Jak's save-file selector and complete unattended boot into Geyser Rock
2. collect representative Jak gameplay, cutscene, pause, death and checkpoint failure bundles
3. add traversable-space / ledge / gap perception and better jump timing
4. add enemy, hazard and collectible perception
5. calibrate special control modes against real Zoomer / Flut Flut / cannon / fishing footage
6. complete Jak 2-hour and 8-hour unattended soaks
7. continue Madden long-soak hardening without regressing its V22 behavior
8. add additional PS2 games as independent registered profiles

## Legal / repository hygiene

No BIOS, ROMs, ISOs, emulator binaries, save files, credentials, or copyrighted game assets are included in the repository. Use your own PCSX2 installation and legally obtained game dumps. Local screenshots/templates used for calibration are intentionally excluded from source control.
