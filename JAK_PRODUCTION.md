# Jak and Daxter: The Precursor Legacy — production runtime

`jak_and_daxter` is game #2 for PS2 AutoPilot. Madden NFL 2005 remains isolated as the soak-tested game #1 profile; none of the Jak policy below is shared with Madden.

## What the game requires

The Precursor Legacy is not a single-control-mode game. On foot, Jak uses continuous left-stick locomotion and right-stick camera control, with jump/double-jump, crouch/high-jump, roll/roll-jump, punch and 360-degree spin. The same campaign later puts the player on the A-Grav Zoomer, on Flut Flut, behind a cannon, and into special minigames such as fishing. These contexts reuse buttons with different semantics, so the production policy must classify the active control mode before issuing aggressive inputs.

Persistent progress is centered on Power Cells, Precursor Orbs and Scout Flies. Seven Scout Flies in an area award a Power Cell, Orbs can be exchanged for Cells, and Cell counts gate later regions. The production runtime therefore treats collectible totals as reward/progress evidence instead of treating raw screen motion as success.

Useful reference points encoded in `jak_knowledge.py`:

- on-foot: left stick move, right stick camera, Cross jump/double jump, L1/R1 crouch/roll, Square punch, Circle spin, L2/R2 stats HUD, Start status/pause;
- A-Grav Zoomer: left stick steer/pitch, right stick camera, Cross accelerate, L1/R1 hop/tight turn;
- Flut Flut: left stick move, right stick camera, Cross jump/flutter, Square headbutt;
- cannon: left stick aim, Cross fire/charge, Triangle exit;
- only the uncontroversial progression gates of 20 Cells (Fire Canyon) and 45 Cells (Mountain Pass) are encoded as hard milestones. Old guides disagree about the late Lava Tube quota, so that remains observation-driven.

## v0.9 production-candidate policy

The registered Jak profile is V3. It preserves the V2 verified boot path (`PRESS START`, then visually verified green `NEW GAME`) and adds:

- explicit on-foot / Zoomer / Flut Flut / cannon / fishing control modes;
- continuous analog movement and camera steering;
- bounded jump/double-jump behavior and a gameplay-only recovery ladder using camera redirects, double-jumps, roll-jumps and spin redirects;
- short-term scene signatures to detect visual loops and repeated viewpoints;
- center/lower-frame motion measurements to distinguish movement from stagnation;
- periodic R2 progress-HUD probes and monotonic OCR tracking for Power Cells, Orbs and Scout Flies;
- a gameplay ambiguity grace window so one weak frame cannot instantly steal controller ownership;
- hands-off behavior for cutscenes, unknown menus and uncalibrated special states;
- control-mode-specific safe behavior for Zoomer, Flut Flut and cannon;
- fishing recognized as a distinct mode but intentionally held neutral until fish/eel perception exists.

The default `config/jak_and_daxter.yaml` now uses `mode: production` and `run-jak24x7.cmd` is the dedicated supervised entry point.

## Safety boundaries

Production does **not** mean "press buttons through every unknown screen." The boot sequence contains at least one live-calibration gap: after New Game the original game asks the player to choose a save file before the opening cutscenes. Until a real PS2/PCSX2 capture establishes the exact slot-selection semantics, that screen remains fail-closed.

The long opening presentation is also protected. A timer may be used only as a conservative lower bound; an unknown screen cannot be promoted to gameplay unless the timer has elapsed, the frame is moving, and OCR does not contain save/menu/file markers. A real calibrated gameplay template always outranks that fallback.

The raw motion watchdog may perform active escape inputs only while the profile has explicit gameplay ownership. In menus, save screens, cutscenes and other unknown states it releases/neutralizes the controller instead.

## Production acceptance

Before calling Jak "soak-tested" rather than "production-candidate", collect real templates/captures for:

- the New Game save-file selector and load/save variants;
- Geyser Rock on-foot gameplay from multiple camera angles;
- opening and ordinary cutscenes/dialog;
- pause/status HUD;
- death/checkpoint respawn;
- A-Grav Zoomer, Flut Flut and cannon transitions;
- representative hazards/ledges and enemies;
- fishing, including green/gold fish versus poison eels.

Then complete a 2-hour unattended soak followed by an 8-hour soak with no blind menu confirms, no persistent controller holds, bounded recovery frequency, and evidence that progress/scene novelty advances over time.

## External research references

Research used for this production pass included the Jak and Daxter Wiki control reference, StrategyWiki controls, multiple GameFAQs walkthroughs, and the Archipelago Jak setup guide. These sources agree on the core control schemes and on New Game requiring save-file selection before the opening sequence. Their late-game progression counts are not always consistent, so disputed values are deliberately not used as hard runtime truth.
