# PS2 AutoPilot

**Windows-first, multi-game PCSX2 automation for unattended gameplay and livestreaming.**

PS2 AutoPilot captures the active PCSX2 render, interprets game-specific visual state, drives a virtual controller, records evidence for unattended failures, and publishes a lightweight state feed for OBS. Shared infrastructure handles capture, supervision, logging, retention, controller I/O, optional read-only emulator telemetry, acceptance tooling, and overlays; each game owns its own perception and policy stack.

> Current package release: **v0.9.1**

## Game status

| Profile | Game | Current policy | Maturity |
| --- | --- | --- | --- |
| `madden2005` | Madden NFL 2005 | `Madden2005V24Profile` | production-candidate |
| `jak_and_daxter` | Jak and Daxter: The Precursor Legacy | `JakAndDaxterV22Profile` | production-candidate |
| `generic_chaos` | Generic controller/capture smoke test | generic | diagnostic |

Madden and Jak are deliberately isolated. Shared runtime plumbing is reused; football assumptions never leak into the platformer policy.

List the profiles available in your checkout:

```bat
ps2-autopilot --list-profiles
```

Maturity applies to the **active registry-selected policy**, not the best result ever achieved by an older version. Madden V23 completed a seven-game unattended lifecycle soak; V24 changes special-teams ownership/possession semantics and therefore remains a production candidate until the active version repeats the required live acceptance.

See `GAME_PROFILES.md` for the detailed maturity and validation boundaries.

## Current evidence boundary

| Capability | Madden NFL 2005 | Jak and Daxter |
| --- | --- | --- |
| PCSX2 capture/controller attachment | live-proven | live-proven, including modern PCSX2 Qt render-window attachment |
| Boot / initial menu navigation | live-proven | live-proven through PRESS START / NEW GAME / save flow |
| Reach normal gameplay autonomously | live-proven | live-proven through opening presentation into Geyser Rock |
| Repeated lifecycle / objective completion | V23 completed 7 games before exposing the EA SPORTS Bio modal; that modal now has a dedicated safe handler | Geyser Rock objective graduation remains open |
| Supervisor AutoPilot restart | implemented and regression-tested | implemented and regression-tested |
| PCSX2 launch/relaunch integration | implemented behind explicit local config + safety opt-ins; real process-death exhibition proof remains open | shared supervisor implementation available; live game-specific acceptance remains open |
| Final long-run acceptance | repeated fresh boot, real PCSX2-death recovery, and clean overnight soak remain | semantic/contact validation, Geyser graduation, and long soaks remain |

Implementation coverage is not treated as live evidence. The repo contains strict acceptance tools specifically so code presence cannot accidentally close a runtime criterion.

## Design rules

PS2 AutoPilot prioritizes reliable unattended behavior over raw button volume:

- **Fail closed on unknown screens.** An unrecognized menu/dialog should produce evidence, not random confirms.
- **Preserve presentation.** Replays, celebrations, cutscenes and story sequences are stream content.
- **Use game-specific semantics.** Madden needs possession/play clocks/menu transactions; Jak needs continuous locomotion/routes/hazards/platforming.
- **Separate policy from recovery ownership.** Game-local recovery, app savestate recovery, and supervisor process recovery have explicit boundaries.
- **Treat recovery as evidence.** Screens, state, decisions and inputs around failures are retained for regression work.
- **Keep semantic telemetry read-only.** PINE-derived state may inform decisions but does not write emulator memory.
- **Learn consequences where low-sample learning is useful.** Jak persists route danger/reward/escape outcomes across AutoPilot restarts.
- **Do not fake acceptance.** Fresh boots, process-death tests, long soaks and objective graduation require retained evidence.

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
                    Madden V24                   Jak V22
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
                                       |
                                       v
                         soak + game acceptance tools

Python supervisor ----------> AutoPilot process restart
        |
        +-------------------> optional PCSX2 launch/relaunch
                              (explicit local config + safety opt-ins)
```

## Requirements

- Windows 10 or 11 for the production runtime
- PCSX2 with your own legally obtained game dump
- 64-bit Python **3.11 or newer**; Python 3.11/3.12 remains the recommended full Madden OCR path
- virtual Xbox 360 controller through `vgamepad` / ViGEm for the normal setup

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

Existing checkout after pulling changes:

```bat
git pull --ff-only
bootstrap.cmd
.venv\Scripts\activate.bat
```

Manual editable install with the full runtime:

```bat
python -m pip install -e ".[full]"
```

The project intentionally keeps machine-local PCSX2/game-image paths out of source control.

## Validate configuration and runtime

Static validation can run **before PCSX2 starts**:

```bat
ps2-autopilot-doctor --config config\madden2005.yaml --config-only
ps2-autopilot-doctor --config config\jak_and_daxter.yaml --config-only
```

Static preflight checks the registered profile, controller backend, supervisor configuration, and—when emulator relaunch is enabled—the configured launcher working directory and executable/PATH resolution.

After PCSX2 is running, use the full live doctor:

```bat
ps2-autopilot-doctor --config config\madden2005.yaml
ps2-autopilot-doctor --config config\jak_and_daxter.yaml
```

The full doctor adds Windows runtime, render-window discovery, frame capture, controller dependency and game-specific live probes.

See `WINDOWS_SETUP.md` for setup details.

## Run a game

### Madden NFL 2005

Interactive:

```bat
ps2-autopilot --config config\madden2005.yaml
```

Supervised:

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

`Ctrl+C` and `runtime/STOP24X7` are intentional clean stops.

The Python supervisor always owns AutoPilot process restart. PCSX2 launch/relaunch is disabled by default; when enabled it requires an explicit argv list. Repeated-failure termination of an existing PCSX2 process is a **separate opt-in**. See `SUPERVISOR.md` and `RECOVERY_LADDER.md`.

## Madden NFL 2005 status

Madden has the strongest multi-game evidence in the repository, but the active V24 policy is deliberately marked production-candidate until its own live gates pass.

The inherited lifecycle includes:

- title/demo and main-menu recognition
- verified PLAY NOW navigation instead of blind confirmation
- bounded randomized team/home-away rotation
- controller-side selection
- conservative save/profile modal handling
- offense/defense role inference and play-clock-aware pre-snap behavior
- spatial player/ball/open-space hypotheses with confidence-gated policy use
- broadcast-preserving replays, celebrations, quarter breaks and post-play presentation
- verified END GAME -> main menu -> next exhibition lifecycle
- persistent session/unknown/failure evidence
- semantic -> hard -> savestate -> supervisor PCSX2 recovery ownership

V24 additionally makes special teams explicit:

- kickoff, kick return, punt, punt return, field goal and extra-point intent
- kicking-side vs returning-side controller ownership
- return contexts never execute the kick-meter macro
- kick/punt returns become offense with a bounded run-only return policy
- kickoffs/punts become defensive coverage
- ambiguous scoring-kick live transitions drop invented possession confidence

The broader “special teams are reliable” criterion stays open until real footage validates recognition and timing.

### Madden final lifecycle gate

Issue #3 is intentionally down to three live criteria:

1. repeated fresh-boot team/controller/settings selection
2. real PCSX2 process death followed by automatic exhibition-loop recovery
3. clean overnight soak with bounded evidence and no unresolved dead-air state

Evaluate retained evidence with:

```bat
ps2-autopilot-madden-acceptance --help
```

The evaluator requires explicit fresh-boot/process-death assertions and consumes the shared versioned runtime-evidence contract.

## Jak and Daxter status

Jak is a separate third-person platformer stack. Real PCSX2 sessions have exercised:

```text
PRESS START
   -> MAIN MENU / NEW GAME
   -> SAVE-FILE / SLOT FLOW
   -> OVERWRITE CONFIRMATION (when applicable)
   -> OPENING PRESENTATION
   -> GEYSER ROCK GAMEPLAY
```

Current V22 behavior includes:

- continuous on-foot analog locomotion and camera steering
- hands-off unknown/story presentation behavior
- save/slot/overwrite ownership that preempts gameplay input
- water/shoreline recovery and persistent water-route danger learning
- target validation, blacklisting, route consequence memory and anti-loop behavior
- atomic hop-step/jump/double-jump/roll-jump/dive/platform-chain transactions
- explicit ALIGN / COMMIT / AIRBORNE-or-ATTACK / VERIFY phases with bounded retry/timeouts
- optional read-only PINE semantics with identity/schema verification
- self-resolving Jak 1 GOAL position/velocity/progression schema rather than invented absolute addresses
- source-derived contact probing with fail-closed validation
- objective/progression telemetry and Geyser Rock acceptance tooling

Jak remains a production candidate because Geyser Rock has not yet graduated autonomously from fresh boots and the long-soak gates remain open.

Useful commands:

```bat
ps2-autopilot-jak-report runtime\verbose.jsonl
ps2-autopilot-jak-semantic-check --help
ps2-autopilot-jak-contact --help
ps2-autopilot-jak-curriculum --help
ps2-autopilot-jak-captures --help
ps2-autopilot-jak-acceptance --help
ps2-autopilot-jak-validation --help
```

See `JAK_PRODUCTION.md` and issue #66 for the Geyser Rock validation/graduation sequence.

## Runtime evidence and debugging

Runtime artifacts live under gitignored `runtime/` paths. Depending on the active game/event:

```text
runtime/
  state.json
  supervisor.json
  supervisor.jsonl
  session.json
  events.jsonl
  input.jsonl
  verbose.jsonl
  spatial.jsonl
  unknown/
  failures/
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

One- or multi-session soak report:

```bat
ps2-autopilot-soak-report runtime
```

Example thresholded report:

```bat
ps2-autopilot-soak-report runtime --min-games-completed 2 --max-unresolved-pct 10 --max-unknown-captures 0
```

Failure bundles are self-contained: triggering frame, prior frame, semantic state, and recent decision/input history travel together. Runtime retention bounds old evidence so unattended sessions cannot grow storage indefinitely.

Acceptance consumers use `runtime_evidence.py` as the public compatibility boundary for retained-log discovery, unresolved-state semantics and soak-report schema validation. Schema drift fails loudly rather than silently becoming zero metrics.

## OBS overlay

The built-in state server defaults to:

```text
http://127.0.0.1:8765/
```

Debug fields:

```text
http://127.0.0.1:8765/?debug=1
```

## Calibration workflow

Captured game imagery is gitignored and should not be committed by default.

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
config/                         game/runtime configuration
overlay/                        OBS browser-source front ends
profiles/<game>/templates/      local calibration templates (game imagery ignored)
state/                          persistent learned local state (gitignored)
src/ps2_autopilot/
  app.py                        shared runtime loop
  supervisor.py                 AutoPilot + optional PCSX2 recovery ownership
  config_preflight.py           static profile/controller/supervisor validation
  observability.py              events, inputs and failure evidence
  soak_report.py                multi-session unattended metrics
  runtime_evidence.py           public retained-evidence compatibility boundary
  madden_acceptance.py          final Madden lifecycle evidence gate
  pine.py / jak1_semantic.py    optional read-only semantic bridge
  jak_experience.py             persistent Jak consequence memory
  runtime_retention.py          bounded runtime storage
  profiles/registry.py          active game registry + maturity
  profiles/madden2005_v*.py     versioned Madden policies
  profiles/jak_and_daxter*.py   versioned Jak policies
```

## Near-term roadmap

The remaining work is intentionally evidence-driven rather than version-number-driven:

1. validate Jak XYZ/velocity/contact/progression semantics end-to-end on the active PCSX2 build
2. populate real Geyser Rock graph/curriculum evidence and graduate the V22 skill engine through the V23 benchmark
3. complete Jak 2-hour / 8-hour and repeated fresh-boot graduation runs
4. run Madden V24 repeated-fresh-boot, real PCSX2-death, and clean overnight acceptance
5. calibrate Madden spatial player/ball/team grouping, receiver choice, defensive timing, and V24 special teams from retained real footage
6. expand to additional PS2 games only as independent registered profiles with their own acceptance boundaries

## Legal / repository hygiene

No BIOS, ROMs, ISOs, emulator binaries, save files, credentials, or copyrighted game assets are included in the repository. Use your own PCSX2 installation and legally obtained game dumps. Local screenshots/templates used for calibration are intentionally excluded from source control.
