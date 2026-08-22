# PS2 AutoPilot

Windows-first autonomous gameplay framework for PCSX2 livestreams.

The first real target is **Madden NFL 2005 (PS2)**. The project captures the PCSX2 client, classifies broad game states, drives a virtual controller, recovers from dead air, and exposes stream telemetry for OBS.

## First target: Madden NFL 2005

Madden is treated as a state machine rather than an LLM pressing every button:

- boot/menu navigation
- play-call selection
- pre-snap offense/defense
- live offense (run/pass heuristics)
- live defense (pursuit/tackle heuristics)
- kicking
- post-play transitions
- watchdog recovery

The v0.2 Madden driver intentionally starts with robust, coarse heuristics that can run unattended. It can then be calibrated with screenshots/templates from the user's actual PCSX2 render.

## Safety / legal

No BIOS, ROMs, ISOs, copyrighted game assets, save files, or emulator binaries are included. Use your own PCSX2 installation and legally obtained game dump.

## Setup

Python 3.11+ on Windows:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .[virtual-gamepad]
```

PCSX2 should have an XInput-compatible controller mapped to port 1. `vgamepad` exposes a virtual Xbox 360 controller; map its A/B/X/Y buttons to PS2 Cross/Circle/Square/Triangle respectively.

Run:

```powershell
ps2-autopilot --config config/madden2005.yaml
```

Add `http://127.0.0.1:8765/` as an OBS Browser Source for the telemetry HUD.

## Calibration

The Madden profile works without templates, but templates make it much more reliable. Capture screenshots with:

```powershell
ps2-autopilot-capture --config config/madden2005.yaml --label playcall
```

Recommended labels:

- `playcall`
- `pre_snap`
- `kick_meter`
- `post_play`
- `pause`
- `game_over`

Images go under `profiles/madden2005/templates/` and are intentionally gitignored by default if they contain captured copyrighted game imagery.

## Architecture

```text
PCSX2 -> client capture -> CV/state estimator -> Madden profile -> virtual gamepad -> PCSX2
                           |                     |
                           +-> watchdog          +-> telemetry -> OBS
```

The local reflex/state layer is fast and deterministic. A future slow AI director can choose high-level goals (aggressive offense, clock management, etc.) without sitting in the controller loop.

## Current milestone

**v0.2: Madden 2005 autonomous exhibition prototype**

Goal: get from menus into an exhibition game, call plays, snap, run a basic offense, pursue on defense, kick, and recover from stalls with minimal manual intervention.
