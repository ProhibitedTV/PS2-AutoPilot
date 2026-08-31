from ps2_autopilot.controllers.base import Controller
from ps2_autopilot.profiles.madden2005 import MaddenPhase, Possession, QueuedTap
from ps2_autopilot.profiles.madden2005_v24 import SpecialTeamsIntent, SpecialTeamsSide
from ps2_autopilot.profiles.madden2005_v25 import Madden2005V25Profile


class RecordingController(Controller):
    def __init__(self) -> None:
        self.taps: list[str] = []

    def tap(self, action: str, duration: float = 0.08) -> None:
        del duration
        self.taps.append(action)

    def hold(self, action: str) -> None:
        del action

    def release(self, action: str) -> None:
        del action

    def release_all(self) -> None:
        pass

    def set_left_stick(self, x: float, y: float) -> None:
        del x, y

    def set_right_stick(self, x: float, y: float) -> None:
        del x, y


def profile() -> Madden2005V25Profile:
    return Madden2005V25Profile({"ocr_enabled": False, "random_seed": 25})


def test_leaving_kicking_discards_pending_meter_taps_before_live_policy_can_run():
    p = profile()
    c = RecordingController()
    p.phase = MaddenPhase.KICKING
    p.phase_since = 1.0
    p.kick_armed = True
    p.queue.extend(
        [
            QueuedTap(2.10, "cross"),
            QueuedTap(2.45, "cross"),
        ]
    )

    p._transition_phase(MaddenPhase.LIVE, 2.0)

    assert p.phase == MaddenPhase.LIVE
    assert p.kick_armed is False
    assert list(p.queue) == []
    assert p.kick_queue_phase_exits == 1
    assert p.kick_queue_clear_events == 1
    assert p.kick_queue_discarded_taps == 2
    assert "kicking->live" in p.kick_queue_last_clear_reason

    # The policy loop calls _run_queue after observation/phase transition. There
    # must be no stale kick-meter Cross left to leak into live football control.
    assert p._run_queue(c, 3.0) is None
    assert c.taps == []


def test_remaining_in_kicking_preserves_meter_transaction():
    p = profile()
    p.phase = MaddenPhase.KICKING
    p.kick_armed = True
    p.queue.append(QueuedTap(10.0, "cross"))

    p._transition_phase(MaddenPhase.KICKING, 2.0)

    assert len(p.queue) == 1
    assert p.kick_armed is True
    assert p.kick_queue_phase_exits == 0
    assert p.kick_queue_clear_events == 0
    assert p.kick_queue_discarded_taps == 0


def test_return_handoff_keeps_v24_semantics_while_clearing_any_stale_meter_taps():
    p = profile()
    p.phase = MaddenPhase.KICKING
    p.phase_since = 1.0
    p.kick_armed = True
    p.queue.append(QueuedTap(2.2, "cross"))
    p.special_teams_intent = SpecialTeamsIntent.KICK_RETURN
    p.special_teams_side = SpecialTeamsSide.RETURNING

    p._transition_phase(MaddenPhase.LIVE, 2.0)

    assert p.possession == Possession.OFFENSE
    assert p.possession_confidence >= 0.95
    assert p.special_return_active is True
    assert list(p.queue) == []
    assert p.kick_queue_discarded_taps == 1


def test_v25_telemetry_exposes_queue_boundary_health():
    p = profile()
    p.phase = MaddenPhase.KICKING
    p.queue.append(QueuedTap(2.1, "cross"))
    p._transition_phase(MaddenPhase.POST_PLAY, 2.0)

    class Context:
        now = 2.0
        frame = None
        motion = 0.0
        template = None
        previous_frame = None

    state = p.telemetry(Context())
    assert state["madden_policy_version"] == "v25"
    assert state["kick_queue_phase_exits"] == 1
    assert state["kick_queue_clear_events"] == 1
    assert state["kick_queue_discarded_taps"] == 1
