# Jak V12 live findings

The V11 soak on 2026-08-23 proved the adaptive gameplay-ownership fix worked, then exposed the next bottleneck: water recovery itself.

## What the soak actually did

The uploaded `verbose(2).jsonl` contained 906 verbose snapshots from roughly 18:07 to 18:23 local time. V11 was loaded throughout. After two adaptive camera-probe hits, the profile successfully attached to gameplay and coherent-water safety took ownership.

From that point the inherited water routine dominated:

- 885 verbose snapshots reported `WATER shoreline seek`;
- there was only one initial backtrack and two initial turn snapshots before the indefinite seek phase;
- three global watchdog recoveries fired while Jak remained in the water;
- V9 local obstacle recovery never triggered, because animated water produced enough optical motion to keep its generic motion gate from considering Jak stationary;
- the run exceeded ten thousand water-escape ticks.

The three failure screenshots show the same basic state: Jak surface-swimming directly into a vertical Geyser Rock cliff while the routine continued to arc along the same shoreline hypothesis.

## The missed exit opportunity

The important signal is that the water heuristic *did* find a much drier view. While seeking right, total water coverage fell from roughly 0.98 to a minimum of about **0.062**, with center coverage around **0.124** and the right sector at **0.0**. The old policy had no concept of a near-shore state, so it never changed behavior. It kept swimming, eventually lost that favorable geometry and later flipped directions back into much higher water coverage.

This is a controller-policy failure rather than a perception failure: V9 correctly recognized coherent water, V11 correctly recognized gameplay, but V7's old water action was an open-loop arc with no outcome validation.

## Game-specific constraint

In *The Precursor Legacy*, Jak can jump with Cross while surface-swimming. Square and Circle cause him to dive while swimming. Deep water is also dangerous because of Lurker sharks. Therefore V12 treats water as urgent safety, reserves Cross for visually gated shore-exit attempts, and does not use Square/Circle while coherent-water escape owns the controller.

References:

- StrategyWiki, *The Precursor Legacy/Controls*: Square/Circle dive while swimming; Cross jumps.
- GameFAQs AmericanArsenal guide, `SWIMMING`: Jak can still jump with X at the surface; Square dives; Lurker sharks make deep-water exposure dangerous.
- StrategyWiki, *Geyser Rock*: the tutorial island's Lurker shark is avoided by staying out of deep water.

## V12 response

V12 replaces the infinite shoreline arc with a bounded closed-loop state machine:

1. backtrack immediately on unexpected water entry;
2. seek a heading while measuring per-leg best water coverage;
3. retain headings that actually reduce water coverage instead of following every noisy left/right ratio change;
4. if water coverage stops improving for several seconds, or the frame remains almost static, execute a deliberate U-turn and start a fresh leg;
5. when total/center water become low and one side is mostly dry, apply forward pressure and repeated Cross surface-jumps to climb out;
6. if the shore-hop fails, U-turn and search another heading;
7. if the global watchdog fires while water owns control, schedule the same water-aware U-turn instead of injecting the generic land recovery ladder.

New telemetry includes `jak_water_nav_mode`, `jak_water_best_ratio`, `jak_water_leg_best_ratio`, `jak_water_progress_age`, `jak_water_near_shore`, U-turn/shore-hop counts and water-specific watchdog recoveries.
