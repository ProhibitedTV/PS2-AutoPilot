import pytest

from ps2_autopilot.pine import PineClient
from ps2_autopilot.pine_lab import (
    PineLabCommand,
    PineLabSafetyError,
    PineSavestateClient,
)


class FakeLabClient(PineSavestateClient):
    def __init__(
        self,
        *,
        allow: bool = True,
        game_id: str = "SCUS-TEST",
        crc: str = "deadbeef",
        title: str = "Jak and Daxter: The Precursor Legacy",
        status: str = "running",
    ) -> None:
        super().__init__(allow_savestate_control=allow)
        self.fake_game_id = game_id
        self.fake_crc = crc
        self.fake_title = title
        self.fake_status = status
        self.requests: list[bytes] = []

    def version(self) -> str:
        return "PCSX2 test"

    def title(self) -> str:
        return self.fake_title

    def game_id(self) -> str:
        return self.fake_game_id

    def uuid(self) -> str:
        return self.fake_crc

    def game_version(self) -> str:
        return "1.00"

    def status(self) -> str:
        return self.fake_status

    def _request(self, payload: bytes) -> bytes:
        self.requests.append(payload)
        return b""


def gates() -> dict:
    return {
        "expected_game_ids": ["SCUS-TEST"],
        "expected_crcs": ["DEADBEEF"],
        "expected_title_contains": "jak and daxter",
    }


def test_production_pine_client_remains_read_only():
    client = PineClient()
    assert not hasattr(client, "request_save_state")
    assert not hasattr(client, "request_load_state")
    assert not hasattr(client, "write32")


def test_lab_command_numbers_match_current_pcsx2_pine_protocol():
    assert PineLabCommand.SAVE_STATE == 9
    assert PineLabCommand.LOAD_STATE == 0x0A


def test_lab_client_requires_explicit_opt_in_before_mutation():
    client = FakeLabClient(allow=False)
    with pytest.raises(PineLabSafetyError, match="disabled"):
        client.request_load_state(2, **gates())
    assert client.requests == []


def test_lab_client_requires_exact_id_or_crc_gate_not_title_only():
    client = FakeLabClient()
    with pytest.raises(PineLabSafetyError, match="exact expected game ID or CRC"):
        client.request_load_state(2, expected_title_contains="jak and daxter")
    assert client.requests == []


@pytest.mark.parametrize("slot", [-1, 256, 999])
def test_lab_client_rejects_slots_outside_one_byte_protocol(slot: int):
    client = FakeLabClient()
    with pytest.raises(PineLabSafetyError, match="0..255"):
        client.request_save_state(slot, **gates())
    assert client.requests == []


def test_lab_client_rejects_wrong_game_before_sending_mutation():
    client = FakeLabClient(game_id="SLUS-WRONG")
    with pytest.raises(PineLabSafetyError, match="game_id"):
        client.request_load_state(7, **gates())
    assert client.requests == []


def test_lab_client_rejects_non_running_emulator():
    client = FakeLabClient(status="paused")
    with pytest.raises(PineLabSafetyError, match="status"):
        client.request_load_state(7, **gates())
    assert client.requests == []


def test_save_state_wire_packet_is_opcode_plus_one_byte_slot():
    client = FakeLabClient()
    report = client.request_save_state(7, **gates())
    assert client.requests == [b"\x09\x07"]
    assert report["accepted"] is True
    assert report["completed"] is False
    assert report["command"] == "save_state"
    assert report["slot"] == 7
    assert report["target"]["game_id"] == "SCUS-TEST"


def test_load_state_wire_packet_is_opcode_plus_one_byte_slot():
    client = FakeLabClient()
    report = client.request_load_state(255, **gates())
    assert client.requests == [b"\x0a\xff"]
    assert report["accepted"] is True
    assert report["completed"] is False
    assert report["command"] == "load_state"
    assert report["slot"] == 255
