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


def _write_valid_trsqv(mem: FakeMemory, root: int, actual_type: int) -> None:
    mem.write32(root - 4, actual_type)
    # GOAL basic header occupies +0..+15. trs vectors start at +16, with scale
    # at +48 and trsqv's first appended vector (transv) at +64.
    mem.write_f32(root + 16, 4096.0)
    mem.write_f32(root + 20, 8192.0)
    mem.write_f32(root + 24, 12288.0)
    mem.write_f32(root + 48, 1.0)
    mem.write_f32(root + 52, 1.0)
    mem.write_f32(root + 56, 1.0)
    mem.write_f32(root + 64, 2048.0)
    mem.write_f32(root + 68, 0.0)
    mem.write_f32(root + 72, -4096.0)


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


def test_target_root_accepts_runtime_subtype_of_trsqv():
    mem = FakeMemory()
    resolver = _resolver(mem)
    target = 0x00400004
    root = 0x00410004
    trsqv_type = resolver.symbols["trsqv"].value
    collide_shape_type = 0x00501004

    # Jak 1's process-drawable declaration explicitly allows root to be a more
    # specific type. Runtime Type.parent at +4 proves the subtype relationship.
    mem.write32(collide_shape_type + 4, trsqv_type)
    mem.write32(target + 0x70, root)
    _write_valid_trsqv(mem, root, collide_shape_type)

    found, offset = resolver._find_target_root(target)
    assert found == root
    assert offset == 0x70


def test_target_root_accepts_multi_level_trsqv_descendant():
    mem = FakeMemory()
    resolver = _resolver(mem)
    target = 0x00400004
    root = 0x00410004
    trsqv_type = resolver.symbols["trsqv"].value
    collide_shape_type = 0x00501004
    target_root_type = 0x00501104

    mem.write32(target_root_type + 4, collide_shape_type)
    mem.write32(collide_shape_type + 4, trsqv_type)
    mem.write32(target + 0x90, root)
    _write_valid_trsqv(mem, root, target_root_type)

    found, offset = resolver._find_target_root(target)
    assert found == root
    assert offset == 0x90


def test_target_root_structural_scan_rejects_typed_but_invalid_vector_candidate():
    mem = FakeMemory()
    resolver = _resolver(mem)
    target = 0x00400004
    trsqv_type = resolver.symbols["trsqv"].value

    bad_root = 0x00408004
    good_root = 0x00410004
    mem.write32(target + 0x100, bad_root)
    mem.write32(bad_root - 4, trsqv_type)
    mem.write_f32(bad_root + resolver.TRSQV_TRANS_OFFSET, math.nan)

    mem.write32(target + 0x2A0, good_root)
    _write_valid_trsqv(mem, good_root, trsqv_type)

    found, offset = resolver._find_target_root(target)
    assert found == good_root
    assert offset == 0x2A0


def test_unrelated_basic_pointer_is_not_accepted_as_root():
    mem = FakeMemory()
    resolver = _resolver(mem)
    target = 0x00400004
    root = 0x00410004
    unrelated = 0x00408004
    trsqv_type = resolver.symbols["trsqv"].value
    process_type = resolver.symbols["process"].value
    unrelated_type = 0x00502004

    mem.write32(unrelated_type + 4, process_type)
    mem.write32(target + 0x40, unrelated)
    _write_valid_trsqv(mem, unrelated, unrelated_type)

    mem.write32(target + 0xA0, root)
    _write_valid_trsqv(mem, root, trsqv_type)

    found, offset = resolver._find_target_root(target)
    assert found == root
    assert offset == 0xA0


def test_cached_root_offset_is_revalidated_for_recreated_target_subtype():
    mem = FakeMemory()
    resolver = _resolver(mem)
    trsqv_type = resolver.symbols["trsqv"].value
    derived_type = 0x00501004
    mem.write32(derived_type + 4, trsqv_type)

    first_target = 0x00400004
    first_root = 0x00410004
    mem.write32(first_target + 0x2A0, first_root)
    _write_valid_trsqv(mem, first_root, derived_type)
    assert resolver._find_target_root(first_target) == (first_root, 0x2A0)

    second_target = 0x00430004
    second_root = 0x00440004
    mem.write32(second_target + 0x2A0, second_root)
    _write_valid_trsqv(mem, second_root, derived_type)
    assert resolver._find_target_root(second_target) == (second_root, 0x2A0)
