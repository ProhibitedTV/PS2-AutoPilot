# Jak V9 live findings

The 2026-08-23 Geyser Rock wall/foliage run supplied a much better calibration point than the earlier open-ocean failure.

## What the runtime did

After the mid-session camera probe reacquired gameplay, the V7 shoreline policy immediately saw roughly `L/C/R=0.17/0.25/0.23` and entered WATER backtrack. It later spent long stretches in `WATER shoreline seek` while scene motion fell toward zero. Near the end of the supplied event window the agent was still issuing shoreline commands with reported water around `0.12-0.13`, but motion was only about `0.0005-0.0012` and `still_seconds` reached ~25 seconds before the global L1 watchdog recovery finally fired.

The input trace also showed the shoreline steering direction changing sign repeatedly while the visual situation had not materially improved. That is expected from V7 because the drier-side direction could be recomputed from tiny left/right ratio changes every frame.

## Frame calibration

Running V7's HSV candidate mask against the supplied 1920x1080 wall/leaf frame produced approximately:

- raw blue/cyan candidate ratio: `0.1206`
- largest connected candidate component: about `0.0134` of the ROI at native resolution
- largest component width: about `0.096` of the ROI
- maximum per-row candidate coverage: below `0.25`

Visually, the frame contains rock, grass, foliage and Jak -- not an open water surface. The important lesson is that **blue/cyan pixel count is a useful cue but not a sufficient scene classification**.

## V9 response

V9 adds two closed-loop layers:

1. **Geometry-aware water ownership.** The existing HSV mask is retained, but shoreline avoidance now also requires coherent connected/horizontal structure. Fragmented blue rock and foliage can increment false-color telemetry without taking over navigation. Water direction also has hysteresis so near-tied left/right ratios cannot flip the escape direction every frame.
2. **Local obstacle recovery.** Once locomotion has been commanded, sustained near-zero motion for only a few seconds triggers a bounded reverse -> pivot -> forward-test sequence. A failed test tries the opposite direction. Only after non-jump escapes fail, and only when coherent water is absent, may the local cycle escalate to a jump-turn. Successful directions are held briefly as route memory so normal exploration does not immediately drive back into the same wall.

New telemetry includes `jak_policy_version=v9`, geometry metrics for the water candidate, false-color frame counts, water direction flips, local-stuck stage/timers, escape success/failure counts and route bias.
