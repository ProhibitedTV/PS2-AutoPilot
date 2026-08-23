# PS2 AutoPilot

Windows-first autonomous PCSX2 gameplay framework for unattended livestreams.

The first real target is **Madden NFL 2005 (PS2)**. AutoPilot captures the active PCSX2 render, reads the game screen, drives a virtual controller, recovers from dead air, and publishes its internal state to an OBS browser overlay.

## v0.5 — verified menus + 24/7 supervision

v0.5 is focused on surviving unattended operation rather than merely pressing buttons until Madden eventually accepts them.

New in this round:

- menu actions are transactional: press -> observe -> verify the expected destination
- failed menu transitions retry before falling back toward a known-safe screen
- OCR geometry plus OpenCV are used to estimate the currently highlighted menu row
- the main menu can move the selection toward `PLAY NOW` instead of assuming it is already highlighted
- unknown menu screens never blindly confirm after a long timeout; the safe default is to back out
- unknown states are automatically captured to `runtime/unknown/` with a PNG and matching JSON metadata
- a semantic-progress watchdog detects when the bot is making no useful progress even if pixels are still moving
- recovery escalation is separate from the old raw-motion watchdog
- persistent `runtime/session.json` tracks game starts/completions and recovery counters
- `runtime/madden-events.jsonl` records state transitions and controller actions for postmortem analysis
- the OBS HUD shows highlighted row, pending/expected menu transaction, semantic-stall time, verified transitions, failures, unknown captures, and 24/7 game counts
- pause recovery is explicit: Start is attempted first, then Cross on the normal Resume Game selection if Start does not clear the pause overlay
- pause detection also recognizes clusters such as `RESUME GAME`, `INSTANT REPLAY`, `GAME STATS`, `SETTINGS`, and `QUIT GAME` when OCR misses the small PAUSE header
- `run24x7.cmd` restarts AutoPilot after an unexpected process failure while still allowing a clean Ctrl+C shutdown

The existing gameplay policy remains in place underneath this supervision layer.

## Install / update

Python 3.11 or 3.12 is recommended for the full OCR stack on Windows.

Fresh setup:

```bat
bootstrap.cmd
```

Existing clone after pulling a new version:

```bat
git pull
bootstrap.cmd
.venv\Scripts\activate.bat
```

`bootstrap.cmd` installs the `full` extra: virtual gamepad plus local OCR.

Manual equivalent:

```bat
python -m pip install -e ".[full]"
```

PCSX2 controller port 1 should map the virtual Xbox 360 controller as follows:

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

## Check the runtime

Boot PCSX2 with Madden visible and run:

```bat
ps2-autopilot-doctor --config config\madden2005.yaml
```

The doctor checks Windows, the active PCSX2 render window, frame capture, the virtual gamepad backend, calibration templates, the Madden field detector, ONNX Runtime preload, and RapidOCR semantic vision.

## Run it

Normal interactive run:

```bat
ps2-autopilot --config config\madden2005.yaml
```

For an unattended process wrapper:

```bat
run24x7.cmd
```

A clean `Ctrl+C` stops AutoPilot instead of immediately restarting it. The wrapper only restarts after an unexpected nonzero process exit.

OBS Browser Source:

```text
http://127.0.0.1:8765/
```

The v0.5 HUD exposes information like:

```text
SEMANTIC SCREEN     MAIN_MENU 96%
HIGHLIGHT           GAME MODES 71%
MENU TXN            cross -> team_select/controller_select/matchup
PHASE               MENU
ACTION              menu: move up toward PLAY NOW
SEMANTIC STILL      2.4s / L0
24/7 GAMES          2 complete / 3 started
MENU VERIFIED/FAIL  14 / 1
UNKNOWN CAPTURES    3
```

## Menu policy

AutoPilot's navigation goal remains deliberately narrow:

```text
TITLE
  -> PLAY NOW
  -> TEAM SELECT
  -> CONTROLLER/SIDE SELECT
  -> MATCHUP / SETTINGS
  -> COIN TOSS
  -> PLAYCALL
  -> FOOTBALL
```

Known non-goal branches are backed out of rather than explored. For unattended streaming, a conservative reset is better than spending an hour inside Franchise training camp.

### Verified transitions

A confirm action is no longer assumed to work. The navigator records the source screen, action, and acceptable destinations, then observes until one is seen.

```text
MAIN_MENU
  action: CROSS
  expected: TEAM_SELECT / CONTROLLER_SELECT / MATCHUP
```

If the source screen remains visible, the action is retried. If a different unexpected mode appears, the transaction is rejected and the bot backs out.

### Pause safety

Pause menus are special-cased because navigating down them can reach settings or quit-game actions. Recovery therefore uses only:

1. Start to toggle pause off.
2. Cross as a fallback for the normally highlighted Resume Game option.

It does not wander down the pause menu while trying to recover.

## 24/7 runtime evidence

Runtime artifacts live under the gitignored `runtime/` folder.

```text
runtime/
  state.json
  session.json
  madden-events.jsonl
  unknown/
    20260822-183142-acde1234.png
    20260822-183142-acde1234.json
```

Unknown captures are perceptually hashed and capped so an unattended stream does not fill the disk with duplicate screenshots.

`session.json` persists coarse lifecycle information such as games started/completed, progress recoveries, hard recoveries, and the last known phase.

`madden-events.jsonl` records transitions, actions, unknown captures, and requested recoveries. The log rotates when it reaches the configured size limit.

## Football policy

### Offense

Before the snap, Cross snaps the ball. On pass plays, the bot keeps the quarterback mostly in the pocket, uses Cross to bring up passing icons, then throws with Cross/Square/Circle/L1/R1. Once the ball is out it tries to take the receiver and make the catch. If the pocket survives too long without a throw, it can throw the ball away.

On runs, the bot attacks upfield and uses sprint, jukes, spin, stiff arms, protect-ball, and occasional dives. Down and distance bias the call: short yardage trends run; long third downs trend pass.

### Defense

The bot switches toward the defender nearest the ball, steers toward the 2D motion centroid, and mixes sprint/shed, dive tackle, play-ball/intercept, strip attempts, rush moves, and Hit Stick attempts.

### Kicking

The kick meter is treated as:

1. Cross — start meter
2. Cross — stop power
3. Cross — stop accuracy

The timing remains configurable in `config/madden2005.yaml`.

## Calibration

OCR gives semantic context, while templates remain useful for stable visual states.

```bat
ps2-autopilot-capture --label playcall_offense --series 5 --interval 0.4
ps2-autopilot-capture --label pre_snap_offense --series 5 --interval 0.4
ps2-autopilot-capture --label pre_snap_defense --series 5 --interval 0.4
ps2-autopilot-capture --label kick_meter --roi 0.20,0.55,0.60,0.40 --series 3
```

Useful labels include `playcall_offense`, `playcall_defense`, `pre_snap_offense`, `pre_snap_defense`, `post_play`, `kick_meter`, `pause`, and `game_over`. Captured game imagery is gitignored.

## Architecture

```text
PCSX2
  |
  +--> frame capture ------------------------------+
  |                                                |
  +--> field/motion CV                             |
  |                                                v
  +--> low-rate local OCR ---> semantic screen / situation
                                                   |
                         +-------------------------+
                         v
                 temporal football state
                         |
            +------------+-------------+
            |                          |
    verified menu graph            field policy
            |                          |
            +------------+-------------+
                         |
                    virtual pad
                         |
                       PCSX2

semantic state ---> progress watchdog ---> recovery ladder
       |
       +--> session/event logs
       +--> unknown-state captures
       +--> OBS telemetry
```

## Next targets

1. validate v0.5 against several complete boot-to-game cycles
2. automate final/post-game screens back into the next Play Now matchup
3. add PCSX2 process launch/restart and cold-boot recovery
4. parse score and field position reliably
5. recognize exact formation/play names and choose better calls
6. add punt/field-goal/fourth-down logic
7. add clock management, red-zone policy, and two-minute offense
8. run 2-hour, 8-hour, and 24-hour unattended soak tests

## Legal / repository hygiene

No BIOS, ROMs, ISOs, emulator binaries, save files, or copyrighted game assets are included. Use your own PCSX2 installation and legally obtained game dump.
