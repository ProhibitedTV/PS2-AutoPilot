# Jak V11 live findings

The `failures.zip` soak bundle from 2026-08-23 contained 30 watchdog failure bundles spanning roughly 23:09-23:31 UTC. Every captured frame showed Jak still in the same Geyser Rock water pocket facing the same cliff. The important failure was not shoreline routing itself: the profile never regained gameplay ownership after the AutoPilot restart.

## What the logs showed

The inherited mid-session camera probe repeated the same cycle for the entire bundle:

1. `mid-session gameplay probe; nudge camera`
2. `observe response`
3. `waiting for camera motion`
4. `production unknown; fail closed / hold inputs`
5. eventually `watchdog neutral hold; await calibrated recovery state`

The probe responses were real but small. Typical idle motion was roughly `0.001-0.003`, while right-stick camera pulses repeatedly produced about `0.004-0.007`. The configured absolute response floor was `0.018`, so no amount of repeatability could ever satisfy the ownership gate. The run accumulated 36 watchdog recoveries while visually remaining in the same swim state.

## Frame calibration

The final failure frame is overwhelmingly coherent water in the lower gameplay ROI. Re-running the V9 geometry calculation against that capture produced approximately:

- raw water candidate ratio: `0.856`
- largest connected component ratio: `0.856`
- largest component width: `1.0` of the ROI
- component coherence: `1.0`
- maximum row coverage: `1.0`

This is the opposite of the earlier foliage false-positive: here the geometry strongly supports a real water state. It still must not grant gameplay ownership by itself, but it is useful context when combined with repeatable controller-correlated camera response.

## V11 response

V11 replaces the brittle absolute attach threshold with live-calibrated adaptive evidence:

- response threshold defaults to `max(0.0042, baseline * 1.30 + 0.0020)`;
- successful camera pulses accumulate evidence across opposite-direction probes;
- one weak/missed probe decays evidence rather than erasing the entire history;
- evidence expires after a short window so unrelated scene motion cannot accumulate forever;
- strongly coherent water reduces the required evidence modestly, but never grants ownership without a camera response;
- the same adaptive threshold is used when an already-proven gameplay session temporarily falls back to UNKNOWN.

Expected live signal after deployment is `jak_policy_version=v11`, followed within seconds by `adaptive attach evidence ...` and then `adaptive camera-response evidence confirmed -> GAMEPLAY`. From there V9's coherent-water escape should immediately own movement instead of allowing another 25-minute UNKNOWN loop.
