# Game Profiles

PS2 AutoPilot is a multi-game PCSX2 runtime. Game-specific behavior lives behind a registered profile; the capture, controller, supervision, retention, logging, and overlay plumbing stay shared.

## Registered games

| Profile | Game | Maturity | Config |
| --- | --- | --- | --- |
| `madden2005` | Madden NFL 2005 | soak-tested | `config/madden2005.yaml` |
| `jak_and_daxter` | Jak and Daxter: The Precursor Legacy | calibration | `config/jak_and_daxter.yaml` |
| `generic_chaos` | Generic smoke test | diagnostic | custom |

List the registry at any time:

```bat
ps2-autopilot --list-profiles
```

The Madden implementation remains versioned and intact. `madden2005` continues to resolve to the latest validated Madden profile (V22 as of v0.8.0); adding another game does not reuse or rewrite its football state machine.

## Run one game

Madden remains the backwards-compatible default:

```bat
run24x7.cmd
```

Explicit Madden config:

```bat
run24x7.cmd config\madden2005.yaml
```

Jak and Daxter:

```bat
run24x7.cmd config\jak_and_daxter.yaml
```

The runner clears the previous runtime session once when manually launched, then preserves current-session crash evidence across supervised restarts.

## Jak and Daxter calibration workflow

The first Jak profile deliberately starts in `mode: observe`. It captures normal runtime telemetry and calibrated-template matches while keeping the virtual pad neutral. This avoids using Madden assumptions or random generic inputs in a 3D platformer.

Start PCSX2 with your own legally obtained Jak and Daxter dump, then:

```bat
ps2-autopilot-doctor --config config\jak_and_daxter.yaml
run24x7.cmd config\jak_and_daxter.yaml
```

Capture representative states while you manually play/navigate:

```bat
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_title --series 3
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_main_menu --series 3
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_gameplay --series 5
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_cutscene --series 5
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_pause --series 3
ps2-autopilot-capture --config config\jak_and_daxter.yaml --label jak_death --series 3
```

Local PNG templates are gitignored because they contain copyrighted game imagery.

Once the state boundary is reliable, change `mode: observe` to `mode: explore`. Explore mode only drives analog locomotion when a high-confidence calibrated gameplay template is present. Unknown states and cutscenes fail closed and hold inputs. Pause/death/menu actions likewise require calibrated state evidence.

## Why Jak is a separate policy

Madden is dominated by discrete menus, play phases, scoreboard OCR, possession inference and short bounded controller actions. Jak is a continuous third-person platformer. Its later milestones therefore need different perception and policy layers:

- player/world localization and traversable-space estimation
- continuous left-stick locomotion and camera steering
- ledge, gap and jump timing
- enemy and hazard perception
- attack/evasion policy
- collectible and objective-progress signals
- death/checkpoint detection and recovery
- cutscene/dialog preservation
- anti-loop exploration memory so it does not pace the same patch of terrain forever

Those systems should live in Jak-specific modules rather than accumulating conditionals inside the Madden stack.
