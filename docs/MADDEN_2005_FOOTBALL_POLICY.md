# Madden NFL 2005 football-policy notes

This document records the game-specific research behind the autonomous Madden
pathway. The goal is not merely to survive menus and finish games; the controller
policy should resemble coherent football while staying honest about what the
current perception stack can and cannot know.

## Primary game-specific references

- AJE, *Madden NFL 2005 Strategy Guide* (PlayStation 2), GameFAQs:
  https://gamefaqs.gamespot.com/ps2/919804-madden-nfl-2005/faqs/31779
- Aurabolt10, *Madden NFL 2005 Strategy Guide*, GameFAQs:
  https://gamefaqs.gamespot.com/gamecube/919803-madden-nfl-2005/faqs/33167

These are contemporary community strategy guides rather than official coaching
manuals. We use them mainly for Madden-specific controls/mechanics and to identify
play patterns that made sense in the 2004 game engine. General down-and-distance
logic remains conservative football heuristics in our own code.

## Confirmed PS2 control semantics that matter to the policy

### Rushing

- X: sprint / speed burst
- Square: dive
- Triangle: cover/protect ball
- Circle: spin
- L1/R1: juke left/right
- L2/R2: stiff arm left/right

### Passing / receiving

- X after the snap: bring up passing icons
- X, Square, Circle, L1, R1: receiver icons
- tap receiver icon: lob
- hold receiver icon: bullet
- Triangle: throw away while passing / catch while receiving
- X: take control of intended receiver
- Circle: receiver sprint

### Defense

- X: switch to player nearest the ball
- Square: dive tackle
- Circle: sprint / shove blocker
- Triangle: jump / intercept / hands up
- R2: strip attempt
- L1/R1: line/rush moves (context dependent)
- right analog: Hit Stick

The Hit Stick and strip mechanics are deliberately high-risk. The policy should
not emit them simply to create visual variety.

## Football principles implemented in V26/V27

### 1. Down-and-distance drives intent

The policy no longer treats run/pass as a near coin flip on every down.

- first down: balanced
- second-and-short: preserve a real pass/shot opportunity
- second-and-long: pass lean
- third-and-short: credible run/pass conversion mix
- third-and-medium/long: high-percentage pass / required pass
- fourth-and-1: conversion remains plausible
- longer fourth downs: prefer a visible Punt or Field Goal card when Madden offers it

The offense keeps a short history of recent intents so it can break obvious
run-run or pass-pass tendencies without becoming random.

### 2. Select named plays, not blind face buttons

When OCR can read the three visible play cards, the controller scores the names
against the current football intent. Examples:

- runs: Dive, Iso, Slam, Draw, Toss, Sweep, Counter, Stretch, Trap, Power
- passes: Slants, Cross, Curl, Hitch, Post, Corner, Drag, Screen, Fade, Seam,
  Verticals, Flat, Comeback
- defense: coverage names such as Cover/Zone/Man versus pressure names such as
  Blitz/Dog/Fire/Stunt

The face button is inferred from the card's horizontal screen position. If the
screen is still at formation level, the policy enters the formation, waits for a
fresh OCR result, and then selects the play. It does not queue both choices as one
blind macro.

When OCR can neither identify named cards nor a formation, V29 no longer immediately
falls back to the early random formation macro if the active-game visual evidence
proves Madden's play-call screen is present. It invokes **Ask Madden once** and
rescans. If the three recommendation names are still unreadable, the controller
chooses only among those three Madden-recommended cards. This delegates personnel
and formation sanity to the game's own coach AI instead of inventing a read.

### 3. Let run blocking develop

The contemporary guides repeatedly emphasize following lead blockers and not
immediately outrunning the designed lane. V26 therefore keeps the runner moving
north/south at reduced commitment for a short backfield window before opening the
sprint/special-move cadence.

Random ordinary-down dives were removed. Square is reserved for goal-line and
short-yardage marker situations.

### 4. Pass concepts get different timing

V27 classifies the selected play name into three broad concepts:

- quick: Slants, Drag, Flat, Screen, Hitch, etc.
- intermediate: default bucket
- shot: Verticals, Post, Corner, Fade, Seam, etc.

Quick concepts release earlier. Shot concepts are allowed more route-development
time. Quick/intermediate throws use a firmer receiver-button hold while shot plays
use a tap, reflecting Madden 2005's bullet-versus-lob input mechanic.

This is still intentionally modest: current perception cannot prove which receiver
is open, so receiver-button choice remains bounded variation with immediate-repeat
avoidance. A future receiver/icon association layer should replace that remaining
random choice.

### 5. Defense closes space before gambling

When spatial confidence is poor, the V23 uncertainty gate remains authoritative:
steer, switch nearest once, and sprint/close space without contact RNG.

When both the controlled defender and action target are confidently tracked, V27
steers directly toward the target and still prioritizes pursuit. Routine contact
uses sprint/shove and natural collision tackling. A Square dive tackle is reserved
for a very close third/fourth-and-short or goal-line situation.

Strip, Hit Stick, jump-at-ball, and rush-move inputs are not part of routine
verified contact because the current perception stack does not yet know enough to
justify those gambles.

## Live evidence hardening in V28/V29

A live V27 run produced a useful UNKNOWN stack rather than a synthetic test case.
Loading/player-card, weather, and stadium presentation were correctly protected by
the existing pregame latch. Two failure classes were actionable:

1. the Madden title prompt sometimes OCRs as compact `PRESSSTART BUTTON`, or even as
   separate `PRESS` and `BUTTON` tokens while `MADDEN` and `2005` remain visible;
2. active defensive formation screens sometimes lose all useful play-call OCR even
   though the lower playbook UI is visually stable.

V28 reacquires TITLE/MAIN_MENU from compact root evidence. V29 extends degraded title
recovery and adds a two-factor visual play-call detector: the characteristic red-left
and dark-right lower playbook chrome is trusted only while V21 already knows a game is
active and the semantic classifier is UNKNOWN/DIALOG. Recognized PAUSED, FINAL,
KICKING, normal presentation, and other semantic states remain authoritative.

The live Buffalo/Miami captures calibrated the initial visual thresholds around a
very strong lower-left red ratio (~0.81) and lower-right dark ratio (~0.79). Defaults
are intentionally lower (0.58 / 0.66) to tolerate capture variance while still
requiring the active-game latch as a second factor.

## Explicit perception limits

The current system does **not** claim to know:

- receiver identity or receiver-icon ownership
- whether a receiver is actually open
- man versus zone from player assignment geometry
- offensive personnel count with enough confidence to choose 4-3/Nickel/Dime
  solely from personnel
- exact field position / field-goal range on every fourth down
- tackle approach angle or defender leverage well enough to pick a juke/stiff-arm
  from geometry alone

The live defensive play-call screenshots visibly contain WR/TE/RB personnel counts,
but the current global OCR did not reliably recover those small labels. V29 therefore
uses Ask Madden as the honest fallback rather than claiming to have parsed personnel.
Where facts are unavailable, the football layer uses conservative heuristics,
game-native recommendations, and telemetry rather than inventing certainty.

## Next research/calibration targets

1. Add a targeted OCR/read path for the fixed WR/TE/RB personnel strip on defensive
   play-call screens so base/Nickel/Dime/Quarter choice can become explicit.
2. Capture Ask Madden recommendation screens to validate card OCR and confirm the
   left/middle/right Square/Cross/Circle mapping under the new fallback.
3. Associate visible receiver icons with tracked players after X brings up passing
   icons; use separation/nearby-defender geometry for an actual read progression.
4. Parse score and field position reliably enough to add clock management,
   two-minute offense, four-minute offense, and field-goal-range decisions.
5. Calibrate defender-target distance against real tackle animations before
   expanding beyond the conservative contact policy.
