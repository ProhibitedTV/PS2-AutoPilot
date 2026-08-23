# Jak V11 validation

This lightweight validation commit exists to run the full pull-request CI suite against the V11 live-calibrated runtime already present on `main`.

Validation criteria:

- package install succeeds;
- all Python sources compile;
- existing V1-V10 regressions remain green;
- V11 adaptive attach tests confirm that repeated `~0.006` camera response from a `~0.0015` idle baseline can regain gameplay ownership while sub-threshold animation remains fail-closed;
- sticky gameplay reacquisition uses the same adaptive threshold;
- the public `jak_and_daxter` registry resolves to V11.

Final calibration uses a `0.0036` motion floor plus `baseline * 1.25 + 0.0016`, derived from 318 deduplicated live probe cycles. This branch-only line triggers CI against that final tree.
