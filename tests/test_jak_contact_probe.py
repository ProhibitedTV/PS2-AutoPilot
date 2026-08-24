from __future__ import annotations

import struct

import pytest

from ps2_autopilot.jak1_semantic import GoalSymbol, Jak1GoalResolver
from ps2_autopilot.jak_contact_probe import (
    CSHAPE_MOVING_STATUS_OFFSET,
    CSHAPE_ONGROUND,
    CSHAPE_ONSURF,
    CSHAPE_ON_WATER,
    JakContactProbeError,
    read_contact_fields,
)


class FakeMemory:
    def __init__(self, size: int = 0x00800000) -> None:
        self.data = bytearray(size)

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


def _resolver(*, runtime_is_moving: bool = True) -> tuple[Jak1GoalResolver, FakeMemory, int]:
    mem = FakeMemory()
    resolver = Jak1GoalResolver(mem)
    target = 0x00400004
    root = 0x00410004
    trsqv_type = 0x00500104
    collide_shape_type = 0x00500204
    moving_type = 0x00500304
    runtime_type = 0x00500404

    resolver.symbols = {
        "*target*": GoalSymbol("*target*", 0, target),
        "trsqv": GoalSymbol("trsqv", 0, trsqv_type),
        "collide-shape-moving": GoalSymbol("collide-shape-moving", 0, moving_type),
    }

    if runtime_is_moving:
        mem.write32(runtime_type + 4, moving_type)
        mem.write32(moving_type + 4, collide_shape_type)
        mem.write32(collide_shape_type + 4, trsqv_type)
    else:
        mem.write32(runtime_type + 4, trsqv_type)
        mem.write32(moving_type + 4, collide_shape_type)
        mem.write32(collide_shape_type + 4, trsqv_type)

    mem.write32(target + 0x80, root)
    mem.write32(root - 4, runtime_type)
    mem.write_f32(root + resolver.TRSQV_TRANS_OFFSET, 4096.0)
    mem.write_f32(root + resolver.TRSQV_TRANS_OFFSET + 4, 8192.0)
    mem.write_f32(root + resolver.TRSQV_TRANS_OFFSET + 8, 12288.0)
    mem.write_f32(root + resolver.TRSQV_TRANSV_OFFSET, 2048.0)
    mem.write_f32(root + resolver.TRSQV_TRANSV_OFFSET + 4, 0.0)
    mem.write_f32(root + resolver.TRSQV_TRANSV_OFFSET + 8, -4096.0)
    return resolver, mem, root


def test_status_offset_is_the_source_derived_collide_shape_moving_layout() -> None:
    assert CSHAPE_MOVING_STATUS_OFFSET == 0x110


def test_reads_ground_surface_and_water_flags_from_verified_moving_root() -> None:
    resolver, mem, root = _resolver()
    status = CSHAPE_ONSURF | CSHAPE_ONGROUND | CSHAPE_ON_WATER
    mem.write32(root + CSHAPE_MOVING_STATUS_OFFSET, status)
    mem.write32(root + CSHAPE_MOVING_STATUS_OFFSET + 4, 0)

    fields = read_contact_fields(resolver)
    assert fields["pine_contact_verified"] is True
    assert fields["pine_contact_status_offset"] == 0x110
    assert fields["pine_contact_status"] == status
    assert fields["jak_on_surface"] is True
    assert fields["jak_on_ground"] is True
    assert fields["jak_grounded"] is True
    assert fields["jak_contact"] is True
    assert fields["jak_on_water"] is True


def test_airborne_status_is_explicit_false_not_missing() -> None:
    resolver, mem, root = _resolver()
    mem.write32(root + CSHAPE_MOVING_STATUS_OFFSET, 0)
    mem.write32(root + CSHAPE_MOVING_STATUS_OFFSET + 4, 0)

    fields = read_contact_fields(resolver)
    assert fields["jak_grounded"] is False
    assert fields["jak_contact"] is False
    assert fields["jak_on_water"] is False


def test_rejects_trsqv_root_that_is_not_a_collide_shape_moving_descendant() -> None:
    resolver, mem, root = _resolver(runtime_is_moving=False)
    mem.write32(root + CSHAPE_MOVING_STATUS_OFFSET, CSHAPE_ONGROUND)
    mem.write32(root + CSHAPE_MOVING_STATUS_OFFSET + 4, 0)

    with pytest.raises(JakContactProbeError, match="does not descend"):
        read_contact_fields(resolver)


def test_rejects_nonzero_status_high_word_as_layout_mismatch() -> None:
    resolver, mem, root = _resolver()
    mem.write32(root + CSHAPE_MOVING_STATUS_OFFSET, CSHAPE_ONGROUND)
    mem.write32(root + CSHAPE_MOVING_STATUS_OFFSET + 4, 1)

    with pytest.raises(JakContactProbeError, match="high word"):
        read_contact_fields(resolver)


def test_rejects_bits_outside_declared_cshape_moving_flag_range() -> None:
    resolver, mem, root = _resolver()
    mem.write32(root + CSHAPE_MOVING_STATUS_OFFSET, 1 << 31)
    mem.write32(root + CSHAPE_MOVING_STATUS_OFFSET + 4, 0)

    with pytest.raises(JakContactProbeError, match="low word"):
        read_contact_fields(resolver)
