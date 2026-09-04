from __future__ import annotations

import numpy as np

from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.profiles.base import ProfileContext
from ps2_autopilot.profiles.nfs_hot_pursuit_2_v14 import NfsHotPursuit2V14Profile
from ps2_autopilot.vision import TemplateMatch


class FakeController(Controller):
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def tap(self, action: str, duration: float = 0.08) -> None:
        self.events.append(("tap", action, duration))

    def hold(self, action: str) -> None:
        self.events.append(("hold", action))

    def release(self, action: str) -> None:
        self.events.append(("release", action))

    def release_all(self) -> None:
        self.events.append(("release_all",))

    def set_left_stick(self, x: float, y: float) -> None:
        self.events.append(("left_stick", x, y))

    def set_right_stick(self, x: float, y: float) -> None:
        self.events.append(("right_stick", x, y))


def _ctx(
    *,
    now: float,
    name: str | None = None,
    score: float = 0.0,
    motion: float = 0.0,
) -> ProfileContext:
    template = None if name is None else TemplateMatch(name, score)
    return ProfileContext(
        frame=np.zeros((360, 640, 3), dtype=np.uint8),
        motion=motion,
        template=template,
        now=now,
    )


def test_pending_semantic_confirm_interlocks_generic_bootstrap():
    profile = NfsHotPursuit2V14Profile(
        {
            "replay_visual_enabled": False,
            "semantic_hint_hold_seconds": 0.5,
            "semantic_hint_min_score": 0.24,
            "menu_action_seconds": 0.05,
            "menu_progress_timeout_seconds": 1.0,
            "bootstrap_stable_seconds": 0.35,
        }
    )
    controller = FakeController()

    # First weak frame starts the semantic hold; second earns World Racing and
    # emits the evidence-backed Confirm transaction.
    profile.tick(
        controller,
        _ctx(
            now=10.0,
            name="nfs_main_menu_world_racing_selected_02",
            score=0.30,
        ),
    )
    profile.tick(
        controller,
        _ctx(
            now=10.6,
            name="nfs_main_menu_world_racing_selected_03",
            score=0.30,
        ),
    )

    taps = [event for event in controller.events if event[0] == "tap"]
    assert [event[1] for event in taps] == ["confirm"]
    assert profile.pending_menu_action == "confirm"

    # The selected-row template disappears during transition. V3 keeps the pending
    # transaction alive; V14 must prevent V6 from injecting Start/Down/Confirm.
    controller.events.clear()
    action = profile.tick(controller, _ctx(now=10.8))

    assert "bootstrap interlocked" in action
    assert profile.pending_menu_action == "confirm"
    assert not any(event[0] == "tap" for event in controller.events)
    assert profile.bootstrap_actions == 0
    assert profile.menu_bootstrap_interlock_ticks == 1
    assert profile.menu_bootstrap_interlock_events == 1


def test_interlock_releases_after_ambiguous_transaction_timeout():
    profile = NfsHotPursuit2V14Profile(
        {
            "replay_visual_enabled": False,
            "semantic_hint_hold_seconds": 0.5,
            "menu_action_seconds": 0.05,
            "menu_progress_timeout_seconds": 1.0,
            "bootstrap_stable_seconds": 0.35,
        }
    )
    controller = FakeController()

    profile.tick(
        controller,
        _ctx(
            now=20.0,
            name="nfs_main_menu_world_racing_selected_02",
            score=0.30,
        ),
    )
    profile.tick(
        controller,
        _ctx(
            now=20.6,
            name="nfs_main_menu_world_racing_selected_03",
            score=0.30,
        ),
    )
    assert profile.pending_menu_action == "confirm"

    profile.tick(controller, _ctx(now=20.8))
    assert profile.menu_bootstrap_interlock_ticks >= 1

    # V3 clears an unresolved ambiguous transition after 2x the menu progress
    # timeout. Bootstrap may resume only after that owner has released the transaction.
    controller.events.clear()
    action = profile.tick(controller, _ctx(now=22.8))
    assert profile.pending_menu_action is None
    assert "bootstrap interlocked" not in action
    assert not profile._menu_bootstrap_interlock_active


def test_bootstrap_still_operates_when_no_menu_transaction_exists():
    profile = NfsHotPursuit2V14Profile(
        {
            "replay_visual_enabled": False,
            "bootstrap_stable_seconds": 0.35,
            "menu_action_seconds": 0.05,
        }
    )
    controller = FakeController()

    profile.tick(controller, _ctx(now=30.0))
    profile.tick(controller, _ctx(now=30.4))

    taps = [event for event in controller.events if event[0] == "tap"]
    assert [event[1] for event in taps] == ["start"]
    assert profile.bootstrap_actions == 1
    assert profile.menu_bootstrap_interlock_ticks == 0


def test_v14_telemetry_exposes_interlock_state():
    profile = NfsHotPursuit2V14Profile({"replay_visual_enabled": False})
    ctx = _ctx(now=40.0)
    profile.pending_menu_action = "confirm"
    profile.pending_menu_screen = profile._screen_from_template(
        "nfs_main_menu_world_racing_selected_02"
    )
    profile.pending_menu_since = 39.0
    controller = FakeController()

    profile._bootstrap_tick(controller, ctx)
    state = profile.telemetry(ctx)

    assert state["nfs_policy_version"] == 14
    assert state["nfs_menu_bootstrap_interlock_active"] is True
    assert state["nfs_menu_bootstrap_interlock_ticks"] == 1
    assert state["nfs_menu_bootstrap_interlock_events"] == 1
    assert state["nfs_menu_bootstrap_interlock_pending_action"] == "confirm"
    assert state["nfs_menu_bootstrap_interlock_pending_age"] == 1.0
