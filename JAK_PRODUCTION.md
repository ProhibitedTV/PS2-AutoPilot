# Jak and Daxter: The Precursor Legacy — production runtime

`jak_and_daxter` is game #2 for PS2 AutoPilot. Madden NFL 2005 remains isolated as the soak-tested game #1 profile; none of the Jak policy below is shared with Madden.

The registered production profile is currently **Jak V21**. “Production-candidate” means the real boot/save/gameplay path is operational and the agent can run unattended, not that it can yet complete Geyser Rock reliably.

## Live-proven lifecycle

Real PCSX2 sessions have exercised the current path through:

```text
PRESS START
 -> MAIN MENU / NEW GAME
 -> SAVE-FILE / SLOT FLOW
 -> OVERWRITE CONFIRMATION when applicable
 -> OPENING PRESENTATION
 -> GEYSER ROCK GAMEPLAY
```

The save-file selector is no longer an uncalibrated blind-confirm boundary. Save/overwrite screens own input ahead of locomotion, and the overwrite confirmation can be recovered even when AutoPilot starts while already parked on that screen. Unknown or destructive save semantics still fail closed.

Modern PCSX2 Qt window attachment is supported by executable/PID identity rather than requiring the render-window title to contain `PCSX2`.

## What the game requires

The Precursor Legacy is not a single-control-mode game. On foot, Jak uses continuous left-stick locomotion and right-stick camera control, with jump/double-jump, crouch/high-jump, roll/roll-jump, punch and spin. The campaign also puts the player on the A-Grav Zoomer, on Flut Flut, behind a cannon, and into special minigames such as fishing. These contexts reuse buttons with different semantics, so aggressive input must remain mode-specific.

Persistent progress is centered on Power Cells, Precursor Orbs and Scout Flies. Seven Scout Flies in an area award a Power Cell, Orbs can be exchanged for Cells, and Cell counts gate later regions. The runtime therefore treats collectible totals and objective transitions as progress evidence rather than treating raw screen motion as success.

Useful reference points encoded in `jak_knowledge.py` include:

- on-foot: left stick move, right stick camera, Cross jump/double jump, L1/R1 crouch/roll, Square punch, Circle spin, L2/R2 stats HUD, Start status/pause;
- A-Grav Zoomer: left stick steer/pitch, right stick camera, Cross accelerate, L1/R1 hop/tight turn;
- Flut Flut: left stick move, right stick camera, Cross jump/flutter, Square headbutt;
- cannon: left stick aim, Cross fire/charge, Triangle exit;
- only uncontroversial progression gates are encoded as hard milestones when the repository has sufficient evidence; uncertain values stay observation-driven.

## V21 architecture

V21 sits on a long sequence of live-derived fixes rather than replacing them wholesale. The current stack includes:

- verified title/New Game/save/overwrite transactions with menu-exclusive input ownership;
- hands-off story/opening presentation behavior;
- continuous analog locomotion and camera steering;
- gameplay-only jump, roll-jump, ledge and recovery behaviors;
- target tracking, persistence, validation, blacklisting and bounded attention budgets;
- navigation-owned macro commits so visual reward hypotheses cannot constantly steal steering;
- stagnation relocation instead of endless camera-only scans;
- water and shoreline recovery with conservative geometry checks and vetoes for uniform sky/lighting blue and side-only water evidence;
- dedicated safe policies for Zoomer, Flut Flut and cannon, plus deliberately limited fishing behavior;
- runtime loop/capture/policy pressure telemetry and load shedding for expensive Jak perception;
- optional **read-only** PCSX2 PINE telemetry;
- self-resolving Jak 1 GOAL symbol-table/schema discovery instead of invented absolute addresses;
- persistent online consequence learning in `state/jak_experience.json`.

## Read-only semantic telemetry

The PINE path is deliberately read-only. It may inspect the running game but does not write memory, execute game code, or use PINE to alter save state.

On the validated retail build, the semantic resolver has live-resolved the GOAL symbol table, `*target*`, `*game-info*`, and a target root derived from `trsqv`. The transform layout was corrected using GOAL's `basic` object layout after a live sample revealed that the previous offset was reading the `(1,1,1)` scale vector as velocity.

Schema resolution is stronger than blindly trusting every emitted coordinate. V21 will not create persistent XYZ learning keys until the live position fields demonstrate plausible movement. Until then, the learning layer can use a brightness-resistant visual scene fingerprint. This prevents a bad/static semantic sample from teaching the agent that unrelated hazards all occur at one coordinate.

## Persistent consequence learning

V21 is intentionally a small online experience system rather than live neural-network training. It is designed to change behavior after a few meaningful outcomes:

- confirmed water entry penalizes the recent approach and creates an immediate episode-level no-return route;
- repeated water/stall failures accumulate persistent danger;
- explicit death or a checkpoint-like semantic displacement after recent danger produces a stronger route penalty;
- verified collectible/progression gains reward the recent route;
- revisiting learned danger preempts generic wandering with an atomic backoff/pivot/relocation maneuver;
- successful left/right escape outcomes are remembered and preferred on later encounters;
- consequence-bearing memory survives AutoPilot restarts in the gitignored `state/` directory.

The memory is inspectable by design. It is a foundation for a later world graph, not a claim that the game can currently be learned end-to-end from scratch without further policy work.

## Current Geyser Rock benchmark

The active benchmark is to graduate Geyser Rock before expanding into the open world. A successful fresh run must eventually:

- obtain all four Geyser Rock Power Cells;
- free all seven Scout Flies;
- complete the Blue Eco door transaction;
- complete the cliff/platform sequence;
- return through the warp gate;
- require no human controller intervention.

The longer-term graduation target is repeated fresh-boot success, not one lucky run.

That benchmark is **not yet complete**. Current live runs can reach and move around Geyser Rock, but reliable objective navigation/platforming remains the main frontier.

## Safety boundaries

Production does **not** mean “press buttons through every unknown screen.” Menu/save/cutscene ownership outranks gameplay policy. Unknown destructive save semantics fail closed, and ordinary presentation remains hands-off.

The raw movement/watchdog layers may perform active escape inputs only while the profile owns gameplay. During menus and presentation, controller state is neutralized rather than allowing locomotion or recovery actions to leak into transactions.

PINE remains advisory/read-only. Vision/menu safety does not depend on it, and V21 validates movement before using semantic XYZ for persistent learning.

## Acceptance still required

Before calling Jak soak-tested rather than production-candidate, the project still needs:

- end-to-end live validation that semantic XYZ changes coherently during controllable movement;
- live validation that collectible counters change on real collection events;
- a sparse Geyser Rock node/edge graph derived from validated positions and outcomes;
- verified ALIGN / COMMIT / AIRBORNE / LAND-VERIFY skill controllers for platforming;
- autonomous Geyser Rock graduation from fresh starts;
- representative death/checkpoint and special-mode validation;
- a clean 2-hour unattended soak;
- a clean 8-hour unattended soak;
- repeated fresh-boot graduation runs without human input.

Until those gates pass, “production-candidate” is the intentional maturity label.
