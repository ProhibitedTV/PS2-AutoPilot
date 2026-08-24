from __future__ import annotations

import math
import struct

from ps2_autopilot.jak1_semantic import GoalSymbol, Jak1GoalResolver


class FakeMemory:
    def __init__(self, size: int = 0x00800000) -> None:
        self.data = bytearray(size)

    def write16(self, address: int, value: int) -> None:
        struct.pack_into("<H", self.data, address, value)

    def write32(self, address: int, value: int) -> None:
        struct.pack_into("<I", self.data, address, value & 0xFFFFFFFF)

    def write_f32(self, address: int, value: float) -> None:
        struct.pack_into("<f", self.data, address, value)

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


def _resolver(mem: FakeMemory) -> Jak1GoalResolver:
    process_type = 0x00500004
    trsqv_type = 0x00500104
    resolver = Jak1GoalResolver(mem)
    resolver.symbols = {
        "process": GoalSymbol("process", 0, process_type),
        "trsqv": GoalSymbol("trsqv", 0, trsqv_type),
    }
    # Deliberately misleading metadata: the old PR #73 implementation searched
    # only around this 128-ish size and failed on the live SCUS-97124 target.
    mem.write16(process_type + 8, 132)
    mem.write16(process_type + 10, 132)
    return resolver


def _write_valid_trsqv(mem: FakeMemory, root: int, trsqv_type: int) -> None:
    mem.write32(root - 4, trsqv_type)
    mem.write_f32(root + 0, 4096.0)
    mem.write_f32(root + 4, 8192.0)
    mem.write_f32(root + 8, 12288.0)
    mem.write_f32(root + 48, 2048.0)
    mem.write_f32(root + 52, 0.0)
    mem.write_f32(root + 56, -4096.0)


def test_target_root_structural_scan_is_not_bound_to_process_size_metadata():
    mem = FakeMemory()
    resolver = _resolver(mem)
    target = 0x00400004
    root = 0x00410004
    trsqv_type = resolver.symbols["trsqv"].value

    # Put root far outside the old +/-16 byte process-size search window.
    root_offset = 0x2A0
    mem.write32(target + root_offset, root)
    _write_valid_trsqv(mem, root, trsqv_type)

    found, offset = resolver._find_target_root(target)
    assert found == root
    assert offset == root_offset


def test_target_root_structural_scan_rejects_typed_but_invalid_vector_candidate():
    mem = FakeMemory()
    resolver = _resolver(mem)
    target = 0x00400004
    trsqv_type = resolver.symbols["trsqv"].value

    bad_root = 0x00408004
    good_root = 0x00410004
    mem.write32(target + 0x100, bad_root)
    mem.write32(bad_root - 4, trsqv_type)
    mem.write_f32(bad_root, math.nan)

    mem.write32(target + 0x2A0, good_root)
    _write_valid_trsqv(mem, good_root, trsqv_type)

    found, offset = resolver._find_target_root(target)
    assert found == good_root
    assert offset == 0x2A0


def test_cached_root_offset_is_revalidated_for_recreated_target():
    mem = FakeMemory()
    resolver = _resolver(mem)
    trsqv_type = resolver.symbols["trsqv"].value

    first_target = 0x00400004
    first_root = 0x00410004
    mem.write32(first_target + 0x2A0, first_root)
    _write_valid_trsqv(mem, first_root, trsqv_type)
    assert resolver._find_target_root(first_target) == (first_root, 0x2A0)

    second_target = 0x00430004
    second_root = 0x00440004
    mem.write32(second_target + 0x2A0, second_root)
    _write_valid_trsqv(mem, second_root, trsqv_type)
    assert resolver._find_target_root(second_target) == (second_root, 0x2A0)
