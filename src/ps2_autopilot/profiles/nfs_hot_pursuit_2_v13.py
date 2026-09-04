from __future__ import annotations

import numpy as np

from .base import ProfileContext
from .nfs_hot_pursuit_2_v2 import NfsScreen
from .nfs_hot_pursuit_2_v12 import NfsHotPursuit2V12Profile


class NfsHotPursuit2V13Profile(NfsHotPursuit2V12Profile):
    """V13: reacquire the PS2 replay screen from its distinctive transport chrome.

    A live V12 run reached HP2's replay viewer without a calibrated replay template.
    The replay was therefore UNKNOWN and V6's generic bootstrap eventually cycled
    into Confirm probes. On the PS2 replay screen, Start is the Return Menu action;
    Confirm/X is a replay transport control, so generic bootstrap can keep the stream
    trapped in presentation instead of returning to the menu lifecycle.

    V13 adds a conservative image-only fallback for the replay viewer. It requires
    the combination visible in the live capture: a dark top timeline rail with
    neutral marker pixels plus a dark lower transport rail containing all four PS2
    glyph colour families. Once claimed, the existing V3/V9 replay lifecycle owns
    the screen, preserves the configured broadcast hold, taps Start once, and waits
    for positive visual progress. Strict template semantics still take precedence.
    """

    name = "nfs_hot_pursuit_2"

    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        self.replay_visual_enabled = bool(cfg.get("replay_visual_enabled", True))
        self.replay_visual_top_dark_min = max(
            0.40, min(0.98, float(cfg.get("replay_visual_top_dark_min", 0.68)))
        )
        self.replay_visual_controls_dark_min = max(
            0.40, min(0.98, float(cfg.get("replay_visual_controls_dark_min", 0.65)))
        )
        self.replay_visual_timeline_neutral_min = max(
            0.01, min(0.50, float(cfg.get("replay_visual_timeline_neutral_min", 0.04)))
        )
        self.replay_visual_glyph_fraction_min = max(
            0.0005, min(0.02, float(cfg.get("replay_visual_glyph_fraction_min", 0.0012)))
        )
        self.replay_visual_grace_seconds = max(
            0.0, min(4.0, float(cfg.get("replay_visual_grace_seconds", 1.25)))
        )

        self.replay_visual_active = False
        self.replay_visual_last_seen_at = -1e9
        self.replay_visual_claims = 0
        self.replay_visual_grace_fills = 0
        self.replay_visual_rejections = 0
        self.replay_visual_features: dict[str, float] = {}

    def _replay_chrome_features(self, frame: np.ndarray) -> dict[str, float]:
        if frame is None or frame.ndim != 3 or frame.shape[2] < 3:
            return {}
        h, w = frame.shape[:2]
        if h < 120 or w < 240:
            return {}

        # Proportional ROIs make the detector independent of the PCSX2 capture size.
        top = frame[0 : max(1, int(h * 0.18)), :, :3]
        timeline = frame[
            int(h * 0.03) : max(int(h * 0.03) + 1, int(h * 0.10)),
            int(w * 0.02) : max(int(w * 0.02) + 1, int(w * 0.98)),
            :3,
        ]
        controls = frame[
            int(h * 0.82) : max(int(h * 0.82) + 1, int(h * 0.97)),
            int(w * 0.05) : max(int(w * 0.05) + 1, int(w * 0.65)),
            :3,
        ]
        if top.size == 0 or timeline.size == 0 or controls.size == 0:
            return {}

        top_i = top.astype(np.int16, copy=False)
        timeline_i = timeline.astype(np.int16, copy=False)
        controls_i = controls.astype(np.int16, copy=False)

        top_dark = float(np.mean(np.max(top_i, axis=2) < 70))
        controls_dark = float(np.mean(np.max(controls_i, axis=2) < 70))

        timeline_max = np.max(timeline_i, axis=2)
        timeline_min = np.min(timeline_i, axis=2)
        timeline_neutral = float(
            np.mean(
                (timeline_max >= 80)
                & (timeline_max <= 245)
                & ((timeline_max - timeline_min) <= 40)
            )
        )

        c0 = controls_i[:, :, 0]
        c1 = controls_i[:, :, 1]
        c2 = controls_i[:, :, 2]
        # OpenCV frames are normally BGR, but the rule deliberately requires both
        # outer-channel dominant families, so it is also safe if an RGB frame arrives.
        outer0 = float(np.mean((c0 > 95) & (c0 > c1 + 28) & (c0 > c2 + 28)))
        green = float(np.mean((c1 > 95) & (c1 > c0 + 28) & (c1 > c2 + 28)))
        outer2 = float(np.mean((c2 > 95) & (c2 > c0 + 28) & (c2 > c1 + 28)))
        magenta = float(
            np.mean((c0 > 90) & (c2 > 80) & (c1 + 24 < np.minimum(c0, c2)))
        )

        return {
            "top_dark": top_dark,
            "controls_dark": controls_dark,
            "timeline_neutral": timeline_neutral,
            "glyph_outer0": outer0,
            "glyph_green": green,
            "glyph_outer2": outer2,
            "glyph_magenta": magenta,
        }

    def _replay_chrome_candidate(self, ctx: ProfileContext) -> bool:
        if not self.replay_visual_enabled:
            self.replay_visual_features = {}
            return False
        features = self._replay_chrome_features(ctx.frame)
        self.replay_visual_features = features
        if not features:
            return False

        glyph_min = self.replay_visual_glyph_fraction_min
        return (
            features["top_dark"] >= self.replay_visual_top_dark_min
            and features["controls_dark"] >= self.replay_visual_controls_dark_min
            and features["timeline_neutral"] >= self.replay_visual_timeline_neutral_min
            and features["glyph_outer0"] >= glyph_min
            and features["glyph_green"] >= glyph_min
            and features["glyph_outer2"] >= glyph_min
            and features["glyph_magenta"] >= glyph_min
        )

    def _claim_visual_replay(self, ctx: ProfileContext, *, grace: bool) -> NfsScreen:
        if not self.replay_visual_active:
            self.replay_visual_claims += 1
        self.replay_visual_active = True
        if not grace:
            self.replay_visual_last_seen_at = ctx.now
        else:
            self.replay_visual_grace_fills += 1
        self._reset_semantic_hint()
        self.raw_screen = NfsScreen.REPLAY
        self.screen_candidate = NfsScreen.REPLAY
        self.screen_candidate_frames = max(1, self.screen_candidate_frames + 1)
        self.screen = NfsScreen.REPLAY
        return self.screen

    def _recognized_screen(self, ctx: ProfileContext) -> NfsScreen:
        recognized = super()._recognized_screen(ctx)
        if recognized is not NfsScreen.UNKNOWN:
            self.replay_visual_active = recognized is NfsScreen.REPLAY
            if recognized is NfsScreen.REPLAY:
                self.replay_visual_last_seen_at = ctx.now
            return recognized

        # A strict, positively mapped template may be in V3's selected-row stability
        # probation. Do not let image fallback override that stronger evidence.
        template = ctx.template
        if (
            template is not None
            and float(template.score) >= self.template_threshold
            and self.raw_screen is not NfsScreen.UNKNOWN
        ):
            self.replay_visual_active = False
            return recognized

        if self._replay_chrome_candidate(ctx):
            return self._claim_visual_replay(ctx, grace=False)

        age = ctx.now - self.replay_visual_last_seen_at
        if self.replay_visual_active and 0.0 <= age <= self.replay_visual_grace_seconds:
            return self._claim_visual_replay(ctx, grace=True)

        if self.replay_visual_active:
            self.replay_visual_rejections += 1
        self.replay_visual_active = False
        return recognized

    def telemetry(self, ctx: ProfileContext) -> dict:
        state = super().telemetry(ctx)
        state.update(
            {
                "nfs_policy_version": 13,
                "nfs_replay_visual_enabled": self.replay_visual_enabled,
                "nfs_replay_visual_active": self.replay_visual_active,
                "nfs_replay_visual_claims": self.replay_visual_claims,
                "nfs_replay_visual_grace_fills": self.replay_visual_grace_fills,
                "nfs_replay_visual_rejections": self.replay_visual_rejections,
                "nfs_replay_visual_features": {
                    key: round(value, 4) for key, value in self.replay_visual_features.items()
                },
                "nfs_replay_visual_grace_seconds": round(self.replay_visual_grace_seconds, 2),
            }
        )
        return state
