# PS2 AutoPilot

Windows-first autonomous gameplay framework for PCSX2 livestreams.

The first real target is **Madden NFL 2005 (PS2)**. The project captures the PCSX2 client, classifies broad game states, drives a virtual controller, recovers from dead air, and exposes stream telemetry for OBS.

## Madden 2005 strategy

This first driver deliberately avoids pretending we know possession before calibration. Madden has several controls that are useful on both sides of the ball, so the bot exploits them:

- On a quiet field, **Cross/X** snaps on offense; on defense it merely cycles defenders.
- During a live play, left stick is driven upfield with gentle lane wander.
- **Cross/X** acts as runner sprint / pass interaction on offense and nearest-defender switch on defense.
- **Circle** is useful as spin/pass selection on offense and sprint/shove on defense.
- **Square** provides aggressive dive/tackle behavior.
- **Triangle** provides protect/catch/intercept behavior.
- Play-call menus are advanced with D-pad movement plus Square/Cross/Circle selections.
- A calibrated kick-meter template triggers a timed three-press X kicking sequence.

That should produce ugly-but-real autonomous football before OCR, possession tracking, receiver detection, or field geometry are added.

## Setup

Python 3.11+ on Windows:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .[virtual-gamepad]
```

PCSX2 should map the virtual Xbox 360 controller to port 1. Map A/B/X/Y to PS2 Cross/Circle/Square/Triangle.

```powershell
ps2-autopilot --config config/madden2005.yaml
```

Add `http://127.0.0.1:8765/` as an OBS Browser Source.

## Calibration

The field detector works without templates, but menu and kicking reliability improve sharply with captures from the exact PCSX2 render.

```powershell
ps2-autopilot-capture --config config/madden2005.yaml --label playcall
```

Recommended labels: `playcall`, `pre_snap`, `kick_meter`, `post_play`, `pause`, `game_over`.

Captured PNGs are gitignored because they may contain copyrighted game imagery.

## Architecture

```text
PCSX2 -> client capture -> CV/state estimator -> Madden profile -> virtual gamepad -> PCSX2
                           |                     |
                           +-> watchdog          +-> telemetry -> OBS
```

No BIOS, ROMs, ISOs, emulator binaries, save files, or copyrighted game assets are included.

## Current milestone

**v0.2: Madden 2005 autonomous exhibition prototype**

Next: calibrate against real gameplay screenshots, distinguish possession/play-call/post-play states, add field-line/player tracking, and improve offense/defense into separate policies.
