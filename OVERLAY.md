# Stream overlay

The default OBS Browser Source is intentionally viewer-facing. It does **not** expose AutoPilot/OCR/recovery internals unless debug mode is explicitly requested.

## Default broadcast HUD

Add this as an OBS Browser Source:

```text
http://127.0.0.1:8765/
```

Recommended browser-source canvas: `1920x1080`.

The default HUD shows only stream-friendly information when AutoPilot can read it:

- Madden NFL 2005 / LIVE branding
- team abbreviations and score when the score bug OCR is confident
- quarter and game clock
- play clock when visible
- down/distance
- recent football event / presentation state
- completed-game and play counts

The page polls local state twice per second. The Python side also rate-limits `state.json` writes to the same broadcast-scale cadence so the overlay does not create a disk write on every gameplay tick.

## Engineering/debug mode

For development only:

```text
http://127.0.0.1:8765/?debug=1
```

This adds the internal phase/screen, role confidence, current action, spatial status, OCR/spatial processing cost, capture/policy timings, loop budget/overrun count, and raw OCR text.

Use the normal URL on stream; use `?debug=1` on a private OBS scene or browser while tuning.

## Twitch chat

An OBS Twitch account connection does not automatically expose the account's chat authorization to an arbitrary Browser Source. The overlay therefore does not embed credentials or assume a channel name.

A best-effort official Twitch chat embed can be enabled explicitly:

```text
http://127.0.0.1:8765/?chat_channel=YOUR_CHANNEL&chat_parent=127.0.0.1
```

This only supplies the channel/parent values to Twitch's official chat iframe; it does not store OAuth credentials in this repository. Twitch embed policy/browser restrictions can vary, so if the iframe is rejected in OBS, use a separate authenticated Twitch/StreamElements chat Browser Source or add a proper EventSub/WebSocket chat bridge later.

Debug and chat can be combined:

```text
http://127.0.0.1:8765/?debug=1&chat_channel=YOUR_CHANNEL&chat_parent=127.0.0.1
```

## Performance philosophy

The overlay is deliberately cheap:

- no framework
- no external fonts
- no animation loop
- no video/canvas rendering
- compact local JSON state
- two updates per second by default
- Twitch iframe is not loaded unless requested

PCSX2 and OBS remain the realtime priority workloads; the overlay should behave like a lightweight score bug, not another application competing for frame time.
