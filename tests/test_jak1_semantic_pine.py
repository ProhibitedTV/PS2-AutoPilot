from __future__ import annotations

import struct

from ps2_autopilot.jak1_semantic import Jak1GoalResolver, ORIGINAL_SYM_TO_STRING_OFFSET
from ps2_autopilot.pcsx2_pine_config import enable_pine_config, read_pine_config


class FakeMemory:
    def __init__(self, size: int = 0x00800000) -> None:
        self.data = bytearray(size)

    def write8(self, address: int, value: int) -> None:
        self.data[address] = value & 0xFF

    def write16(self, address: int, value: int) -> None:
        struct.pack_into("<H", self.data, address, value)

    def write32(self, address: int, value: int) -> None:
        struct.pack_into("<I", self.data, address, value & 0xFFFFFFFF)

    def write_f32(self, address: int, value: float) -> None:
        struct.pack_into("<f", self.data, address, value)

    def write_goal_string(self, pointer: int, value: str) -> None:
        raw = value.encode("ascii") + b"\x00"
        self.write32(pointer, len(value))
        self.data[pointer + 4 : pointer + 4 + len(raw)] = raw

    def read8(self, address: int) -> int:
        return self.data[address]

    def read16(self, address: int) -> int:
        return struct.unpack_from("<H", self.data, address)[0]

    def read32(self, address: int) -> int:
        return struct.unpack_from("<I", self.data, address)[0]

    def read_f32(self, address: int) -> float:
        return struct.unpack_from("<f", self.data, address)[0]

    def read32_many(self, addresses) -> list[int]:
        return [self.read32(address) for address in addresses]


def _retail_like_memory() -> FakeMemory:
    mem = FakeMemory()
    s7 = 0x00200004
    false_base = s7 - 4
    false_string = 0x00205004
    mem.write32(s7, s7)
    mem.write_goal_string(false_string, "#f")
    # OpenGOAL memory_dump_tool's #f probe reads base + 0xff38 + 4.
    mem.write32(false_base + ORIGINAL_SYM_TO_STRING_OFFSET + 4, false_string)

    process_type = 0x00500004
    trsqv_type = 0x00500104
    target_type = 0x00500204
    game_info_type = 0x00500304
    target_ptr = 0x00400004
    root_ptr = 0x00410004
    game_info_ptr = 0x00420004

    # process payload size 128 => raw allocated size 132 including the basic type tag.
    mem.write16(process_type + 8, 132)
    mem.write16(process_type + 10, 132)
    mem.write32(target_ptr - 4, target_type)
    mem.write32(root_ptr - 4, trsqv_type)
    mem.write32(game_info_ptr - 4, game_info_type)
    mem.write32(target_ptr + 128, root_ptr)

    # GOAL basic `trs` has a 16-byte basic header, followed by trans/rot/scale.
    # Keep scale at +48 as (1,1,1) to reproduce the live SCUS-97124 signature
    # that exposed the old bug: +48 was incorrectly decoded as transv.
    mem.write_f32(root_ptr + 16, 4096.0)
    mem.write_f32(root_ptr + 20, 8192.0)
    mem.write_f32(root_ptr + 24, 12288.0)
    mem.write_f32(root_ptr + 48, 1.0)
    mem.write_f32(root_ptr + 52, 1.0)
    mem.write_f32(root_ptr + 56, 1.0)
    mem.write_f32(root_ptr + 64, 2048.0)
    mem.write_f32(root_ptr + 68, 0.0)
    mem.write_f32(root_ptr + 72, -4096.0)

    mem.write_f32(game_info_ptr + 16, 5.0)
    mem.write_f32(game_info_ptr + 20, 12.0)
    mem.write_f32(game_info_ptr + 88, 2.0)
    mem.write_f32(game_info_ptr + 92, 1.0)

    symbol_values = {
        "*target*": target_ptr,
        "*game-info*": game_info_ptr,
        "target": target_type,
        "process": process_type,
        "trsqv": trsqv_type,
        "game-info": game_info_type,
    }
    table_start = s7 - 32768
    string_cursor = 0x00310004
    for index, (name, value) in enumerate(symbol_values.items()):
        sym = table_start + index * 8
        mem.write32(sym, value)
        mem.write32(sym + ORIGINAL_SYM_TO_STRING_OFFSET, string_cursor)
        mem.write_goal_string(string_cursor, name)
        string_cursor += 0x80
    return mem


def test_jak1_goal_symbols_self_resolve_without_absolute_addresses():
    resolver = Jak1GoalResolver(
        _retail_like_memory(),
        scan_start=0x001F0000,
        scan_end=0x00210000,
        scan_batch=4096,
    )
    state = resolver.snapshot()
    assert state["pine_schema_verified"] is True
    assert state["pine_semantic_schema"] == "jak1-goal-symbols-v1"
    assert state["power_cells"] == 1
    assert state["precursor_orbs"] == 12
    assert state["scout_flies"] == 2
    assert state["jak_x"] == 1.0
    assert state["jak_y"] == 2.0
    assert state["jak_z"] == 3.0
    assert state["jak_vx"] == 0.5
    assert state["jak_vy"] == 0.0
    assert state["jak_vz"] == -1.0
    assert state["pine_target_root_offset"] == 128


def test_symbol_map_exposes_named_runtime_objects():
    resolver = Jak1GoalResolver(
        _retail_like_memory(),
        scan_start=0x001F0000,
        scan_end=0x00210000,
        scan_batch=4096,
    )
    symbols = resolver.build_symbol_map()
    assert symbols["*target*"].value == 0x00400004
    assert symbols["*game-info*"].value == 0x00420004
    assert resolver.find_s7() == 0x00200004


def test_enable_pine_config_only_updates_emucore_and_backs_up(tmp_path):
    ini = tmp_path / "PCSX2.ini"
    ini.write_text(
        "[EmuCore]\nEnablePINE = false\nPINESlot = 28012\nSomeOtherSetting = 7\n\n"
        "[UI]\nEnablePINE = false\n",
        encoding="utf-8",
    )
    changed, backup = enable_pine_config(ini, port=28011)
    assert changed is True
    assert backup is not None and backup.exists()
    state = read_pine_config(ini)
    assert state.enabled is True
    assert state.port == 28011
    text = ini.read_text(encoding="utf-8")
    assert "SomeOtherSetting = 7" in text
    assert "[UI]\nEnablePINE = false" in text


def test_enable_pine_config_adds_missing_keys_to_emucore(tmp_path):
    ini = tmp_path / "PCSX2.ini"
    ini.write_text("[EmuCore]\nSomeOtherSetting = 7\n[UI]\nFoo = bar\n", encoding="utf-8")
    changed, _backup = enable_pine_config(ini, port=28011)
    assert changed is True
    state = read_pine_config(ini)
    assert state.enabled is True
    assert state.port == 28011
