# Game Profiles

PS2 AutoPilot is a multi-game PCSX2 runtime. Game-specific behavior lives behind a registered profile; capture, controller, supervision, retention, logging, optional semantic telemetry, acceptance tooling, and overlay plumbing stay shared.

## Registered games

| Profile | Game | Current policy | Maturity | Config |
| --- | --- | --- | --- | --- |
| `madden2005` | Madden NFL 2005 | `Madden2005V25Profile` | production-candidate | `config/madden2005.yaml` |
| `jak_and_daxter` | Jak and Daxter: The Precursor Legacy | `JakAndDaxterV22Profile` | production-candidate | `config/jak_and_daxter.yaml` |
| `generic_chaos` | Generic smoke test | generic | diagnostic | custom |

List the registry at any time:

```bat
ps2-autopilot --list-profiles
```

The Madden and Jak implementations are independently versioned. `madden2005` resolves to V25 and `jak_and_daxter` resolves to V22; neither policy imports the other game's state machine.

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

`run24x7.cmd` launches the Python supervisor. AutoPilot process crashes are restarted conservatively. When an explicit local PCSX2/game argv list is configured, the supervisor can also detect render-window/process loss and relaunch PCSX2. Terminating an existing emulator after repeated failures requires a separate opt-in; machine-specific executable and game-image paths are intentionally not committed.

See `SUPERVISOR.md` and `RECOVERY_LADDER.md` for the ownership and safety boundaries.

## Maturity means active-version evidence

Maturity describes the policy currently selected by the registry, not the best evidence ever achieved by an older policy version. A successor that changes behavior must re-earn live acceptance rather than automatically inheriting its predecessor's badge.

- `diagnostic`: smoke-test tooling, not a production game policy.
- `production-candidate`: implementation and regression coverage are strong enough for targeted live acceptance, but the active policy still has unresolved live gates.
- `soak-tested`: the active policy itself has completed the repository's defined unattended soak/acceptance evidence.

This distinction is why Madden V25 is currently `production-candidate`: V23 completed a seven-game unattended lifecycle soak, while V24 changed special-teams ownership/possession semantics and V25 now tightens timed kick-input ownership at the KICKING phase boundary. The active policy has not yet repeated the final live acceptance suite.

## Validation boundaries

### Madden NFL 2005

The Madden lifecycle has strong real-run evidence. V23 and its predecessors autonomously reached games, completed postgame navigation, returned to `PLAY NOW`, rotated teams/sides, and completed a seven-game unattended soak before an EA SPORTS Bio modal exposed a new blocker. That blocker was subsequently promoted into a dedicated write-safe recovery path.

V24 established the current special-teams state model:

- strict kickoff, kick-return, punt, punt-return, field-goal and extra-point intent
- separate kicking-side and returning-side controller ownership
- no kick-meter inputs while the CPU owns a return kick
- kick/punt returns hand live possession to offense with a bounded run-only return policy
- kickoff/punt coverage hands live possession to defense
- ambiguous scoring-kick live transitions drop invented possession confidence instead of guessing

V25 preserves those semantics and hardens the timed kick-meter transaction itself. Madden queues three Cross presses for a kick's start, power, and accuracy. If vision accepts a transition out of `KICKING` before every queued tap has fired, V25 discards the remainder immediately so a stale meter input cannot leak into `LIVE`, `POST_PLAY`, or another screen. Telemetry reports KICKING phase exits, queue-clear events, discarded taps, and the last clear reason for live calibration.

Those semantics are regression-tested but the active V25 profile still needs live calibration/soak evidence, so it remains a production candidate.

Issue #3 has three remaining live lifecycle gates: repeated fresh-boot selection, a real PCSX2 process-death recovery during the exhibition loop, and a clean overnight soak after the latest fixes. The closure tool is:

```bat
ps2-autopilot-madden-acceptance --help
```

It consumes retained evidence through the shared runtime-evidence contract and does not infer operator assertions such as a fresh boot or deliberate process-death test from file presence alone.

### Jak and Daxter

Jak V22 has crossed the old save-file calibration boundary. Real PCSX2 sessions have exercised:

```text
PRESS START
 -> NEW GAME
 -> save/slot flow
 -> overwrite confirmation when applicable
 -> opening presentation
 -> Geyser Rock gameplay
```

The current profile includes continuous analog navigation, water/shoreline recovery, ledge/platform skills, target/route validation, atomic relocation, optional read-only PINE semantics, persistent route-consequence memory, and hardened water-danger learning. The PINE bridge self-resolves the supported Jak 1 GOAL runtime schema instead of relying on invented absolute RAM addresses.

Semantic validation and contact probing remain fail-closed. Geyser Rock graduation requires retained evidence rather than planner inference, and the active V22 policy remains a production candidate until its live semantic/graduation gates pass.

Useful validation commands include:

```bat
ps2-autopilot-jak-semantic-check --help
ps2-autopilot-jak-contact --help
ps2-autopilot-jak-curriculum --help
ps2-autopilot-jak-acceptance --help
ps2-autopilot-jak-validation --help
```

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
