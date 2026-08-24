from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from typing import Iterable, Protocol


# These structural constants come from OpenGOAL's Jak 1 memory_dump_tool and
# common/goal_constants.h. They describe the original game's GOAL runtime format,
# not one retail build's absolute RAM addresses.
ORIGINAL_MAX_GOAL_SYMBOLS = 8192
ORIGINAL_SYM_TO_STRING_OFFSET = 0xFF38
SYMBOL_SCAN_START = 0x00100000
SYMBOL_SCAN_END = 0x02000000  # retail PS2 EE RAM is 32 MiB
SYMBOL_MAP_BYTES = 0xFF00
METER_LENGTH = 4096.0


class MemoryReader(Protocol):
    def read8(self, address: int) -> int: ...
    def read16(self, address: int) -> int: ...
    def read32(self, address: int) -> int: ...
    def read_f32(self, address: int) -> float: ...
    def read32_many(self, addresses: Iterable[int]) -> list[int]: ...


class Jak1SemanticError(RuntimeError):
    pass


@dataclass(frozen=True)
class GoalSymbol:
    name: str
    address: int
    value: int


@dataclass(frozen=True)
class Jak1RuntimeLayout:
    s7: int
    target_ptr: int | None
    game_info_ptr: int | None
    root_ptr: int | None
    root_offset: int | None


class Jak1GoalResolver:
    """Resolve useful Jak 1 state through the retail GOAL symbol table.

    This deliberately avoids CRC-specific absolute object addresses. The original
    game contains symbol metadata at runtime; OpenGOAL's memory dump tooling documents
    how to locate the #f/s7 anchor and walk that table. Once found, permanent symbols
    such as *target* and *game-info* provide the live object pointers.

    All operations are read-only. Every pointer and decoded value is sanity checked;
    failure returns an unavailable semantic snapshot rather than guessing.
    """

    REQUIRED_SYMBOLS = (
        "*target*",
        "*game-info*",
        "process",
        "trsqv",
        "game-info",
    )

    # game-info field offsets derived from the decompiled Jak 1 type declaration.
    # Fields before the byte arrays are naturally 4-byte aligned; both arrays are
    # exactly 32 bytes, leaving buzzer-total and fuel at 88 and 92 respectively.
    GAME_INFO_MONEY_OFFSET = 16
    GAME_INFO_MONEY_TOTAL_OFFSET = 20
    GAME_INFO_BUZZER_TOTAL_OFFSET = 88
    GAME_INFO_FUEL_OFFSET = 92

    # trsqv inherits trs: trans/rot/scale are 3 inline 16-byte vectors. transv is
    # therefore the next inline vector at +48. OpenGOAL documents 4096 game units/m.
    TRSQV_TRANS_OFFSET = 0
    TRSQV_TRANSV_OFFSET = 48

    def __init__(
        self,
        reader: MemoryReader,
        *,
        scan_start: int = SYMBOL_SCAN_START,
        scan_end: int = SYMBOL_SCAN_END,
        scan_batch: int = 40000,
    ) -> None:
        self.reader = reader
        self.scan_start = max(0x80000, int(scan_start))
        self.scan_end = min(0x20000000, int(scan_end))
        self.scan_batch = max(1024, min(40000, int(scan_batch)))
        self.s7: int | None = None
        self.symbols: dict[str, GoalSymbol] = {}
        self._root_offset: int | None = None
        self._last_target_ptr: int | None = None

    @staticmethod
    def _valid_ee_ptr(value: int, *, allow_zero: bool = False) -> bool:
        if allow_zero and value == 0:
            return True
        return 0x00080000 <= int(value) < 0x02000000

    def _goal_string(self, pointer: int, max_len: int = 64) -> str | None:
        if not self._valid_ee_ptr(pointer) or pointer + 4 >= self.scan_end:
            return None
        addresses = [pointer + 4 + i * 4 for i in range((max_len + 3) // 4)]
        try:
            words = self.reader.read32_many(addresses)
        except Exception:
            return None
        raw = b"".join(struct.pack("<I", int(word) & 0xFFFFFFFF) for word in words)
        raw = raw.split(b"\x00", 1)[0]
        if not raw or len(raw) >= max_len:
            return None
        try:
            text = raw.decode("ascii")
        except UnicodeDecodeError:
            return None
        if any(ord(ch) < 0x20 or ord(ch) > 0x7E for ch in text):
            return None
        return text

    def find_s7(self) -> int:
        if self.s7 is not None:
            return self.s7
        start = self.scan_start & ~0xF
        stride = 8
        span = self.scan_end - start
        total = max(0, span // stride)
        for offset in range(0, total, self.scan_batch):
            count = min(self.scan_batch, total - offset)
            bases = [start + (offset + i) * stride for i in range(count)]
            values = self.reader.read32_many(base + 4 for base in bases)
            for base, value in zip(bases, values):
                if int(value) != base + 4:
                    continue
                # OpenGOAL's retail Jak 1 memory dumper verifies #f through the
                # SymInfo string pointer at candidate + ORIGINAL... + 4.
                try:
                    string_ptr = self.reader.read32(
                        base + ORIGINAL_SYM_TO_STRING_OFFSET + 4
                    )
                except Exception:
                    continue
                if self._goal_string(string_ptr) == "#f":
                    self.s7 = base + 4
                    return self.s7
        raise Jak1SemanticError("Jak 1 GOAL #f/s7 symbol anchor was not found in EE RAM")

    def build_symbol_map(self) -> dict[str, GoalSymbol]:
        if self.symbols:
            return self.symbols
        s7 = self.find_s7()
        start = s7 - ((ORIGINAL_MAX_GOAL_SYMBOLS // 2) * 8)
        count = SYMBOL_MAP_BYTES // 8
        symbol_addresses = [start + i * 8 for i in range(count)]
        info_addresses = [addr + ORIGINAL_SYM_TO_STRING_OFFSET for addr in symbol_addresses]
        string_ptrs = self.reader.read32_many(info_addresses)
        values = self.reader.read32_many(symbol_addresses)

        # Fetch up to 64 bytes for every plausible symbol name in a few batched PINE
        # calls rather than issuing thousands of tiny round trips.
        valid: list[tuple[int, int, int]] = []
        name_word_addresses: list[int] = []
        for index, (sym_addr, ptr) in enumerate(zip(symbol_addresses, string_ptrs)):
            ptr = int(ptr)
            if not self._valid_ee_ptr(ptr):
                continue
            valid.append((index, sym_addr, ptr))
            name_word_addresses.extend(ptr + 4 + i * 4 for i in range(16))

        name_words = self.reader.read32_many(name_word_addresses)
        cursor = 0
        result: dict[str, GoalSymbol] = {}
        for index, sym_addr, _ptr in valid:
            words = name_words[cursor : cursor + 16]
            cursor += 16
            raw = b"".join(struct.pack("<I", int(word) & 0xFFFFFFFF) for word in words)
            raw = raw.split(b"\x00", 1)[0]
            if not raw or len(raw) >= 64:
                continue
            try:
                name = raw.decode("ascii")
            except UnicodeDecodeError:
                continue
            if any(ord(ch) < 0x20 or ord(ch) > 0x7E for ch in name):
                continue
            result[name] = GoalSymbol(name, sym_addr, int(values[index]))

        missing = [name for name in self.REQUIRED_SYMBOLS if name not in result]
        if missing:
            raise Jak1SemanticError(
                "GOAL symbol table resolved but required Jak symbols were missing: "
                + ", ".join(missing)
            )
        self.symbols = result
        return result

    def symbol(self, name: str) -> GoalSymbol:
        symbols = self.build_symbol_map()
        try:
            return symbols[name]
        except KeyError as exc:
            raise Jak1SemanticError(f"GOAL symbol {name!r} not found") from exc

    def _type_sizes(self, type_name: str) -> tuple[int, int]:
        type_ptr = self.symbol(type_name).value
        if not self._valid_ee_ptr(type_ptr):
            raise Jak1SemanticError(f"type {type_name!r} has invalid pointer 0x{type_ptr:08X}")
        allocated = int(self.reader.read16(type_ptr + 8))
        padded = int(self.reader.read16(type_ptr + 10))
        if not (4 <= allocated <= 8192 and 4 <= padded <= 8192):
            raise Jak1SemanticError(f"type {type_name!r} has implausible size {allocated}/{padded}")
        return allocated, padded

    def _typed_pointer(self, address: int, expected_type: int) -> int | None:
        if not self._valid_ee_ptr(address):
            return None
        try:
            value = int(self.reader.read32(address))
        except Exception:
            return None
        if not self._valid_ee_ptr(value) or value < 4:
            return None
        try:
            if int(self.reader.read32(value - 4)) == expected_type:
                return value
        except Exception:
            pass
        return None

    def _find_target_root(self, target_ptr: int) -> tuple[int, int]:
        if self._root_offset is not None and self._last_target_ptr == target_ptr:
            root_type = self.symbol("trsqv").value
            root = self._typed_pointer(target_ptr + self._root_offset, root_type)
            if root is not None:
                return root, self._root_offset
            # Inline basics are less common but supported.
            probe = target_ptr + self._root_offset
            if probe >= 4 and int(self.reader.read32(probe - 4)) == root_type:
                return probe, self._root_offset

        allocated, padded = self._type_sizes("process")
        root_type = self.symbol("trsqv").value
        centers = {allocated - 4, padded - 4, allocated, padded}
        candidates = sorted(
            {base + delta for base in centers for delta in range(-16, 17, 4) if base + delta >= 0}
        )
        for offset in candidates:
            root = self._typed_pointer(target_ptr + offset, root_type)
            if root is not None:
                self._root_offset = offset
                self._last_target_ptr = target_ptr
                return root, offset
            inline = target_ptr + offset
            if inline >= 4 and self._valid_ee_ptr(inline):
                try:
                    if int(self.reader.read32(inline - 4)) == root_type:
                        self._root_offset = offset
                        self._last_target_ptr = target_ptr
                        return inline, offset
                except Exception:
                    pass
        raise Jak1SemanticError("could not locate target.root trsqv from runtime type metadata")

    @staticmethod
    def _finite_vec(values: Iterable[float], *, limit: float = 1.0e9) -> tuple[float, float, float]:
        vals = tuple(float(v) for v in values)
        if len(vals) != 3 or not all(math.isfinite(v) and abs(v) <= limit for v in vals):
            raise Jak1SemanticError(f"invalid semantic vector {vals!r}")
        return vals[0], vals[1], vals[2]

    @staticmethod
    def _count(value: float, maximum: int, name: str) -> int:
        if not math.isfinite(value):
            raise Jak1SemanticError(f"{name} is not finite")
        rounded = int(round(value))
        if abs(value - rounded) > 0.15 or not 0 <= rounded <= maximum:
            raise Jak1SemanticError(f"{name}={value!r} failed sanity validation")
        return rounded

    def snapshot(self) -> dict[str, object]:
        symbols = self.build_symbol_map()
        target_ptr = int(symbols["*target*"].value)
        game_info_ptr = int(symbols["*game-info*"].value)
        target_type = self.symbol("target").value if "target" in symbols else None
        game_info_type = symbols["game-info"].value

        if not self._valid_ee_ptr(game_info_ptr) or game_info_ptr < 4:
            raise Jak1SemanticError("*game-info* is not a valid EE pointer")
        if int(self.reader.read32(game_info_ptr - 4)) != game_info_type:
            raise Jak1SemanticError("*game-info* failed GOAL type-tag validation")

        fuel = float(self.reader.read_f32(game_info_ptr + self.GAME_INFO_FUEL_OFFSET))
        money = float(self.reader.read_f32(game_info_ptr + self.GAME_INFO_MONEY_OFFSET))
        money_total = float(self.reader.read_f32(game_info_ptr + self.GAME_INFO_MONEY_TOTAL_OFFSET))
        buzzer_total = float(
            self.reader.read_f32(game_info_ptr + self.GAME_INFO_BUZZER_TOTAL_OFFSET)
        )
        power_cells = self._count(fuel, 101, "power_cells")
        precursor_orbs = self._count(money_total, 2000, "precursor_orbs")
        scout_flies = self._count(buzzer_total, 112, "scout_flies")

        data: dict[str, object] = {
            "pine_semantic_schema": "jak1-goal-symbols-v1",
            "pine_schema_verified": True,
            "pine_s7": f"0x{self.find_s7():08X}",
            "pine_symbol_count": len(symbols),
            "pine_game_info_ptr": f"0x{game_info_ptr:08X}",
            "power_cells": power_cells,
            "precursor_orbs": precursor_orbs,
            "scout_flies": scout_flies,
            "jak_power_cells": power_cells,
            "jak_precursor_orbs": precursor_orbs,
            "jak_scout_flies": scout_flies,
            "jak_orbs_spendable": self._count(money, 2000, "spendable_orbs"),
        }

        # During menus/loading *target* may legitimately be #f. Progress remains valid;
        # position is emitted only once a typed target object is present.
        if self._valid_ee_ptr(target_ptr) and target_ptr >= 4:
            if target_type is not None and int(self.reader.read32(target_ptr - 4)) != target_type:
                raise Jak1SemanticError("*target* failed GOAL type-tag validation")
            root_ptr, root_offset = self._find_target_root(target_ptr)
            raw_pos = self._finite_vec(
                self.reader.read_f32(root_ptr + self.TRSQV_TRANS_OFFSET + axis * 4)
                for axis in range(3)
            )
            raw_vel = self._finite_vec(
                self.reader.read_f32(root_ptr + self.TRSQV_TRANSV_OFFSET + axis * 4)
                for axis in range(3)
            )
            pos_m = tuple(value / METER_LENGTH for value in raw_pos)
            vel_m = tuple(value / METER_LENGTH for value in raw_vel)
            data.update(
                {
                    "pine_target_ptr": f"0x{target_ptr:08X}",
                    "pine_target_root_ptr": f"0x{root_ptr:08X}",
                    "pine_target_root_offset": root_offset,
                    "jak_x": pos_m[0],
                    "jak_y": pos_m[1],
                    "jak_z": pos_m[2],
                    "jak_vx": vel_m[0],
                    "jak_vy": vel_m[1],
                    "jak_vz": vel_m[2],
                    "jak_x_raw": raw_pos[0],
                    "jak_y_raw": raw_pos[1],
                    "jak_z_raw": raw_pos[2],
                }
            )
        return data
