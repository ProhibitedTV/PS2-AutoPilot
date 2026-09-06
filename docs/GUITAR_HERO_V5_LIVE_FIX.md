# Guitar Hero V5 live input fix

The retained GH1 first-run capture proved an important distinction between **song note
mapping** and **menu confirmation** on the current PCSX2 virtual-controller path.

- Gameplay lanes remain: Green=L2, Red=L1, Yellow=R1, Blue=R2, Orange=Cross.
- Normal AutoPilot menu confirmation is `confirm`, which the virtual-gamepad backend maps
  to Xbox A / PS2 Cross.
- The `USING THE GUITAR CONTROLLER` card prints `Continue (Green button)`, but repeated
  virtual L2 attempts did not advance the live game. V5 therefore sends `confirm`/Cross
  first, then retains L2 as a bounded fallback if the frame does not change.

This is intentionally evidence-driven. The profile does **not** change the five gameplay
lane bindings based on this menu behavior. Live note-highway acceptance will independently
verify that PCSX2 receives L2/L1/R1/R2/Cross correctly during a song.
