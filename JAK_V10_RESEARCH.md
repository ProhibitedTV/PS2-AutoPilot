# Jak V10 gameplay research

V10 shifts the goal from merely staying alive to producing gameplay that looks recognizably like a person playing *The Precursor Legacy*.

## Verified control/action vocabulary

Multiple guides and the Jak community control reference agree on the important on-foot moves:

- `Circle`: spin kick. Useful as a broad moving attack and for ordinary breakables.
- `Square`: lunging punch.
- `Cross, Cross`: double jump.
- moving + `L1/R1`, then `Cross`: roll jump. It covers substantially more ground than a normal jump and is core traversal tech.
- jump, then `Square`: dive attack. This is the special move used to break Scout Fly boxes.
- `L2/R2`: briefly exposes HUD/progress information.
- `Triangle`: first-person/goggle view; movement is unavailable while using it.

V10 therefore prefers a low-risk moving spin for ambient combat/breakables, reserves dive attacks for a visual Scout Fly-box cue, and only uses roll jumps when V9 reports dry, stable, mostly straight travel.

References:
- https://jakanddaxter.fandom.com/wiki/The_Precursor_Legacy_controls
- https://strategywiki.org/wiki/Jak_and_Daxter:_The_Precursor_Legacy/Controls
- https://gamefaqs.gamespot.com/ps2/516509-jak-and-daxter-the-precursor-legacy/faqs/45484

## Why Blue Eco is a high-value visual target

Blue Eco is unusually valuable to an autonomous policy because it does several useful things at once: it increases movement speed, activates Precursor doors/devices, and attracts nearby collectibles such as Precursor Orbs and Scout Flies. Geyser Rock explicitly teaches the player to charge at a Blue Eco vent and return to the nearby Precursor door.

V10 adds a conservative bright-cyan/white energy cue. It is allowed to steer only after V9's geometry-aware water classifier has declined shoreline ownership, preventing the original "blue means ocean" failure mode from returning through a different detector.

References:
- https://jakdaxter.neoseeker.com/wiki/Jak_and_Daxter:_The_Precursor_Legacy
- https://strategywiki.org/wiki/Jak_and_Daxter:_The_Precursor_Legacy/Geyser_Rock

## Geyser Rock gives us a useful deterministic proving ground

The tutorial area has four Power Cells and 50 Precursor Orbs. Its four cell objectives are:

1. Find the cell on the path.
2. Open the Precursor door using Blue Eco.
3. Climb the cliff.
4. Free all seven Scout Flies.

All seven Scout Fly boxes are grouped in the tutorial area. This makes Geyser Rock an unusually good place to validate the V10 red/gray box cue and dive-attack transaction before relying on it in later, more dangerous levels.

References:
- https://strategywiki.org/wiki/Jak_and_Daxter:_The_Precursor_Legacy/Walkthrough
- https://strategywiki.org/wiki/Jak_and_Daxter:_The_Precursor_Legacy/Geyser_Rock

## Longer-term state-awareness opportunity

OpenGOAL's Jak 1 source documentation confirms that the underlying game has much richer semantic state than screenshots expose. The target/player structure has a `current-level` field; the progress system tracks total Power Cells, Orbs and Scout Flies plus level/task information; and the engine can address entities by name/type/position.

That does **not** automatically give the current PCSX2 build those values, but it gives us a concrete reverse-engineering map for a future optional telemetry path. A state bridge could eventually make the policy know the current level, player position/state, task progression and nearby named entities while vision remains the independent safety/verification channel.

References:
- https://opengoal.dev/docs/source-docs/jak1/packages/engine/target/
- https://opengoal.dev/docs/source-docs/jak1/packages/engine/ui/progress/
- https://opengoal.dev/docs/source-docs/jak1/packages/engine/entity/
- https://opengoal.dev/docs/source-docs/jak1/symbol-index/

## V10 implementation

V10 adds:

- Scout Fly box visual cue -> approach -> jump -> dive-attack transaction.
- Blue Eco energy cue -> bounded steering toward the energy source.
- Periodic moving spin attacks during ordinary traversal.
- Guarded roll-jump traversal on dry, already-moving, mostly straight routes.
- Stream telemetry for current cue, confidence, skill/stage, Scout Fly dives, Blue Eco seeks, roll jumps and moving spin attacks.
- Overlay intent labels so the audience can see *why* the controller is doing something.

All V10 skills yield immediately to V9 coherent-water avoidance and local obstacle recovery.

## Next research / implementation targets

1. Calibrate Scout Fly-box and Blue Eco thresholds from real capture bundles instead of synthetic tests alone.
2. Add objective awareness from the Progress screen: current area, discovered/completed Power Cell tasks, and missing collectibles.
3. Add Power Cell / Precursor Orb visual attraction with temporal confirmation and reward validation.
4. Add enemy/contact perception and combat modes rather than relying only on moving spin clears.
5. Add cliff/gap/platform affordance perception so roll jumps and double jumps can be selected from geometry rather than cadence.
6. Prototype an optional semantic-state bridge using OpenGOAL's documented Jak 1 structures as the reverse-engineering reference, while keeping screen vision as a safety check.
