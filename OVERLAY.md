# Stream overlay

The default OBS Browser Source is intentionally viewer-facing. It does **not** expose AutoPilot/OCR/recovery internals unless debug mode is explicitly requested.

## Default Gamecast HUD

Add this as an OBS Browser Source:

```text
http://127.0.0.1:8765/
```

Recommended browser-source canvas: `1920x1080`.

The default HUD shows stream-friendly information when available:

- Madden NFL 2005 / game number / LIVE branding
- latched team abbreviations and score so the public score bug does not disappear between OCR views
- quarter and game clock
- play clock when visible
- down/distance
- a short event toast for touchdowns, turnovers, sacks, penalties, kicks, etc.
- pregame/replay/final presentation labels
- completed-game and play counts

The page polls local state twice per second. No animation/render loop runs in the background.

## Layout modes

Compact score bug:

```text
http://127.0.0.1:8765/?compact=1
```

Hide the score HUD (useful if the game feed already has enough scoreboard graphics):

```text
http://127.0.0.1:8765/?hud=0
```

Chat-only layout:

```text
http://127.0.0.1:8765/?chat_only=1&chat_channel=YOUR_CHANNEL&chat_parent=127.0.0.1
```

Parameters can be combined.

## Engineering/debug mode

For development only:

```text
http://127.0.0.1:8765/?debug=1
```

This adds internal phase/screen, role confidence, current action, spatial status, OCR worker state/result age/drop count, OCR/spatial processing cost, capture/policy timings, loop budget/overrun count, and raw OCR text.

Use the normal URL on stream; use `?debug=1` on a private OBS scene or browser while tuning.

## Twitch chat

An OBS Twitch account connection does not automatically expose the account's chat authorization to an arbitrary Browser Source. The overlay therefore does not embed credentials or assume a channel name.

A best-effort official Twitch chat embed can be enabled explicitly:

```text
http://127.0.0.1:8765/?chat_channel=YOUR_CHANNEL&chat_parent=127.0.0.1
```

This supplies only channel/parent values to Twitch's official chat iframe; it does not store OAuth credentials in this repository. If Twitch rejects the iframe in OBS, use a separate authenticated Twitch/StreamElements browser source or add a proper EventSub/WebSocket bridge later.

Debug and chat can be combined:

```text
http://127.0.0.1:8765/?debug=1&chat_channel=YOUR_CHANNEL&chat_parent=127.0.0.1
```

## Performance philosophy

The overlay stays deliberately cheap:

- no framework
- no external fonts
- no video/canvas rendering
- compact local JSON state
- two updates per second by default
- event animation is CSS-only and short-lived
- Twitch iframe is not loaded unless requested

PCSX2 and OBS remain the realtime priority workloads; the overlay should behave like a lightweight broadcast score bug, not another application competing for frame time.
