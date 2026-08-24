# PS2 AutoPilot

**Windows-first, multi-game PCSX2 automation for unattended gameplay and livestreaming.**

PS2 AutoPilot captures the active PCSX2 render, interprets game-specific visual state, drives a virtual controller, records evidence for unattended failures, and publishes a lightweight state feed for OBS. Shared infrastructure handles capture, supervision, logging, retention, controller I/O, optional read-only emulator telemetry, and overlays; each game owns its own perception and policy stack.

> Current package release: **v0.9.1**

## Game status

| Profile | Game | Current policy | Maturity |
| --- | --- | --- | --- |
| `madden2005` | Madden NFL 2005 | `Madden2005V23Profile` | soak-tested |
| `jak_and_daxter` | Jak and Daxter: The Precursor Legacy | `JakAndDaxterV21Profile` | production-candidate |
| `generic_chaos` | Generic controller/capture smoke test | generic | diagnostic |

Madden and Jak are deliberately isolated. Shared runtime plumbing is reused; football assumptions never leak into the platformer policy.

List the profiles available in your checkout:

```bat
ps2-autopilot --list-profiles
```

## What maturity means here

The project now has more evidence than “the bot can reach one game,” but the two integrations are at very different stages. The table below separates behavior observed in live PCSX2 runs from acceptance work that is still open.

| Capability | Madden NFL 2005 | Jak and Daxter |
| --- | --- | --- |
| PCSX2 capture/controller attachment | live-proven | live-proven, including modern PCSX2 Qt render-window attachment |
| Boot / initial menu navigation | live-proven | live-proven through PRESS START and NEW GAME |
| Save / modal handling | multiple real Madden save/profile modals handled conservatively | live-proven through slot/save/overwrite confirmation, including restart while parked on overwrite |
| Reach normal gameplay autonomously | live-proven | live-proven through the opening presentation into Geyser Rock gameplay |
| Repeated lifecycle / objective completion | a live unattended soak completed 7 games before exposing the EA SPORTS Bio modal; that modal now has a dedicated safe handler | not yet: Geyser Rock objective completion remains the active benchmark |
| Long-run acceptance | substantial multi-game evidence exists, but final overnight/cold-restart acceptance remains open | 2-hour, 8-hour, and 5/5 Geyser graduation are not complete |
| Emulator-process recovery | AutoPilot process supervision exists | AutoPilot process supervision exists |
| Relaunch PCSX2 after emulator death | **not yet**; tracked separately | **not yet**; tracked separately |

The Madden seven-game soak is evidence of a real multi-game lifecycle, not a claim that every overnight failure mode is solved. Likewise, Jak reaching gameplay is not equivalent to autonomously completing Geyser Rock.

## Design goals

PS2 AutoPilot is built around a few rules that matter more than raw button volume:

- **Fail closed on unknown screens.** An unrecognized menu or dialog should produce evidence, not random confirms.
- **Preserve presentation.** Replays, celebrations, cutscenes and story sequences are stream content and should not be skipped just because they are temporarily non-interactive.
- **Use game-specific semantics.** Madden needs possession, play clocks and menu transactions; Jak needs continuous locomotion, route memory, hazards and platforming skills.
- **Treat recovery as evidence-driven.** The runtime keeps screenshots, decisions and inputs around failures so a bad unattended state can become a regression test.
- **Learn from consequences where useful.** Jak V21 persists low-sample route danger/reward/escape experience across restarts instead of forgetting every failed excursion.
- **Stay broadcast-friendly.** Public overlays expose useful game state while deeper forensic telemetry remains available separately.

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
                          optional read-only semantics
                             (PINE when configured)
                                       |
                                       v
                              registered game profile
                         +-------------+-------------+
                         |                           |
                    Madden V23                   Jak V21
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

Supervised wrapper:

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

`Ctrl+C` is an intentional clean shutdown. The wrapper restarts AutoPilot after an unexpected nonzero AutoPilot exit; it does **not** yet relaunch a dead PCSX2 process.

## Jak and Daxter production status

Jak is the second real-game integration and intentionally looks very different from Madden.

### Live-proven lifecycle

The current boot path has been exercised in real PCSX2 sessions:

```text
PRESS START
   -> MAIN MENU / NEW GAME
   -> SAVE-FILE / SLOT FLOW
   -> OVERWRITE CONFIRMATION (when applicable)
   -> OPENING PRESENTATION
   -> GEYSER ROCK GAMEPLAY
```

The save-file selector is no longer the calibration frontier. The current stack owns save/slot/overwrite menus before locomotion and can recover when AutoPilot is restarted while already sitting on the overwrite confirmation. Unknown destructive save actions still fail closed.

### Current V21 behavior

- OCR/visual verified PRESS START and NEW GAME transactions
- save-slot and overwrite-confirmation ownership that preempts gameplay input
- hands-off opening/story presentation behavior
- continuous on-foot analog locomotion and camera steering
- water/shoreline recovery with guards against uniform sky/lighting blue and side-only false water
- target validation, blacklisting, atomic relocation commits and stagnation recovery
- dedicated safe control modes for Zoomer, Flut Flut, cannon and primitive fishing behavior
- objective/progression telemetry for Geyser Rock
- optional **read-only** PCSX2 PINE bridge that self-resolves the Jak 1 GOAL symbol table and runtime schema on the validated retail build
- semantic position is accepted for learning only after it demonstrates plausible movement; static/bad coordinate samples are not trusted blindly
- persistent V21 consequence memory in `state/jak_experience.json`: water/death/respawn/stall outcomes penalize recent routes, verified progress rewards them, and successful escape directions are remembered across AutoPilot restarts

### What is not yet proven

Jak is still a **production-candidate**, not a completed autonomous playthrough system. The current Geyser Rock benchmark remains open:

- obtain all four Geyser Rock Power Cells
- free all seven Scout Flies
- complete the Blue Eco door transaction
- complete the cliff/platform sequence
- return through the warp gate
- complete the sequence without human controller intervention
- ultimately pass the fresh-boot graduation target repeatedly

Dynamic semantic coordinates and collectible counters also remain subject to end-to-end live validation before they are treated as authoritative for every navigation decision.

See [`JAK_PRODUCTION.md`](JAK_PRODUCTION.md) and [`GAME_PROFILES.md`](GAME_PROFILES.md) for the detailed boundaries.

## Madden NFL 2005 status

Madden remains the most mature integration. Its V23 stack includes deterministic menu navigation, bounded team/matchup variety, offense/defense role inference, game-situation OCR, spatial player/ball hypotheses, deliberate passing/running/defense behavior, confidence-gated defensive contact cadence, replay/presentation preservation, postgame lifecycle handling and unattended supervision.

A prior unattended run completed **7 games / 7 completed** before getting stuck on the distinct EA SPORTS Bio missing-profile modal. V22 added a dedicated visually verified CANCEL transaction for that modal, and V23 further reduces low-information defensive button spam. This is meaningful multi-game validation, while full overnight/cold-emulator-restart acceptance remains open.

The versioned Madden modules remain in the repository so later improvements do not erase prior working behavior.

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

Single-session summary:

```bat
ps2-autopilot-report
```

Automated one- or multi-session soak acceptance:

```bat
ps2-autopilot-soak-report runtime
```

Example acceptance gate:

```bat
ps2-autopilot-soak-report runtime --min-games-completed 2 --max-unresolved-pct 10 --max-unknown-captures 0
```

Jak-specific objective/runtime report:

```bat
ps2-autopilot-jak-report runtime\verbose.jsonl
```

Failure bundles are intentionally self-contained: the triggering frame, prior frame, current semantic state and recent decision/input window travel together. Runtime retention caps old bundles and captures so unattended operation cannot grow the folder without bound.

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
config/                       game/runtime configuration
overlay/                      OBS browser-source front ends
profiles/<game>/templates/    local calibration templates (game imagery ignored)
state/                        persistent learned local state (gitignored)
src/ps2_autopilot/
  app.py                      shared runtime loop
  observability.py            events, inputs and failure evidence
  soak_report.py              multi-session unattended acceptance report
  pine.py / jak1_semantic.py  optional read-only semantic bridge
  jak_experience.py           persistent Jak consequence memory
  runtime_retention.py        bounded runtime storage
  profiles/registry.py        game registry
  profiles/madden2005_v*.py   versioned Madden policies
  profiles/jak_and_daxter*.py versioned Jak policies
```

## Roadmap

Near-term work is evidence-driven:

1. validate Jak semantic position/progression fields during real controllable movement and collection events
2. turn validated Jak position buckets and learned outcomes into a sparse Geyser Rock route graph
3. build explicit ALIGN / COMMIT / AIRBORNE / VERIFY platforming skill controllers
4. graduate Geyser Rock autonomously before expanding into the open world
5. complete Jak 2-hour, 8-hour and repeated fresh-boot acceptance
6. add the final supervisor escalation that can relaunch PCSX2 after emulator process death
7. continue Madden overnight/cold-restart hardening and spatial calibration without regressing its proven multi-game lifecycle
8. add additional PS2 games as independent registered profiles

## Legal / repository hygiene

No BIOS, ROMs, ISOs, emulator binaries, save files, credentials, or copyrighted game assets are included in the repository. Use your own PCSX2 installation and legally obtained game dumps. Local screenshots/templates used for calibration are intentionally excluded from source control.
