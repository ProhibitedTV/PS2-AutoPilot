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

If OCR is not trustworthy, the proven legacy path remains the fallback. Reliability
still outranks cleverness.

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

Where those facts are unavailable, the football layer uses conservative heuristics
and telemetry rather than inventing certainty.

## Next research/calibration targets

1. Capture play-call screenshots from several offensive and defensive formations to
   validate left/middle/right card-to-button mapping and OCR play-name quality.
2. Associate visible receiver icons with tracked players after X brings up passing
   icons; use separation/nearby-defender geometry for an actual read progression.
3. Parse score and field position reliably enough to add clock management,
   two-minute offense, four-minute offense, and field-goal-range decisions.
4. Infer offensive personnel/receiver count at the defensive play-call screen so
   base/Nickel/Dime/Quarter personnel can follow the game's own matchup guidance.
5. Calibrate defender-target distance against real tackle animations before
   expanding beyond the conservative contact policy.
