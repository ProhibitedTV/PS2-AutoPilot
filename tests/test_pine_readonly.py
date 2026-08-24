from ps2_autopilot.pine import PineClient, PineCommand, PineSnapshot, PineTelemetryBridge


def test_pine_client_exposes_reads_but_no_writes():
    client = PineClient()
    assert callable(client.read8)
    assert callable(client.read16)
    assert callable(client.read32)
    assert callable(client.read64)
    assert not hasattr(client, "write8")
    assert not hasattr(client, "write32")
    assert not hasattr(client, "savestate")


def test_pine_command_numbers_match_pcsx2_protocol():
    assert PineCommand.READ8 == 0
    assert PineCommand.READ16 == 1
    assert PineCommand.READ32 == 2
    assert PineCommand.READ64 == 3
    assert PineCommand.VERSION == 8
    assert PineCommand.TITLE == 0x0B
    assert PineCommand.GAME_ID == 0x0C
    assert PineCommand.UUID == 0x0D
    assert PineCommand.GAME_VERSION == 0x0E
    assert PineCommand.STATUS == 0x0F


def test_title_only_identity_is_diagnostic_when_no_ram_fields_exist():
    bridge = PineTelemetryBridge(
        {
            "enabled": True,
            "expected_title_contains": "jak and daxter",
            "fields": {},
        }
    )
    bridge.snapshot = PineSnapshot(game_title="Jak and Daxter: The Precursor Legacy")
    assert bridge._identity_verified() is True


def test_ram_fields_require_exact_id_or_crc_gate_not_title_alone():
    bridge = PineTelemetryBridge(
        {
            "enabled": True,
            "expected_title_contains": "jak and daxter",
            "fields": {"jak_x": {"address": "0x1000", "type": "f32"}},
        }
    )
    bridge.snapshot = PineSnapshot(game_title="Jak and Daxter: The Precursor Legacy")
    assert bridge._identity_verified() is False


def test_exact_id_can_unlock_configured_ram_fields():
    bridge = PineTelemetryBridge(
        {
            "enabled": True,
            "expected_game_ids": ["SCUS-TEST"],
            "expected_title_contains": "jak and daxter",
            "fields": {"jak_x": {"address": "0x1000", "type": "f32"}},
        }
    )
    bridge.snapshot = PineSnapshot(
        game_title="Jak and Daxter: The Precursor Legacy",
        game_id="SCUS-TEST",
    )
    assert bridge._identity_verified() is True
