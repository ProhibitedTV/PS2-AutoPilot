# PS2 AutoPilot

Windows-first autonomous PCSX2 gameplay framework for unattended livestreams.

The first real target is **Madden NFL 2005 (PS2)**. AutoPilot captures the active PCSX2 render window, classifies broad football states, drives a virtual controller, recovers from dead air, and publishes live telemetry for an OBS browser source.

## v0.3 — temporal football brain

The Madden driver now reasons across time instead of treating every screenshot independently:

- follows the largest visible window owned by the PCSX2 process, including older builds that open a separate GS/render window
- distinguishes `PLAYCALL`, `PRE_SNAP`, `LIVE`, and `POST_PLAY` phases with hysteresis so low-motion frames do not constantly flip state
- infers **offense vs defense** from optional labeled templates and from snap causality
- uses separate offense and defense policies instead of one possession-agnostic button mash
- offense has run/pass intent, passing-icon timing, receiver selection, catch attempts, sprint, spin, jukes, ball protection, and dives
- defense switches to the player nearest the ball, pursues the field's motion centroid, sprints/sheds, tackles, contests passes, and uses rush moves
- uses grass centroid to steer back toward usable field space instead of blindly oscillating left/right
- play calling is now a short Madden-aware formation/play macro rather than a single random face-button press
- post-play logic skips cutscenes without immediately confusing the next pre-snap phase
- kick meter logic remains a timed three-X sequence and now reports kick statistics
- adds L2/R2 virtual trigger support for future stiff-arm, throw-away, strip-ball, and advanced policies
- exposes phase, inferred role/confidence, play intent, tracking targets, and football stats to OBS
- adds a runtime doctor and better calibration capture tooling

This is still a vision-first prototype. It does **not** claim to understand score, down/distance, clock, routes, or every menu yet. Those become the next layer once calibration samples from the actual PCSX2 render are available.

## Install

Python 3.11+ on Windows:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .[virtual-gamepad]
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

## Check the runtime first

Boot PCSX2, then run:

```powershell
ps2-autopilot-doctor --config config/madden2005.yaml
```

The doctor checks the Windows runtime, resolves the real PCSX2 render window, captures a frame, checks the virtual-gamepad package, counts calibration templates, and prints the current Madden grass/field probe.

This is especially useful with **PCSX2 1.6.x**, where the game renderer may be a different top-level window from the main PCSX2 interface. AutoPilot now follows the largest visible window belonging to the same PCSX2 process.

## Run Madden 2005

Boot Madden normally, enter a safe menu or exhibition flow, then:

```powershell
ps2-autopilot --config config/madden2005.yaml
```

Add this as an OBS Browser Source:

```text
http://127.0.0.1:8765/
```

The overlay reports what the machine currently believes:

```text
PHASE       PRE_SNAP
ROLE        OFFENSE 82%
PLAY PLAN   PASS
ACTION      snap ball
RAW VISION  field_idle
FIELD/TARGET +0.04 / -0.21
PLAYS       8 complete / 9 started
```

## How possession inference works

The first version could not distinguish offense and defense. v0.3 uses two signals:

1. **Calibration templates** such as `pre_snap_offense` or `playcall_defense` are high-confidence evidence.
2. When templates are absent, the bot watches cause-and-effect. If its pre-snap Cross press is followed quickly by live motion, it was probably the offense and just snapped the ball. If the play begins from pre-snap without its snap attempt, it was probably defending.

That is intentionally probabilistic. The role and confidence are visible in the OBS telemetry so bad inferences are easy to diagnose.

## Calibration

Full-screen templates work, but smaller HUD/UI crops are much more robust because the field and players constantly change.

Capture one frame:

```powershell
ps2-autopilot-capture --label pre_snap_offense
```

Capture five samples:

```powershell
ps2-autopilot-capture --label playcall_offense --series 5 --interval 0.4
```

Capture only a stable region of the screen using normalized `x,y,width,height` fractions:

```powershell
ps2-autopilot-capture --label kick_meter --roi 0.20,0.55,0.60,0.40 --series 3
```

Useful labels:

- `playcall_offense`
- `playcall_defense`
- `pre_snap_offense`
- `pre_snap_defense`
- `post_play`
- `kick_meter`
- `pause`
- `game_over`

Numbered series such as `playcall_offense_01.png` are recognized automatically.

Captured templates are stored under `profiles/madden2005/templates/` and are gitignored by default because they may contain copyrighted game imagery.

## Madden policy

The controller policy is based on the standard PS2 control layout used by Madden 2005:

**Offense**

- Cross: snap; after the snap, passing icons / receiver Cross / runner sprint depending context
- Square/Circle/L1/R1: receiver choices during passing and ball-carrier moves during rushing
- Triangle: catch / protect ball depending context
- left stick: upfield movement corrected toward the visible field centroid

**Defense**

- Cross: defender nearest the ball
- Circle: sprint / shove blocker
- Square: dive tackle
- Triangle: jump / intercept / hands up
- L1/R1: rush/swim-style defensive moves
- left stick: pursuit biased toward the motion centroid

The policy deliberately uses controls that remain reasonably useful even when the inferred play type is imperfect.

## Reliability

The outer watchdog still handles dead air:

1. profile performs a soft state-specific recovery for long-lived menus/playcall/pre-snap/post-play states
2. global motion watchdog runs a Madden recovery sequence
3. repeated failures trigger the configured PCSX2 save-state reload key

The goal is not just to make a clever agent. It is to keep a stream producing footage instead of spending twenty minutes stuck on a menu.

## Architecture

```text
PCSX2 process
   |
   +-> largest visible render window
             |
             v
        frame capture ------------------------+
             |                                |
             v                                v
      field / motion CV                template matcher
             |                                |
             +--------------+-----------------+
                            v
                    temporal phase model
                            |
                   possession inference
                            |
               +------------+------------+
               |                         |
          offense policy            defense policy
               |                         |
               +------------+------------+
                            v
                    virtual Xbox pad
                            |
                            v
                          PCSX2

phase / role / action / metrics -> local HTTP overlay -> OBS
```

## Current next targets

1. calibrate against the actual Madden 2005 PCSX2 render and tune thresholds
2. infer down, distance, score, quarter, clock, and possession from scoreboard/UI crops
3. distinguish run/pass/kick/punt situations from play-call UI
4. improve field/player localization beyond green and motion centroids
5. add clock management, fourth-down logic, red-zone behavior, and defensive situational calls
6. automate the complete exhibition boot -> game -> final -> next-game loop
7. add a director layer that can rotate games and expose high-level behavior to stream/chat controls

## Legal / repository hygiene

No BIOS, ROMs, ISOs, emulator binaries, save files, or copyrighted game assets are included. Use your own PCSX2 installation and legally obtained game dump.
