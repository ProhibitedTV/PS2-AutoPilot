# Need for Speed: Hot Pursuit 2 V11

V11 is the 1080p chase-camera safety release. It addresses the live failure where
the racer fell behind, lost the course, and alternated between walls.

V11.1 corrects the first live V11 regression: the four-second launch guard held the
stick at zero even after coherent vision reported a sustained bend. Launches now use
the normal predictive steering geometry with a strict ±0.36 cap while continuing to
hold throttle and suppress all launch braking.

V11.2 adds strict ownership for the alternate You're The Cop HUD. The first V11.1
follow-up reached a timed police pursuit, but the missing racer rank panel reduced HUD
confidence to 0.32 and left the car parked while unknown-screen bootstrap pressed
menu buttons. The fixed neon-green police-resource row now identifies that layout,
promotes it to `cop_racing`, and hands it to normal drive/recovery control.

## What the stopped V10 run proved

- The 12 Hz control loop remained responsive: the prior asynchronous template fix
  kept normal control decisions out of the roughly 650 ms template scan path.
- The rolling recovery-storm circuit breaker reached Restart Race instead of
  allowing an unlimited wall trap.
- The road model was the immediate failure. Its seed mapped to approximately
  74-82% of a 1080p frame, directly across the rear of the chase car. Car body,
  shoulder, and wall pixels could then form one falsely confident surface.

## V11 safety contract

- Road sampling is normalized and moved above the chase car.
- A normalized trapezoid masks the car body before connected-component scoring.
- Overwide and reverse-perspective surfaces fail closed with a recorded reason.
- Raw rejected width, coverage, and center-contact geometry remains in failure
  bundles for the next tuning pass.
- Unknown-screen road color cannot claim a race without fixed HUD ownership.
- Racer and You're The Cop HUD layouts both provide fixed gameplay ownership.
- The first four race seconds hold throttle and cap steering.
- Moving gameplay with blind road vision stays forward, rapidly decays steering,
  and requests Restart Race after seven seconds.
- Positive wrong-way evidence requests Restart Race; it does not attempt a blind
  U-turn.
- Normal image-derived steering is capped below recovery maneuver authority.

## Launch checklist

1. Pull `main` and run `bootstrap.cmd` so the editable environment and dependencies
   match the checkout.
2. Set the Windows desktop to 1920x1080 if that is the monitor's native resolution.
   Do not crop, cover, minimize, or move the PCSX2 render off-screen after startup.
3. Start PCSX2 and load Need for Speed: Hot Pursuit 2.
4. Run `ps2-autopilot-doctor --config config\nfs_hot_pursuit_2.yaml`.
5. Run `run-nfs24x7.cmd`.
6. Confirm state telemetry reports package `0.11.2` and NFS policy `11`.
7. Observe one launch. It should accelerate immediately with bounded steering. If
   road vision is rejected while the race HUD remains present, the console should
   report `v11 moving-blind`, followed by one bounded restart if blindness persists.

The detector is regression-tested at 640x360 and 1920x1080. Real failure frames are
negative-test inputs during development, but a clean live race remains the final
closed-loop acceptance test before another overnight soak.
