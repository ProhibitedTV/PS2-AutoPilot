# Need for Speed: Hot Pursuit 2 V8

V8 is the long-stall/24-7 recovery pass built from the first overnight failure corpus.

## Live failure that motivated V8

The retained overnight bundles showed an active World Racing race with the fixed HP2 HUD visible while the player's car remained at 0 mph against the same wall for more than eight hours. V7 fixes the ownership error by recognizing the fixed gameplay HUD even when pavement segmentation is blind.

V8 addresses what happens after gameplay ownership is retained: the profile must actually escape or abandon the dead race rather than make the same recovery forever.

## Recovery changes

### Shared watchdog joins the alternating recovery streak

The original context-free `recover()` method predated V4's wall-trap recovery escalation. A watchdog-triggered recovery could therefore bypass the streak bookkeeping and reuse one steering direction. V8 records the watchdog arm in the same streak model used by normal road-loss recovery, alternating steering direction on repeated nearby attempts.

### HUD-owned recovery remains gameplay

When a reverse/forward recovery completes but road segmentation is still unavailable, a strong fixed gameplay HUD keeps the profile in `RACING` ownership. The road-loss timer can then schedule another bounded escape attempt instead of dropping through `CALIBRATION`/menu bootstrap.

### Hard-stall Restart Race fallback

A 24/7 stream must eventually abandon a race it cannot free itself from. V8 adds a last-resort restart ladder. It is eligible only when all of the following remain true:

- fixed gameplay HUD is strong;
- road confidence is below driving confidence;
- motion is near zero;
- at least four HUD-owned recovery attempts have failed;
- the stall has persisted for at least 28 seconds.

The fallback uses HP2's documented Pause Menu order: `Start` opens Pause, one `Down` selects **Restart Race**, then `Confirm` requests the restart. A second bounded Confirm handles regional/settings variants that show an additional confirmation. The policy then waits for visual progress and hands ownership back to the normal lifecycle/bootstrap system.

The hard restart is not used for normal cornering, brief crashes, menu uncertainty, or a single failed recovery.

## Default V8 settings

```yaml
hud_gameplay_enabled: true
hud_gameplay_threshold: 0.82
hud_stall_motion_threshold: 0.0035
hud_stall_reset_motion: 0.020
hud_stall_hard_restart_enabled: true
hud_stall_hard_restart_recoveries: 4
hud_stall_hard_restart_seconds: 28.0
hard_restart_action_seconds: 0.75
hard_restart_wait_seconds: 7.0
hard_restart_progress_delta: 0.025
```

## Telemetry

V8 adds:

- `nfs_policy_version=8`
- `nfs_hud_stall_age`
- `nfs_hud_stall_recovery_attempts`
- `nfs_hud_watchdog_recovery_arms`
- `nfs_hard_restart_stage`
- `nfs_hard_restart_attempts`
- `nfs_hard_restart_inputs`
- `nfs_hard_restart_successes`
- `nfs_hard_restart_failures`

## Live acceptance gates

V8 remains `diagnostic` until retained live evidence shows:

1. a wall/guardrail stall stays HUD-owned as gameplay;
2. repeated recoveries alternate steering and can free ordinary traps;
3. if recovery cannot free the car, the hard-stall threshold opens Pause and selects Restart Race;
4. the restarted race returns through loading/countdown into active driving;
5. normal gameplay never triggers the hard restart;
6. results/replay/menu screens are not misclassified as HUD-owned gameplay;
7. cold boot -> race -> finish -> next race survives multiple consecutive races;
8. a multi-hour soak contains no permanent wall park, UNKNOWN deadlock or runaway input loop.
