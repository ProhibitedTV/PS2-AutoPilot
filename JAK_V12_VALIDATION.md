# Jak V12 validation

PR validation marker for the V12 closed-loop water escape built from the 2026-08-23 V11 soak.

Validation criteria:

- package install and compile succeed;
- all previous Jak regressions remain green;
- the public Jak profile resolves to V12;
- near-shore live-scale ratios switch to a Cross-only surface-jump exit attempt;
- low-motion or no-progress swimming triggers a bounded U-turn instead of indefinite shoreline seek;
- water-owned watchdog recovery schedules water navigation rather than generic land combos;
- coherent-water ownership cancels unsafe skill state that could otherwise press Square/Circle while swimming.

This branch exists only to execute the pull-request CI workflow against the complete V12 tree already staged on `main`.
