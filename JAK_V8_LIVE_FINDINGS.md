# Jak V8 live findings

The first autonomous Geyser Rock traversal exposed two failure modes that are now guarded in V8.

1. **Visual-only main-menu false positive during gameplay.** Tropical foliage can place lime/green pixels inside the calibrated NEW GAME ROI while the other menu ROIs remain relatively dark. The older visual fallback could therefore report `main_menu_detection_source=visual-fallback` during gameplay and repeatedly issue NEW GAME confirms. V8 treats gameplay ownership as sticky and disables visual-only main-menu ownership after gameplay has been proven. A real post-gameplay menu still needs semantic OCR evidence.

2. **Static camera occlusion after gameplay ownership expires.** When Jak walks behind a tree, rock, or dense foliage, the frame can become nearly static. Once the short gameplay grace window expires, the previous fail-closed policy correctly stops moving but has no evidence-driven way to resume. V8 uses a reversible right-stick camera probe. A measurable camera response proves the frame is still player-controlled gameplay; only then does V8 issue a short reverse+turn escape. No response remains neutral so cutscenes and unknown menus are preserved.

The V8 telemetry includes `jak_policy_version=v8`, sticky gameplay-session state, lost-gameplay probe attempts/successes, visual-menu suppressions, and post-reacquire escape counters. These fields make it obvious whether a live process has actually loaded V8 rather than an older installed profile.

## Live validation signal

After redeploy, `runtime/verbose.jsonl` should contain `jak_policy_version: v8`. A foliage/wall lock should transition through `lost-gameplay probe; nudge camera only`, then either safely remain neutral if the camera does not respond or emit `gameplay reacquired; back-turn away from camera occluder` after a verified camera response. A post-gameplay foliage false positive should increment `jak_visual_menu_suppressions` instead of pressing Cross.
