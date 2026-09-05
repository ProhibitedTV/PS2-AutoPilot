# Guitar Hero V2 — unattended streaming profile

`guitar_hero` targets the original **Guitar Hero (2005), NTSC-U SLUS-21224** in PCSX2.
It is intentionally a Quick Play streaming profile first: boot the game, own save/title
prompts, enter Quick Play, choose a setlist song and difficulty, preserve loading/venue
presentation, play the note highway, advance results, and move to the next song.

## Why the DualShock path is useful

The original PS2 game maps the five notes directly to the normal controller:

| Lane | PS2 input | AutoPilot action |
| --- | --- | --- |
| Green | L2 | `l2` |
| Red | L1 | `l1` |
| Yellow | R1 | `r1` |
| Blue | R2 | `r2` |
| Orange | Cross | `cross` |

On the DualShock path there is no separate strum press. That lets AutoPilot hit a note
with a short non-blocking button hold, hit chords by holding multiple note actions in the
same control tick, and keep long notes held. The left analog stick is used as whammy on
visually confirmed sustains. `Select` Star Power support exists but is disabled in the
shipping config until live meter/timing evidence is retained.

## Menu ownership

The main menu is the original five-row `Career / Quick Play / Multiplayer / Tutorials /
Options` layout. V2 targets **Quick Play**. Named templates are authoritative when they
exist; conservative image semantics provide a template-free fallback for the first live
bring-up:

- save/no-save prompt: centered green YES evidence, one Confirm
- title: large logo + "press any button" style prompt, one Start
- main menu: five-row highlighted list, route selected row to Quick Play
- setlist: notebook-paper + blue selected-song semantics, optionally advance one song
- difficulty: four-row highlighted list, route current row to configured difficulty
- results/high-score: bounded one-at-a-time Confirm transactions, never an input flood
- failed song: Confirm the default Retry path for stream continuity

Every menu input waits for either visual progress or a bounded transaction timeout before
the next input. The policy also carries an explicit route stage (`boot -> main -> setlist ->
difficulty -> song -> gameplay -> post_song`) so visually similar text menus cannot steal
ownership from the screen expected by the current lifecycle. Unknown static screens after
menu ownership **fail closed** and are retained by normal runtime observability instead of
receiving random controller probes.

## Cutscenes, loading, and presentation

This profile treats presentation as stream content. Any unknown moving screen that is not
a recognized note highway/menu is owned as presentation and receives **no input**. After a
song launch, even a static loading/venue card is input-silent until gameplay appears. After
the highway disappears, static frames also remain presentation until positive
results/high-score/failed evidence appears; the profile does not assume that a held venue
or celebration frame is safe to skip. The profile's watchdog recovery is also suppressed
while gameplay, loading/presentation, or post-song results own the screen. The config
therefore uses a long global stuck timeout and a practically disabled savestate-reload
threshold.

This matters for GH1 because boot/intro presentation, venue fly-ins, the concert itself,
and the post-song celebration are exactly what should remain visible on an unattended
stream.

## Note-highway vision

V2 does not hard-code desktop pixels. Each 60 Hz policy tick downsizes the captured render
to a 640x480 working frame and searches the lower fretboard for the five colored receptor
rings. A coherent receptor row establishes lane X positions and the strike Y coordinate.
Color masks then inspect a narrow band immediately above each receptor:

1. a lane arms while its strike band is clear;
2. a colored gem entering the lower hit band creates one note event;
3. simultaneous lane events are emitted as a chord in the same tick;
4. a vertical connected color run above the gem marks a sustain;
5. sustains extend the hold and optionally oscillate the left stick for whammy;
6. a lane cannot fire again until the strike band clears and its rearm interval expires.

The detector intentionally does not infer a note from arbitrary stage color. A coherent
receptor layout must own gameplay first.

## First live acceptance

Run:

```bat
bootstrap.cmd
ps2-autopilot-doctor --config config\guitar_hero.yaml --config-only
run-guitar-hero24x7.cmd
```

For the first supervised session keep `difficulty: easy` and verify these signals in the
console/overlay and retained JSONL:

- `gh_screen` moves save/title -> main_menu -> setlist -> difficulty -> presentation -> gameplay
- `gh_route_stage` advances with the expected Quick Play lifecycle
- no controller inputs are emitted while `gh_screen=presentation`
- idle highway frames show low `gh_hit_strengths`
- a visible green/red/yellow gem produces the matching L2/L1/R1 hold once
- a chord increments `gh_chords_attempted` and holds all required note buttons
- a sustain increments `gh_sustain_ticks` and does not repeatedly re-trigger its head
- song completion returns through results/high-score evidence to setlist and advances to the next song

The active profile remains **diagnostic** until a fresh boot, complete song, failure/retry,
post-song/high-score path, and multi-song unattended soak are all captured. In particular,
the exact GH1 results/high-score/initials screens should be added as retained templates
before calling post-song lifecycle acceptance complete. The profile has hooks for exact
templates under `profiles/guitar_hero/templates/`; add them from retained live frames rather
than lowering image thresholds globally.
