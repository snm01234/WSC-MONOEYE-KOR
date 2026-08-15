"""Core helpers for SD Gundam G Generation: Mono-Eye Gundams (WSC) text/font ROM work."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Stock commercial image is 8 MiB. Expanded patches prepend 8 MiB of FF so that
# stock `mov al, bank|0x80` / OUT C3h keeps hitting the original banks.
ROM_SIZE = 0x800000
ROM_SIZE_16MB = 0x1000000
BANK_SIZE = 0x10000
ROM_SIZE_CODE_8MB = 0x08
ROM_SIZE_CODE_16MB = 0x09

# Data Crystal ROM map (logical stock banks; file offset += stock_base)
SEG_FONT = 0x40
SEG_DICT = 0x5F
SEG_TEXT = 0x60
SEG_PROG = 0x7A

DICT_DATA_START = 0x3662
DICT_DATA_END = 0x7BCB  # inclusive end of last phrase region (approx)
DICT_PTR_START = 0x7BCC
DICT_PTR_END = 0x99B9  # inclusive

DEFAULT_ROM_NAME = "SD Gundam G Generation Mono-Eye Gundams.wsc"

# Set by load_rom / set_stock_base. bank_offset() adds this automatically.
_STOCK_BASE = 0


def stock_base_for_size(size: int) -> int:
    if size == ROM_SIZE:
        return 0
    if size == ROM_SIZE_16MB:
        return ROM_SIZE
    raise ValueError(
        f"Unexpected ROM size {size:#x}, expected {ROM_SIZE:#x} or {ROM_SIZE_16MB:#x}"
    )


def set_stock_base(base: int) -> None:
    global _STOCK_BASE
    if base not in (0, ROM_SIZE):
        raise ValueError(f"stock base must be 0 or {ROM_SIZE:#x}, got {base:#x}")
    _STOCK_BASE = base


def get_stock_base() -> int:
    return _STOCK_BASE


def stock_base(rom: bytes | bytearray) -> int:
    return stock_base_for_size(len(rom))


def is_expanded_rom(rom: bytes | bytearray) -> bool:
    return len(rom) == ROM_SIZE_16MB


def logical_bank_offset(segment: int, offset: int = 0) -> int:
    """Stock-relative offset (bank 0x40 → 0x400000), ignoring 16MB prepend."""
    return (segment & 0xFF) * BANK_SIZE + (offset & 0xFFFF)


def bank_offset(segment: int, offset: int = 0) -> int:
    """File offset for a stock logical bank (respects 16MB prepend via load_rom)."""
    return _STOCK_BASE + logical_bank_offset(segment, offset)


def expansion_bank_offset(segment: int, offset: int = 0) -> int:
    """File offset in the prepended 8 MiB (banks 0x00–0x7F). 16MB ROM only."""
    if _STOCK_BASE == 0:
        raise RuntimeError("expansion_bank_offset requires a loaded 16MB ROM")
    seg = segment & 0xFF
    if seg > 0x7F:
        raise ValueError(f"expansion bank must be 0x00–0x7F, got {seg:#x}")
    return seg * BANK_SIZE + (offset & 0xFFFF)


def bank_al_stock(logical_bank: int) -> int:
    """AL value for OUT C3h targeting a stock bank (high bit set)."""
    return (logical_bank & 0x7F) | 0x80


def bank_al_expansion(logical_bank: int) -> int:
    """AL value for OUT C3h targeting a prepended expansion bank (high bit clear)."""
    return logical_bank & 0x7F


def le16(data: bytes | bytearray, off: int) -> int:
    return data[off] | (data[off + 1] << 8)


def write_le16(buf: bytearray, off: int, value: int) -> None:
    buf[off] = value & 0xFF
    buf[off + 1] = (value >> 8) & 0xFF


def find_rom(root: Path | None = None) -> Path:
    root = root or Path(__file__).resolve().parents[1]
    candidate = root / DEFAULT_ROM_NAME
    if candidate.exists():
        return candidate
    matches = list(root.glob("*.wsc"))
    if not matches:
        raise FileNotFoundError(f"No .wsc ROM found under {root}")
    return matches[0]


def load_rom(path: Path | None = None) -> bytearray:
    rom_path = path or find_rom()
    data = bytearray(rom_path.read_bytes())
    set_stock_base(stock_base_for_size(len(data)))
    return data


def slice_bank(rom: bytes | bytearray, segment: int) -> bytes:
    start = stock_base(rom) + logical_bank_offset(segment)
    return bytes(rom[start : start + BANK_SIZE])


def slice_expansion_bank(rom: bytes | bytearray, segment: int) -> bytes:
    if not is_expanded_rom(rom):
        raise ValueError("slice_expansion_bank requires a 16MB ROM")
    seg = segment & 0xFF
    if seg > 0x7F:
        raise ValueError(f"expansion bank must be 0x00–0x7F, got {seg:#x}")
    start = seg * BANK_SIZE
    return bytes(rom[start : start + BANK_SIZE])

@dataclass
class Tbl:
    """Game custom encoding table (not Shift-JIS)."""

    code_to_char: Dict[int, str]
    char_to_code: Dict[str, int]

    @classmethod
    def load(cls, path: Path) -> "Tbl":
        code_to_char: Dict[int, str] = {}
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip("\n")
            if not line or line.startswith("#") or "=" not in line:
                continue
            left, right = line.split("=", 1)
            code = int(left.strip(), 16)
            # Keep empty mappings as empty string
            code_to_char[code] = right
        char_to_code: Dict[str, int] = {}
        for code, ch in code_to_char.items():
            if ch and ch not in char_to_code:
                char_to_code[ch] = code
        return cls(code_to_char, char_to_code)

    def decode_char(self, code: int) -> str:
        if code in self.code_to_char:
            return self.code_to_char[code]
        if code <= 0xFF:
            return f"<{code:02X}>"
        return f"<{code:04X}>"

    def encode_char(self, ch: str) -> bytes:
        if ch not in self.char_to_code:
            raise KeyError(f"Character not in TBL: {ch!r}")
        code = self.char_to_code[ch]
        if code <= 0xDF:
            return bytes([code])
        # Two-byte E0..E7 form stored as 0xE0nn in TBL keys
        return bytes([(code >> 8) & 0xFF, code & 0xFF])


def is_dict_token(b: int) -> bool:
    # F0–FE stock; FF used by extended dictionary hook (index >= 0xF00).
    return 0xF0 <= b <= 0xFF


# Ext3 portal (runtime hook): E5 18 xx yy → index 0x1000+((xx<<8)|yy).
# EF cannot be used — Hangul glyph lead. Magic E518 is unused in text walks.
EXT3_MAGIC0 = 0xE5
EXT3_MAGIC1 = 0x18
EXT3_INDEX_BASE = 0x1000
# Ceiling for up to 16×4K banks (0x11..0x20). Runtime/meta may use fewer.
EXT3_INDEX_END = 0x10FFF
EXT3_SLOTS_PER_BANK = 0x1000
EXT3_SEG0 = 0x11

# Compact portal used only by the alternative short-record candidate.
COMPACT3_MAGIC0 = 0xE5
COMPACT3_MAGIC1 = 0x19
COMPACT3_INDEX_BASE = 0xC000
COMPACT3_INDEX_END = 0xC0FF
COMPACT3_SEG = 0x1C


def is_ext3_magic(b0: int, b1: int) -> bool:
    return b0 == EXT3_MAGIC0 and b1 == EXT3_MAGIC1


def is_compact3_magic(b0: int, b1: int) -> bool:
    return b0 == COMPACT3_MAGIC0 and b1 == COMPACT3_MAGIC1


def is_kanji_lead(b: int) -> bool:
    # Game length walker treats all leads >= 0xE0 as 2-byte (except the stream
    # still reserves 0xF0–0xFE for dictionary tokens). Hangul overflow may use
    # 0xE8–0xEF as additional glyph pages.
    return 0xE0 <= b <= 0xEF


# Dialogue / script records are short. Sheet abs that walk into binary until a
# distant NUL can look like multi-KB "strings"; padding those with 00 bricks the ROM.
MAX_SAFE_RECORD_LEN = 256


def read_encoded_z(
    data: bytes | bytearray, offset: int, max_len: int = 0x10000
) -> Tuple[bytes, int]:
    """
    Read a zero-terminated encoded string without mistaking the trail byte of
    E0-FF two-byte units (kanji / dict / ext-dict), E518 ext3 quads, or
    E519 compact triples for NUL.

    Returns (payload_without_terminator, terminator_offset).
    """
    out = bytearray()
    cursor = offset
    end = min(len(data), offset + max_len)
    while cursor < end:
        lead = data[cursor]
        if lead == 0:
            return bytes(out), cursor
        out.append(lead)
        cursor += 1
        if lead >= 0xE0 and cursor < end:
            trail = data[cursor]
            out.append(trail)
            cursor += 1
            # Ext3: E5 18 xx yy (4 bytes total)
            if is_ext3_magic(lead, trail) and cursor + 1 < end:
                out.append(data[cursor])
                out.append(data[cursor + 1])
                cursor += 2
            # Compact short-record portal: E5 19 bb (3 bytes total)
            elif is_compact3_magic(lead, trail) and cursor < end:
                out.append(data[cursor])
                cursor += 1
    return bytes(out), cursor


def read_encoded_z_safe(
    data: bytes | bytearray, offset: int, max_len: int = MAX_SAFE_RECORD_LEN
) -> Tuple[bytes, int] | None:
    """Like read_encoded_z, but returns None if no NUL within max_len."""
    payload, term = read_encoded_z(data, offset, max_len)
    if term >= len(data) or data[term] != 0:
        return None
    if len(payload) > max_len:
        return None
    return payload, term


def dict_index_from_token(lead: int, trail: int) -> int:
    return ((lead - 0xF0) << 8) | trail


def dict_index_from_ext3_token(b0: int, b1: int, b2: int, b3: int) -> int:
    if not is_ext3_magic(b0, b1):
        raise ValueError(f"not ext3 magic: {b0:02X} {b1:02X}")
    return EXT3_INDEX_BASE + ((b2 << 8) | b3)


def dict_index_from_compact3_token(b0: int, b1: int, b2: int) -> int:
    if not is_compact3_magic(b0, b1):
        raise ValueError(f"not compact3 magic: {b0:02X} {b1:02X}")
    return COMPACT3_INDEX_BASE + b2


def token_from_dict_index(index: int) -> bytes:
    # Stock F0–FE page: 0x000–0xEFF. Extended FF page: 0xF00–0xFFF.
    # Ext3 portal: 0x1000–0x1FFF as E5 18 xx yy.
    if 0 <= index <= 0xEFF:
        return bytes([0xF0 + (index >> 8), index & 0xFF])
    if 0xF00 <= index <= 0xFFF:
        return bytes([0xFF, index & 0xFF])
    if EXT3_INDEX_BASE <= index <= EXT3_INDEX_END:
        slot = index - EXT3_INDEX_BASE
        if (slot & 0xFF) == 0:
            raise ValueError(f"ext3 index trail would be NUL: {index:#x}")
        return bytes(
            [EXT3_MAGIC0, EXT3_MAGIC1, (slot >> 8) & 0xFF, slot & 0xFF]
        )
    raise ValueError(f"dict index out of range: {index}")


def token_from_compact3_index(index: int) -> bytes:
    """Return the 3-byte compact portal for one reserved ext3 index."""
    if not COMPACT3_INDEX_BASE < index <= COMPACT3_INDEX_END:
        raise ValueError(f"compact3 index out of range: {index:#x}")
    return bytes([COMPACT3_MAGIC0, COMPACT3_MAGIC1, index - COMPACT3_INDEX_BASE])


def dict_token_safe_in_zstring(index: int) -> bool:
    """Return whether a generated dictionary token has no embedded NUL byte.

    Stock/FF-page tokens only need a non-zero trail byte.  Ext3 is four bytes
    (``E5 18 xx yy``), so *both* payload bytes must be non-zero.  The original
    ext3 guard checked only ``yy``; live ``E5 18 00 yy`` records later proved
    unsafe because an outer event/string boundary consumer can stop on ``xx``
    and expose ``yy`` as a one-byte glyph.
    """
    if EXT3_INDEX_BASE <= index <= EXT3_INDEX_END:
        slot = index - EXT3_INDEX_BASE
        return ((slot >> 8) & 0xFF) != 0 and (slot & 0xFF) != 0
    return (index & 0xFF) != 0


class Dictionary:
    """Segment 5F phrase dictionary with LE16 pointer table (+ optional ext/ext3)."""

    def __init__(
        self,
        rom: bytes | bytearray,
        count: int | None = None,
        *,
        ext_ptr_off: int | None = None,
        ext_seg: int = 0x5E,
        stock_count: int | None = None,
        ext_in_expansion: bool = False,
        ext3_ptr_off: int | None = None,
        ext3_seg: int = EXT3_SEG0,
        ext3_count: int = 0,
        ext3_banks: int | None = None,
        ext3_alias_page_count: int = 0,
        ext3_alias_local_start: int = 0x0600,
        ext3_alias_seg: int = 0x21,
    ):
        self.rom = rom
        base0 = stock_base(rom)
        self.base = base0 + logical_bank_offset(SEG_DICT)
        self.ptr_file = self.base + DICT_PTR_START
        default_count = (DICT_PTR_END - DICT_PTR_START + 1) // 2
        self.stock_count = default_count if stock_count is None else stock_count
        self.ext_ptr_off = ext_ptr_off
        self.ext_seg = ext_seg
        self.ext_in_expansion = ext_in_expansion
        self.ext3_ptr_off = 0 if ext3_ptr_off is None else ext3_ptr_off
        self.ext3_seg = ext3_seg
        self.ext3_count = ext3_count
        self.ext3_alias_page_count = ext3_alias_page_count
        self.ext3_alias_local_start = ext3_alias_local_start
        self.ext3_alias_seg = ext3_alias_seg
        # Prefer explicit bank count; else derive from flat count.
        if ext3_banks is not None:
            self.ext3_banks = ext3_banks
        elif ext3_count > 0:
            self.ext3_banks = max(
                1, (ext3_count + EXT3_SLOTS_PER_BANK - 1) // EXT3_SLOTS_PER_BANK
            )
        else:
            self.ext3_banks = 0
        self.ext3_base: int | None = None
        if ext_ptr_off is not None:
            if ext_in_expansion:
                if not is_expanded_rom(rom):
                    raise ValueError("ext_in_expansion requires a 16MB ROM")
                if (ext_seg & 0xFF) > 0x7F:
                    raise ValueError(
                        f"expansion ext_seg must be 0x00–0x7F, got {ext_seg:#x}"
                    )
                self.ext_base = (ext_seg & 0x7F) * BANK_SIZE
            else:
                self.ext_base = base0 + logical_bank_offset(ext_seg)
        else:
            self.ext_base = None
        if self.ext3_banks > 0:
            if not is_expanded_rom(rom):
                raise ValueError("ext3 requires a 16MB ROM")
            if (ext3_seg & 0xFF) > 0x7F:
                raise ValueError(f"ext3_seg must be 0x00–0x7F, got {ext3_seg:#x}")
            self.ext3_base = (ext3_seg & 0x7F) * BANK_SIZE
            if ext3_count <= 0:
                self.ext3_count = self.ext3_banks * EXT3_SLOTS_PER_BANK
        if self.ext3_alias_page_count:
            if not is_expanded_rom(rom):
                raise ValueError("ext3 aliases require a 16MB ROM")
            if not 0 < self.ext3_alias_page_count <= 0x10:
                raise ValueError(
                    f"ext3 alias page count out of range: {self.ext3_alias_page_count}"
                )
            if not 0 <= self.ext3_alias_local_start < EXT3_SLOTS_PER_BANK:
                raise ValueError(
                    f"ext3 alias local start out of range: {self.ext3_alias_local_start:#x}"
                )
            if (self.ext3_alias_seg & 0xFF) > 0x7F:
                raise ValueError(
                    f"ext3_alias_seg must be 0x00–0x7F, got {self.ext3_alias_seg:#x}"
                )
        self.count = default_count if count is None else count
        self.ptrs: List[int] = [
            le16(rom, self.ptr_file + i * 2) for i in range(self.stock_count)
        ]
        if self.ext_ptr_off is not None and self.count > self.stock_count:
            for i in range(self.count - self.stock_count):
                self.ptrs.append(
                    le16(rom, self.ext_base + self.ext_ptr_off + i * 2)
                )

    def _ext3_bank_local(self, index: int) -> tuple[int, int]:
        off = index - EXT3_INDEX_BASE
        page, local = off >> 12, off & 0xFFF
        if (
            page < self.ext3_alias_page_count
            and local >= self.ext3_alias_local_start
        ):
            return (
                (self.ext3_alias_seg & 0xFF) + page,
                local - self.ext3_alias_local_start,
            )
        return (self.ext3_seg & 0xFF) + page, local

    def _ext3_is_alias(self, index: int) -> bool:
        off = index - EXT3_INDEX_BASE
        page, local = off >> 12, off & 0xFFF
        return (
            page < self.ext3_alias_page_count
            and local >= self.ext3_alias_local_start
        )

    def entry_offset(self, index: int) -> int:
        if index >= EXT3_INDEX_BASE:
            seg, local = self._ext3_bank_local(index)
            base = (seg & 0x7F) * BANK_SIZE
            return le16(self.rom, base + self.ext3_ptr_off + local * 2)
        return self.ptrs[index]

    def entry_abs(self, index: int) -> int:
        if index >= EXT3_INDEX_BASE:
            if self.ext3_banks <= 0:
                raise IndexError(f"ext3 index without table: {index}")
            seg, local = self._ext3_bank_local(index)
            if not self._ext3_is_alias(index):
                bank_i = seg - (self.ext3_seg & 0xFF)
                if not 0 <= bank_i < self.ext3_banks:
                    raise IndexError(f"ext3 index out of range: {index}")
            base = (seg & 0x7F) * BANK_SIZE
            ptr = le16(self.rom, base + self.ext3_ptr_off + local * 2)
            return base + ptr
        if index < self.stock_count:
            return self.base + self.entry_offset(index)
        if self.ext_base is None:
            raise IndexError(f"extended dict index without ext table: {index}")
        return self.ext_base + self.entry_offset(index)

    def raw_entry(self, index: int, max_len: int = 256) -> bytes:
        abs_off = self.entry_abs(index)
        payload, _ = read_encoded_z(self.rom, abs_off, max_len)
        return payload

    def _expand_index(
        self,
        idx: int,
        tbl: Optional[Tbl],
        *,
        depth: int,
        max_depth: int,
        as_codes: bool,
    ) -> str:
        in_ext3 = EXT3_INDEX_BASE <= idx < EXT3_INDEX_BASE + self.ext3_count
        if in_ext3:
            if self.ext3_banks <= 0:
                return f"<BADDICT:{idx:04X}>"
        elif idx >= self.count:
            return f"<BADDICT:{idx:04X}>"
        return self.expand(
            self.raw_entry(idx),
            tbl,
            depth=depth + 1,
            max_depth=max_depth,
            as_codes=as_codes,
        )

    def expand(
        self,
        data: bytes,
        tbl: Optional[Tbl] = None,
        *,
        depth: int = 0,
        max_depth: int = 12,
        as_codes: bool = False,
    ) -> str:
        """Expand dictionary tokens; optionally map through TBL."""
        if depth > max_depth:
            return "…"
        i = 0
        parts: List[str] = []
        while i < len(data):
            b = data[i]
            if b == 0:
                break
            if is_dict_token(b):
                if i + 1 >= len(data):
                    parts.append(f"<TRUNC:{b:02X}>")
                    break
                idx = dict_index_from_token(b, data[i + 1])
                parts.append(
                    self._expand_index(
                        idx, tbl, depth=depth, max_depth=max_depth, as_codes=as_codes
                    )
                )
                i += 2
                continue
            if is_kanji_lead(b):
                if i + 1 >= len(data):
                    parts.append(f"<TRUNC:{b:02X}>")
                    break
                if is_compact3_magic(b, data[i + 1]):
                    if i + 2 >= len(data):
                        parts.append(f"<TRUNC:{b:02X}>")
                        break
                    idx = dict_index_from_compact3_token(
                        b, data[i + 1], data[i + 2]
                    )
                    parts.append(
                        self._expand_index(
                            idx,
                            tbl,
                            depth=depth,
                            max_depth=max_depth,
                            as_codes=as_codes,
                        )
                    )
                    i += 3
                    continue
                if is_ext3_magic(b, data[i + 1]):
                    if i + 3 >= len(data):
                        parts.append(f"<TRUNC:{b:02X}>")
                        break
                    idx = dict_index_from_ext3_token(
                        b, data[i + 1], data[i + 2], data[i + 3]
                    )
                    parts.append(
                        self._expand_index(
                            idx,
                            tbl,
                            depth=depth,
                            max_depth=max_depth,
                            as_codes=as_codes,
                        )
                    )
                    i += 4
                    continue
                code = (b << 8) | data[i + 1]
                if as_codes:
                    parts.append(f"[{code:04X}]")
                elif tbl:
                    parts.append(tbl.decode_char(code))
                else:
                    parts.append(f"[{code:04X}]")
                i += 2
                continue
            if as_codes:
                parts.append(f"[{b:02X}]")
            elif tbl:
                parts.append(tbl.decode_char(b))
            else:
                parts.append(f"[{b:02X}]")
            i += 1
        return "".join(parts)

    def expand_index(self, index: int, tbl: Optional[Tbl] = None) -> str:
        return self.expand(self.raw_entry(index), tbl)

    def all_raw_entries(self) -> List[bytes]:
        return [self.raw_entry(i) for i in range(self.count)]


# Script stream helpers -----------------------------------------------------

# Bytes that appear as dialogue charset (printable) in script payloads.
# Control-looking values that ALSO exist in TBL as kana (08=は, 17=が, 18=こ)
# are ambiguous; structural analysis uses length/context heuristics.


@dataclass
class ScriptToken:
    kind: str  # 'char' | 'dict' | 'ctrl' | 'end'
    raw: bytes
    text: str = ""
    value: int = 0


def tokenize_script_payload(
    data: bytes,
    tbl: Optional[Tbl] = None,
    dictionary: Optional[Dictionary] = None,
) -> List[ScriptToken]:
    """Tokenize a null-terminated script payload (no leading structural header)."""
    tokens: List[ScriptToken] = []
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0:
            tokens.append(ScriptToken("end", b"\x00", "", 0))
            break
        if is_dict_token(b):
            if i + 1 >= len(data):
                tokens.append(ScriptToken("ctrl", bytes([b]), f"<{b:02X}>", b))
                break
            raw = data[i : i + 2]
            idx = dict_index_from_token(b, data[i + 1])
            text = ""
            if dictionary is not None:
                text = dictionary.expand_index(idx, tbl)
            tokens.append(ScriptToken("dict", raw, text, idx))
            i += 2
            continue
        if is_kanji_lead(b):
            if i + 1 >= len(data):
                tokens.append(ScriptToken("ctrl", bytes([b]), f"<{b:02X}>", b))
                break
            if is_compact3_magic(b, data[i + 1]):
                if i + 2 >= len(data):
                    tokens.append(
                        ScriptToken("ctrl", bytes([b, data[i + 1]]), f"<{b:02X}>", b)
                    )
                    break
                raw = data[i : i + 3]
                idx = dict_index_from_compact3_token(b, data[i + 1], data[i + 2])
                text = dictionary.expand_index(idx, tbl) if dictionary is not None else ""
                tokens.append(ScriptToken("dict", raw, text, idx))
                i += 3
                continue
            raw = data[i : i + 2]
            code = (b << 8) | data[i + 1]
            text = tbl.decode_char(code) if tbl else f"[{code:04X}]"
            tokens.append(ScriptToken("char", raw, text, code))
            i += 2
            continue
        # Single-byte glyph / possible control
        raw = bytes([b])
        text = tbl.decode_char(b) if tbl else f"[{b:02X}]"
        kind = "char" if (tbl and b in tbl.code_to_char) else "ctrl"
        tokens.append(ScriptToken(kind, raw, text, b))
        i += 1
    return tokens


def decode_payload(
    data: bytes,
    tbl: Tbl,
    dictionary: Dictionary,
) -> str:
    return dictionary.expand(data, tbl)


def encode_plaintext(text: str, tbl: Tbl) -> bytes:
    """Encode a plain Unicode string using TBL (no dictionary compression)."""
    out = bytearray()
    for ch in text:
        out.extend(tbl.encode_char(ch))
    return bytes(out)


def rebuild_dictionary(
    phrases: Sequence[bytes],
    *,
    data_start: int = DICT_DATA_START,
    ptr_start: int = DICT_PTR_START,
    bank_limit: int = BANK_SIZE,
    preserve_ptrs: Sequence[int] | None = None,
    base_bank: bytes | bytearray | None = None,
) -> Tuple[bytearray, List[int]]:
    """
    Pack null-terminated phrases into a 64KB bank image for segment 5F.

    If preserve_ptrs is provided, write each phrase at that exact offset
    (keeps original gap padding for byte-identical round-trips).
    Otherwise pack tightly from data_start upward.

    Phrase data may also extend into the region after the pointer table
    when ptr_start + 2*count leaves room (FF padding in stock ROM).
    Returns (bank_bytes, pointer_list).
    """
    bank = bytearray(base_bank if base_bank is not None else b"\x00" * bank_limit)
    if base_bank is None:
        bank[:] = b"\x00" * bank_limit

    ptrs: List[int] = []
    if preserve_ptrs is not None:
        if len(preserve_ptrs) != len(phrases):
            raise ValueError("preserve_ptrs length must match phrases")
        for phrase, cursor in zip(phrases, preserve_ptrs):
            end = cursor + len(phrase) + 1
            if end > bank_limit:
                raise ValueError(f"Phrase does not fit at {cursor:#x}")
            bank[cursor : cursor + len(phrase)] = phrase
            bank[cursor + len(phrase)] = 0
            ptrs.append(cursor)
    else:
        # Pack tightly in [data_start, ptr_start), then spill after pointer table.
        ptr_bytes = len(phrases) * 2
        ptr_end = ptr_start + ptr_bytes
        if ptr_end > bank_limit:
            raise ValueError("Pointer table exceeds bank")
        cursor = data_start
        spill_at = ptr_end
        for phrase in phrases:
            need = len(phrase) + 1
            if cursor + need <= ptr_start:
                at = cursor
                cursor += need
            else:
                at = spill_at
                if at + need > bank_limit:
                    raise ValueError(
                        f"Dictionary overflow at phrase {len(ptrs)}: need {at + need:#x}"
                    )
                spill_at += need
            ptrs.append(at)
            bank[at : at + len(phrase)] = phrase
            bank[at + len(phrase)] = 0

    for i, p in enumerate(ptrs):
        write_le16(bank, ptr_start + i * 2, p)
    return bank, ptrs


def patch_bank(rom: bytearray, segment: int, bank: bytes | bytearray) -> None:
    if len(bank) != BANK_SIZE:
        raise ValueError(f"Bank must be {BANK_SIZE} bytes, got {len(bank)}")
    start = stock_base(rom) + logical_bank_offset(segment)
    rom[start : start + BANK_SIZE] = bank


def patch_expansion_bank(
    rom: bytearray, segment: int, bank: bytes | bytearray
) -> None:
    if not is_expanded_rom(rom):
        raise ValueError("patch_expansion_bank requires a 16MB ROM")
    if len(bank) != BANK_SIZE:
        raise ValueError(f"Bank must be {BANK_SIZE} bytes, got {len(bank)}")
    seg = segment & 0xFF
    if seg > 0x7F:
        raise ValueError(f"expansion bank must be 0x00–0x7F, got {seg:#x}")
    start = seg * BANK_SIZE
    rom[start : start + BANK_SIZE] = bank


def ws_header(rom: bytes | bytearray) -> dict:
    """Parse the 16-byte WonderSwan footer (always at end of image)."""
    h = len(rom) - 16
    return {
        "maintenance": rom[h + 5],
        "developer": rom[h + 6],
        "color": rom[h + 7],
        "game_id": rom[h + 8],
        "version": rom[h + 9],
        "rom_size_code": rom[h + 10],
        "sram_size_code": rom[h + 11],
        "flags": rom[h + 12],
        "mapper": rom[h + 13],
        "checksum": le16(rom, h + 14),
        "stock_base": stock_base(rom),
        "size": len(rom),
    }


def update_ws_checksum(rom: bytearray) -> int:
    """Update the WonderSwan header checksum (sum of all bytes except checksum)."""
    stock_base_for_size(len(rom))  # validate size
    checksum = sum(rom[:-2]) & 0xFFFF
    write_le16(rom, len(rom) - 2, checksum)
    return checksum


def expand_rom_to_16mb(rom: bytes | bytearray) -> bytearray:
    """
    Prepend 8 MiB of 0xFF and set rom_size=$09.

    Append would break stock bank|0x80 mirroring; prepend keeps AL=C0 → bank40.
    """
    if len(rom) == ROM_SIZE_16MB:
        out = bytearray(rom)
        set_stock_base(ROM_SIZE)
        return out
    if len(rom) != ROM_SIZE:
        raise ValueError(f"expand expects 8MB or 16MB, got {len(rom):#x}")
    out = bytearray(b"\xFF" * ROM_SIZE) + bytearray(rom)
    # Footer moved with the original image; update size code + checksum.
    out[-16 + 0xA] = ROM_SIZE_CODE_16MB
    set_stock_base(ROM_SIZE)
    update_ws_checksum(out)
    return out

# Font helpers --------------------------------------------------------------

# Mono-Eye Gundams' text font is not stored as raw WSC VRAM tiles.
# Segment 40 contains a compact table at +0x440. Each 16-byte record is
# 8x8 pixels at 2bpp (four low-to-high 2-bit pixels per byte). The renderer
# expands each logical pixel to 2x2, producing a 16x16 on-screen glyph.
COMPACT_FONT_SEGMENT = 0x40
COMPACT_FONT_TABLE = 0x440
COMPACT_FONT_RECORD_SIZE = 16


def text_code_to_glyph_index(code: int) -> int:
    """Mirror the game's conversion at ROM 7A:0610 and 7A:0768."""
    if code >= 0xE000:
        return code - 0xDF20
    return code


def compact_font_file_offset(code: int) -> int:
    """File offset of a stock compact glyph (uses current stock_base)."""
    index = text_code_to_glyph_index(code)
    return (
        bank_offset(COMPACT_FONT_SEGMENT, COMPACT_FONT_TABLE)
        + index * COMPACT_FONT_RECORD_SIZE
    )


def decode_compact_font_record(record: bytes) -> List[List[int]]:
    """Decode one 16-byte compact record to an 8x8 matrix of 2bpp values.

    Layout (confirmed vs stock 『): 8 rows × 2 bytes, planar —
    byte0 = bit0 plane, byte1 = bit1 plane, LSB = leftmost pixel.
    """
    if len(record) != COMPACT_FONT_RECORD_SIZE:
        raise ValueError("Compact font record must be 16 bytes")
    pixels: List[List[int]] = [[0] * 8 for _ in range(8)]
    for row in range(8):
        plane0 = record[row * 2]
        plane1 = record[row * 2 + 1]
        for x in range(8):
            bit = x  # LSB = leftmost (screen-correct; MSB-left mirrored Hangul)
            lo = (plane0 >> bit) & 1
            hi = (plane1 >> bit) & 1
            pixels[row][x] = lo | (hi << 1)
    return pixels


def encode_compact_font_record(pixels: Sequence[Sequence[int]]) -> bytes:
    """Encode an 8x8 matrix to the game's 16-byte planar 2bpp record."""
    if len(pixels) != 8 or any(len(row) != 8 for row in pixels):
        raise ValueError("Compact font pixels must be 8x8")
    out = bytearray()
    for row in pixels:
        plane0 = 0
        plane1 = 0
        for x, value in enumerate(row):
            bit = x  # LSB = leftmost
            v = value & 0x3
            if v & 1:
                plane0 |= 1 << bit
            if v & 2:
                plane1 |= 1 << bit
        out.append(plane0)
        out.append(plane1)
    return bytes(out)

def decode_ws_4bpp_tile(tile32: bytes) -> List[List[int]]:
    """Decode one 8x8 4bpp WonderSwan tile (32 bytes) to 8x8 pixel indices."""
    if len(tile32) != 32:
        raise ValueError("4bpp tile must be 32 bytes")
    pixels = [[0] * 8 for _ in range(8)]
    for row in range(8):
        p0 = tile32[row * 4 + 0]
        p1 = tile32[row * 4 + 1]
        p2 = tile32[row * 4 + 2]
        p3 = tile32[row * 4 + 3]
        for col in range(8):
            bit = 7 - col
            pix = (
                ((p0 >> bit) & 1)
                | (((p1 >> bit) & 1) << 1)
                | (((p2 >> bit) & 1) << 2)
                | (((p3 >> bit) & 1) << 3)
            )
            pixels[row][col] = pix
    return pixels


def encode_ws_4bpp_tile(pixels: Sequence[Sequence[int]]) -> bytes:
    out = bytearray(32)
    for row in range(8):
        p0 = p1 = p2 = p3 = 0
        for col in range(8):
            bit = 7 - col
            pix = pixels[row][col] & 0xF
            p0 |= ((pix >> 0) & 1) << bit
            p1 |= ((pix >> 1) & 1) << bit
            p2 |= ((pix >> 2) & 1) << bit
            p3 |= ((pix >> 3) & 1) << bit
        out[row * 4 + 0] = p0
        out[row * 4 + 1] = p1
        out[row * 4 + 2] = p2
        out[row * 4 + 3] = p3
    return bytes(out)


def decode_ws_2bpp_tile(tile16: bytes) -> List[List[int]]:
    if len(tile16) != 16:
        raise ValueError("2bpp tile must be 16 bytes")
    pixels = [[0] * 8 for _ in range(8)]
    for row in range(8):
        p0 = tile16[row * 2 + 0]
        p1 = tile16[row * 2 + 1]
        for col in range(8):
            bit = 7 - col
            pixels[row][col] = ((p0 >> bit) & 1) | (((p1 >> bit) & 1) << 1)
    return pixels


def glyph_16x16_from_4bpp(data: bytes) -> List[List[int]]:
    """Assemble 16x16 glyph from 4 tiles in TL, TR, BL, BR order (128 bytes)."""
    if len(data) < 128:
        raise ValueError("Need 128 bytes for 16x16 4bpp glyph")
    tiles = [decode_ws_4bpp_tile(data[i * 32 : (i + 1) * 32]) for i in range(4)]
    canvas = [[0] * 16 for _ in range(16)]
    order = [(0, 0), (0, 8), (8, 0), (8, 8)]  # TL TR BL BR
    for ti, (oy, ox) in enumerate(order):
        for y in range(8):
            for x in range(8):
                canvas[oy + y][ox + x] = tiles[ti][y][x]
    return canvas


def glyph_16x16_to_4bpp(canvas: Sequence[Sequence[int]]) -> bytes:
    out = bytearray()
    order = [(0, 0), (0, 8), (8, 0), (8, 8)]
    for oy, ox in order:
        tile = [[canvas[oy + y][ox + x] & 0xF for x in range(8)] for y in range(8)]
        out.extend(encode_ws_4bpp_tile(tile))
    return bytes(out)
