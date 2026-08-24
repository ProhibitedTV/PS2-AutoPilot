from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import Any, Iterable

from .pine import PineClient, PineError


class PineLabSafetyError(PineError):
    """Raised when a state-mutating lab operation fails a safety gate."""


class PineLabCommand(IntEnum):
    """The only state-mutating PINE commands allowed by the lab client.

    Guest-memory write opcodes intentionally do not appear here. Production semantic
    telemetry remains on :class:`PineClient`, which is read-only by design.
    """

    SAVE_STATE = 9
    LOAD_STATE = 0x0A


@dataclass(frozen=True)
class PineTargetIdentity:
    emulator_version: str
    game_title: str
    game_id: str
    game_crc: str
    game_version: str
    emulator_status: str

    def as_dict(self) -> dict[str, str]:
        return dict(asdict(self))


def _normalized(values: Iterable[str], *, upper: bool) -> set[str]:
    output: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text:
            output.add(text.upper() if upper else text.lower())
    return output


class PineSavestateClient(PineClient):
    """Explicitly opt-in PCSX2 savestate control for deterministic lab workflows.

    This class is deliberately separate from the production :class:`PineClient`.
    It exposes only PINE's slot-based SAVE_STATE / LOAD_STATE commands and never
    exposes guest-memory writes. Mutating requests require:

    * construction with ``allow_savestate_control=True``;
    * a slot in PCSX2's one-byte range (0..255);
    * at least one exact game-ID or CRC allow-list gate;
    * a currently running emulator; and
    * all supplied identity gates to match.

    PCSX2 queues save/load work onto its CPU thread. A successful PINE response means
    the request was accepted, not that disk I/O or state restoration has completed.
    Callers must verify postconditions independently before scoring an episode.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 28011,
        timeout: float = 0.05,
        *,
        allow_savestate_control: bool = False,
    ) -> None:
        super().__init__(host=host, port=port, timeout=timeout)
        self.allow_savestate_control = bool(allow_savestate_control)

    @staticmethod
    def _slot(value: int) -> int:
        try:
            slot = int(value)
        except (TypeError, ValueError) as exc:
            raise PineLabSafetyError("PINE savestate slot must be an integer") from exc
        if slot < 0 or slot > 255:
            raise PineLabSafetyError("PINE savestate slot must be in the range 0..255")
        return slot

    def target_identity(self) -> PineTargetIdentity:
        return PineTargetIdentity(
            emulator_version=self.version(),
            game_title=self.title(),
            game_id=self.game_id(),
            game_crc=self.uuid(),
            game_version=self.game_version(),
            emulator_status=self.status(),
        )

    def assert_safe_target(
        self,
        *,
        expected_game_ids: Iterable[str] = (),
        expected_crcs: Iterable[str] = (),
        expected_title_contains: str = "",
        require_running: bool = True,
    ) -> PineTargetIdentity:
        ids = _normalized(expected_game_ids, upper=True)
        crcs = _normalized(expected_crcs, upper=False)
        expected_title = str(expected_title_contains or "").strip().lower()

        if not ids and not crcs:
            raise PineLabSafetyError(
                "savestate control requires at least one exact expected game ID or CRC"
            )

        identity = self.target_identity()
        failures: list[str] = []
        if ids and identity.game_id.strip().upper() not in ids:
            failures.append(f"game_id={identity.game_id!r}")
        if crcs and identity.game_crc.strip().lower() not in crcs:
            failures.append(f"game_crc={identity.game_crc!r}")
        if expected_title and expected_title not in identity.game_title.lower():
            failures.append(f"title={identity.game_title!r}")
        if require_running and identity.emulator_status != "running":
            failures.append(f"status={identity.emulator_status!r}")
        if failures:
            raise PineLabSafetyError(
                "PINE savestate target failed identity/status gates: " + ", ".join(failures)
            )
        return identity

    def _request_state_change(
        self,
        command: PineLabCommand,
        slot: int,
        *,
        expected_game_ids: Iterable[str] = (),
        expected_crcs: Iterable[str] = (),
        expected_title_contains: str = "",
    ) -> dict[str, Any]:
        if not self.allow_savestate_control:
            raise PineLabSafetyError(
                "savestate control is disabled; construct PineSavestateClient with "
                "allow_savestate_control=True only for an explicit lab workflow"
            )

        safe_slot = self._slot(slot)
        identity = self.assert_safe_target(
            expected_game_ids=expected_game_ids,
            expected_crcs=expected_crcs,
            expected_title_contains=expected_title_contains,
            require_running=True,
        )
        response = self._request(bytes((int(command), safe_slot)))
        if response:
            raise PineError(
                f"unexpected payload in PINE {command.name} acknowledgement: {response!r}"
            )
        return {
            "accepted": True,
            "completed": False,
            "command": command.name.lower(),
            "slot": safe_slot,
            "target": identity.as_dict(),
        }

    def request_save_state(
        self,
        slot: int,
        *,
        expected_game_ids: Iterable[str] = (),
        expected_crcs: Iterable[str] = (),
        expected_title_contains: str = "",
    ) -> dict[str, Any]:
        return self._request_state_change(
            PineLabCommand.SAVE_STATE,
            slot,
            expected_game_ids=expected_game_ids,
            expected_crcs=expected_crcs,
            expected_title_contains=expected_title_contains,
        )

    def request_load_state(
        self,
        slot: int,
        *,
        expected_game_ids: Iterable[str] = (),
        expected_crcs: Iterable[str] = (),
        expected_title_contains: str = "",
    ) -> dict[str, Any]:
        return self._request_state_change(
            PineLabCommand.LOAD_STATE,
            slot,
            expected_game_ids=expected_game_ids,
            expected_crcs=expected_crcs,
            expected_title_contains=expected_title_contains,
        )
