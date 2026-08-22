# PS2 AutoPilot

Windows-first autonomous PCSX2 gameplay framework for unattended livestreams.

The first real target is **Madden NFL 2005 (PS2)**. AutoPilot captures the active PCSX2 render, reads the game screen, drives a virtual controller, recovers from dead air, and publishes its internal state to an OBS browser overlay.

## v0.4 — semantic Madden agent

The first live test proved the controller chain worked, but also exposed the biggest problem with v0.3: random menu input happily created a Franchise and entered the **Select Drill/Player → Pocket Presence** path.

v0.4 replaces that behavior with a semantic layer:

- optional local OCR using RapidOCR/ONNX Runtime; no cloud API is required
- OCR frames are upscaled before inference so older PCSX2 builds with small client captures remain readable
- recognizes title, Play Now/main menu, team select, controller select, matchup/settings, coin toss, play calling, kick meter, pause/final screens, dialogs, and known wrong-mode screens
- treats `FRANCHISE SETUP`, `SELECT DRILL/PLAYER`, `POCKET PRESENCE`, Training Camp, Mini Camp, Madden 101, and similar branches as **escape states**
- wrong-mode recovery backs toward the title screen instead of confirming deeper into a menu
- after a recognized title screen, it uses the main menu's default **Play Now** route instead of menu RNG
- parses down/distance, quarter, and game clock when OCR can read them
- uses down/distance to bias offensive play intent toward run or pass
- play calling no longer wanders around with random D-pad input
- passing keeps the QB in the pocket, brings up receiver icons, chooses among all five receiver buttons, takes control of the receiver, and attempts a catch
- rushing adds sprint, jukes, spin, stiff arms, ball protection, and situational dives
- defense pursues a two-dimensional motion target and mixes switch-nearest-ball, sprint/shed, tackle, play-ball, strip, rush moves, and Hit Stick attempts
- kicking keeps the three-stage X timing: start meter, stop power, stop accuracy
- OBS now exposes semantic screen, OCR text, situation, role confidence, play plan, menu escapes, and tracking data

The agent still improves dramatically with calibration templates, but v0.4 no longer requires templates just to avoid obviously wrong game modes.

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

v0.4 checks Windows, the active PCSX2 render window, frame capture, the virtual gamepad backend, calibration templates, the Madden field detector, and RapidOCR semantic vision. The OCR check prints a preview of text read from the current game frame.

If the game is sitting on `PRESS START`, a healthy OCR check should usually show some version of that phrase.

## Run it

```bat
ps2-autopilot --config config\madden2005.yaml
```

Stop with `Ctrl+C`.

OBS Browser Source:

```text
http://127.0.0.1:8765/
```

The HUD now exposes information like:

```text
SEMANTIC SCREEN  PLAYCALL 94%
PHASE            PLAYCALL
SITUATION        3&7 Q4 2:14
ROLE             OFFENSE 82%
PLAY PLAN        PASS
ACTION           playcall: circle (pass)
OCR              3RD & 7 | QTR 4 | 2:14 | ASK MADDEN ...
```

## Menu policy

AutoPilot's navigation goal is deliberately narrow:

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

Known non-goal branches are backed out of rather than explored. This is important for unattended streaming: a conservative reset is better than spending an hour inside Franchise training camp.

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
              possession + down/distance
                         |
            +------------+-------------+
            |                          |
       menu navigator             field policy
                                      |
                        +-------------+-------------+
                        |                           |
                     offense                      defense
                        +-------------+-------------+
                                      |
                                virtual Xbox pad
                                      |
                                    PCSX2

telemetry ---------------------------------------> OBS
```

## Next targets

1. live-calibrate OCR/vision against actual Madden game screens
2. parse score and field position reliably
3. recognize exact formation/play names and choose better calls
4. detect receivers/defenders rather than using motion centroids alone
5. add punt/field-goal/fourth-down logic
6. add clock management, red-zone policy, and two-minute offense
7. automate final screen -> next exhibition game for continuous streams

## Legal / repository hygiene

No BIOS, ROMs, ISOs, emulator binaries, save files, or copyrighted game assets are included. Use your own PCSX2 installation and legally obtained game dump.
