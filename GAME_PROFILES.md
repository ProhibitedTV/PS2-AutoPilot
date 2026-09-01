# Game Profiles

PS2 AutoPilot is a multi-game PCSX2 runtime. Game-specific behavior lives behind a registered profile; capture, controller, supervision, retention, logging, optional semantic telemetry, acceptance tooling, and overlay plumbing stay shared.

## Registered games

| Profile | Game | Current policy | Maturity | Config |
| --- | --- | --- | --- | --- |
| `madden2005` | Madden NFL 2005 | `Madden2005V32Profile` | production-candidate | `config/madden2005.yaml` |
| `jak_and_daxter` | Jak and Daxter: The Precursor Legacy | `JakAndDaxterV22Profile` | production-candidate | `config/jak_and_daxter.yaml` |
| `nfs_hot_pursuit_2` | Need for Speed: Hot Pursuit 2 | `NfsHotPursuit2V9Profile` | diagnostic | `config/nfs_hot_pursuit_2.yaml` |
| `generic_chaos` | Generic smoke test | generic | diagnostic | custom |

List the registry at any time:

```bat
ps2-autopilot --list-profiles
```

The real-game implementations are independently versioned. Shared runtime plumbing is reused; football, platforming and racing assumptions stay inside their respective policies.

## Run one game

Madden remains the backwards-compatible default:

```bat
run24x7.cmd
```

Jak:

```bat
run-jak24x7.cmd
```

Need for Speed: Hot Pursuit 2:

```bat
run-nfs24x7.cmd
```

Equivalent explicit commands use `run24x7.cmd <config>`. The supervisor can restart AutoPilot and, when explicitly configured, relaunch PCSX2. Machine-specific emulator/game-image paths remain local.

See `SUPERVISOR.md` and `RECOVERY_LADDER.md` for ownership and safety boundaries.

## Maturity means active-version evidence

Maturity describes the policy currently selected by the registry, not the best evidence ever achieved by an older version. A successor that changes behavior must re-earn live acceptance.

- `diagnostic`: implementation/calibration tooling exists, but the active game policy has not earned unattended lifecycle evidence.
- `production-candidate`: implementation and regression coverage are strong enough for targeted live acceptance, but unresolved live gates remain.
- `soak-tested`: the active policy itself has completed the repository's unattended soak/acceptance evidence.

## Validation boundaries

### Madden NFL 2005 — V32

Madden has the strongest repeated lifecycle evidence in the repository. V23 completed a seven-game unattended exhibition soak; later versions changed active special-teams, football, reacquisition and presentation behavior, so the current V32 policy remains a production candidate until its own live acceptance gates are repeated.

The active stack includes title/main-menu and PLAY NOW navigation, team/home-away rotation, football phase ownership, scoreboard/OCR and spatial hypotheses, root-menu reacquisition, theme-tolerant playcall reacquisition, quarter-break/broadcast input safety, retained evidence, acceptance tooling and supervisor recovery boundaries.

Useful acceptance command:

```bat
ps2-autopilot-madden-acceptance --help
```

### Jak and Daxter — V22

Real PCSX2 sessions have exercised PRESS START -> NEW GAME -> save/slot flow -> opening presentation -> Geyser Rock gameplay.

The active policy includes continuous analog navigation, water/shoreline recovery, platforming skills, route/target validation, optional read-only PINE semantics, persistent consequence memory and hardened water-danger learning. Semantic/contact validation remains fail-closed, and Geyser Rock graduation still requires retained evidence.

Useful validation commands:

```bat
ps2-autopilot-jak-semantic-check --help
ps2-autopilot-jak-contact --help
ps2-autopilot-jak-curriculum --help
ps2-autopilot-jak-acceptance --help
ps2-autopilot-jak-validation --help
```

### Need for Speed: Hot Pursuit 2 — V9

NFS HP2 is the first racing policy and remains `diagnostic` while the active stack earns unattended lifecycle evidence from real PCSX2 sessions.

V1 established adaptive road-corridor perception, analog steering and bounded recovery. V2 added PS2 menu topology, route planning, replay/pause ownership and You're The Cop controls. V3 added evidence-stable selected-row transactions, predictive steering, coasting and one-shot lifecycle exits. V4 added countdown launch control, Hot Pursuit hazard templates, BUSTED continuation, pursuit-racer ownership and recovery escalation. V5 added temporally confirmed traffic tracking, shortcut ownership and anti-ram behavior. V6 removed the indefinite UNKNOWN deadlock with progress-aware bootstrap and guarded fast race takeover. V7 used the fixed PS2 race HUD to keep ownership of active gameplay when road segmentation is blind. V8 added repeated wall-stall recovery and a last-resort Restart Race ladder.

V9 focuses on smarter continuous racecraft, terminal robustness and 24/7 presentation:

- strong fixed-HUD gameplay can extend the last coherent road solution through short moving texture/shadow dropouts without allowing a stationary wall trap to inherit stale road;
- the first wall-recovery attempts use the most recent road-center direction so the forward half of the escape points back toward the course;
- later failed attempts retain V4/V8 alternating-side escalation rather than trusting stale geometry forever;
- Restart Race success is now acknowledged only from visual change *after* restart confirmation, so opening the Pause menu cannot count as a successful restart;
- repeated failed Restart Race attempts can escalate to the documented Pause -> Quit Race path, returning control to the unattended menu lifecycle instead of leaving a poisoned race state parked forever;
- replay presentation cycles through several bounded hold times rather than using the same cadence every race;
- racer modes may issue an occasional horn flourish only on a clean, well-observed straight; cop mode is excluded because Circle has pursuit semantics there;
- camera switching remains disabled because changing the chase camera would invalidate the road/HUD geometry that the driving stack depends on;
- image-only traffic steering remains opt-in pending live false-positive evidence; positive directional roadblock/shortcut/police templates still have higher-confidence ownership.

Live gates are tracked in issue #129. See:

```text
NFS_HP2_V1.md
NFS_HP2_V2.md
NFS_HP2_V3.md
NFS_HP2_V4.md
NFS_HP2_V5.md
NFS_HP2_V7.md
NFS_HP2_V8.md
NFS_HP2_V9.md
```

## Why these policies stay separate

Madden is dominated by menus, OCR, possession and short phase-bound controller transactions. Jak is a continuous third-person platformer with hazards, routes, objectives and movement skills. NFS is a continuous racing controller where the primary problem is extracting a drivable corridor, predicting bend direction, managing speed, avoiding traffic/police hazards, choosing shortcuts and preserving menu/race-mode ownership.

Those semantics should not become branches inside one giant profile. The registry keeps them behind the same runtime contract while each game owns its own perception and policy state.

## Local template workflow

Templates are game-specific and may contain copyrighted game imagery, so local PNG calibration captures remain gitignored.

Examples:

```bat
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_gameplay_geyser --series 5
ps2-autopilot-capture --config config\nfs_hot_pursuit_2.yaml --label nfs_main_menu_world_racing_selected --series 3
ps2-autopilot-capture --config config\nfs_hot_pursuit_2.yaml --label nfs_world_racing_quick_race_selected --series 3
ps2-autopilot-capture --config config\nfs_hot_pursuit_2.yaml --label nfs_race_hud --series 5
ps2-autopilot-capture --config config\nfs_hot_pursuit_2.yaml --label nfs_race_hud_roadblock_avoid_left --series 3
ps2-autopilot-capture --config config\nfs_hot_pursuit_2.yaml --label nfs_race_hud_shortcut_enter_left --series 3
ps2-autopilot-capture --config config\nfs_hot_pursuit_2.yaml --label nfs_hot_pursuit_hud_police_ram_left --series 3
ps2-autopilot-capture --config config\nfs_hot_pursuit_2.yaml --label nfs_busted_continue --series 3
```

Prefer tight stable ROIs over full dynamic frames when a distinctive selected row, HUD element or dialog region is available.
