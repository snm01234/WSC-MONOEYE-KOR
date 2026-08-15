#!/usr/bin/env python3
"""
Install extended dictionary tokens beyond index 0xFFF (multi-bank).

  E5 18 xx yy  →  index 0x1000 + ((xx << 8) | yy)

Magic 0xE518 is unused in script/aux/name75 zstring walks. yy != 0 (zstring).

Payload: expand banks 0x11 .. 0x11+N-1, each with 4096 LE16 ptrs + phrases.
  index 0x1000-0x1FFF → bank 0x11
  index 0x2000-0x2FFF → bank 0x12
  ...

Runtime: walker stashes index in WRAM; leaf maps bank = 0x11 + ((index-0x1000)>>12).
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    bank_al_expansion,
    is_expanded_rom,
    le16,
    load_rom,
    patch_expansion_bank,
    slice_expansion_bank,
    stock_base,
    update_ws_checksum,
)
from patch_font_hangul_hook import (  # noqa: E402
    CODE_SEG_7A,
    EXT_CAVE_SEG,
    far_jmp,
    near_call,
    near_jmp,
)

SITE1 = 0x7A0736
SITE1_RETURN = 0x7A0743
SITE1_MOVES = bytes.fromhex("8b46fc8b5efe8bca8bd7")

SITE2_FIXED = 0x7A080D
SITE2_RETURN = 0x7A081B
SITE2_MOVES = bytes.fromhex("8b46fc8b5efe8bca8b560a")
SITE2_OR_PREFIX = bytes.fromhex("0bd0")

LEAF = 0x7A06CE
LEAF_EXPECT = bytes.fromhex("558bec83ec08")
LEAF_CONTINUE = 0x7A06D4
LEAF_STREAM = 0x7A0743

HANGUL_STICKY = 0xFFBA
HANGUL_FAR_STUB = 0x7AFFF3
HANGUL_FAR_STUB_MAX = 4# Same-CS trampoline used by the copied leaf phrase loop. The loop executes
# in the F000 cave, while the stock leaf uses a near RET in A000:06CE; this
# wrapper keeps the near-call/near-ret pair in bank 7A and returns far to the
# cave caller.
LEAF_FAR_TRAMP = 0x7AFFF7
LEAF_FAR_TRAMP_MAX = 9
LEAF_CLEANUP = 0x7A07A8  # mov sp,bp; pop bp; ret (stock epilogue tail)

WRAM_INDEX = 0x19F8
WRAM_FLAG = 0x19FA

CAVE3 = 0x7FFD10
CAVE3_MAX = 480

MAGIC = 0xE518
LEAD0 = 0xE5
LEAD1 = 0x18

# Optional compact portal for short records.  It reuses ext3 bank 0x1C.
COMPACT3_MAGIC = 0xE519
COMPACT3_INDEX_BASE = 0xC000
COMPACT3_INDEX_END = 0xC0FF

EXP3_SEG0 = 0x11
EXP3_SLOTS = 0x1000
EXP3_PTR_OFF = 0x0000
INDEX_BASE = 0x1000
DEFAULT_NUM_BANKS = 12  # banks 0x11..0x1C → indices 0x1000..0xCFFF

BANK_MAP_SEG = 0x8000
BANK_SAVE_OFF = 0xDEB2  # AL = current ROM bank (stock dict leaf)
BANK_MAP_OFF = 0xDEB5  # map ROM bank from AL
FAD0_SEG = 0x8000
FAD0_OFF = 0xFAD0

HOOK_LEN = 5


def sab(rom: bytes | bytearray, off: int) -> int:
    return stock_base(rom) + off


def index_end(num_banks: int) -> int:
    return INDEX_BASE + num_banks * EXP3_SLOTS - 1


def bank_local_for_index(index: int) -> Tuple[int, int]:
    """Return (expansion_seg, local_slot) for an absolute ext3 index."""
    if index < INDEX_BASE:
        raise ValueError(f"not an ext3 index: {index:#x}")
    off = index - INDEX_BASE
    return EXP3_SEG0 + (off >> 12), off & 0xFFF


def find_site2(rom: bytes) -> tuple[int, int]:
    st = stock_base(rom)
    bank = bytes(rom[st + 0x7A0000 : st + 0x7B0000])
    # Prefer fixed site; accept either stock moves or our far-jmp hook.
    i = SITE2_FIXED & 0xFFFF
    if bank[i - 2 : i] == SITE2_OR_PREFIX and (
        bank[i : i + len(SITE2_MOVES)] == SITE2_MOVES or bank[i] == 0xEA
    ):
        return SITE2_FIXED, SITE2_RETURN
    pat = SITE2_OR_PREFIX + SITE2_MOVES
    j = bank.find(pat)
    if j < 0:
        raise RuntimeError("site2 not found")
    site2 = 0x7A0000 + j + 2
    return site2, site2 + len(SITE2_MOVES) + 3


def verify_rom(rom: bytes) -> dict:
    st = stock_base(rom)
    bank = bytes(rom[st + 0x7A0000 : st + 0x7B0000])
    s1 = bytes(bank[SITE1 & 0xFFFF : (SITE1 & 0xFFFF) + len(SITE1_MOVES)])
    if s1 != SITE1_MOVES and bank[SITE1 & 0xFFFF] != 0xEA:
        raise RuntimeError(f"site1 unexpected: {s1.hex()}")
    leaf = bytes(bank[LEAF & 0xFFFF : (LEAF & 0xFFFF) + 6])
    if leaf != LEAF_EXPECT and leaf[0] != 0xEA:
        raise RuntimeError(f"leaf unexpected: {leaf.hex()}")
    site2, site2_ret = find_site2(rom)
    return {"site2": site2, "site2_return": site2_ret}


def _patch_rel8(buf: bytearray, at: int, target: int) -> None:
    disp = target - (at + 2)
    if not -128 <= disp <= 127:
        raise RuntimeError(f"rel8 out of range: {disp}")
    buf[at + 1] = disp & 0xFF


def build_handlers(site2_return: int, *, compact3: bool = False) -> tuple[bytes, dict]:
    out = bytearray()
    parts: dict[str, int] = {}

    def emit_walker(moves: bytes, ret_ip: int, name: str) -> None:
        nonlocal out
        parts[name] = len(out)
        if not compact3:
            out += b"\x81\xFA" + struct.pack("<H", MAGIC)
            jne_at = len(out)
            out += b"\x75\x00"
            out += b"\xFF\x46\xF8\xC4\x5E\xF8\x4B\x26\x8A\x27"
            out += b"\xFF\x46\xF8\xC4\x5E\xF8\x4B\x26\x8A\x07"
            out += b"\x89\xC3"
            out += b"\x81\xC3" + struct.pack("<H", INDEX_BASE)
            out += b"\x89\x1E" + struct.pack("<H", WRAM_INDEX)
            out += b"\xC6\x06" + struct.pack("<H", WRAM_FLAG) + b"\x01"
            out += b"\xBA\x00\xF0"
            not_m = len(out)
            _patch_rel8(out, jne_at, not_m)
        else:
            # E5 19 bb: read one trailing byte, map it to C000+bb, then
            # share the same stock moves and Hangul far stub as ext3.
            out += b"\x81\xFA" + struct.pack("<H", COMPACT3_MAGIC)
            compact_jne = len(out)
            out += b"\x75\x00"
            out += b"\xFF\x46\xF8\xC4\x5E\xF8\x4B\x26\x8A\x07"
            out += b"\x30\xE4"  # xor ah,ah
            out += b"\x89\xC3"
            out += b"\x81\xC3" + struct.pack("<H", COMPACT3_INDEX_BASE)
            out += b"\x89\x1E" + struct.pack("<H", WRAM_INDEX)
            out += b"\xC6\x06" + struct.pack("<H", WRAM_FLAG) + b"\x01"
            out += b"\xBA\x00\xF0"
            compact_skip = len(out)
            out += b"\xEB\x00"
            e518_check = len(out)
            _patch_rel8(out, compact_jne, e518_check)

            out += b"\x81\xFA" + struct.pack("<H", MAGIC)
            ext3_jne = len(out)
            out += b"\x75\x00"
            out += b"\xFF\x46\xF8\xC4\x5E\xF8\x4B\x26\x8A\x27"
            out += b"\xFF\x46\xF8\xC4\x5E\xF8\x4B\x26\x8A\x07"
            out += b"\x89\xC3"
            out += b"\x81\xC3" + struct.pack("<H", INDEX_BASE)
            out += b"\x89\x1E" + struct.pack("<H", WRAM_INDEX)
            out += b"\xC6\x06" + struct.pack("<H", WRAM_FLAG) + b"\x01"
            out += b"\xBA\x00\xF0"
            not_m = len(out)
            _patch_rel8(out, ext3_jne, not_m)
            _patch_rel8(out, compact_skip, not_m)
        out += moves
        out += b"\x9A" + struct.pack("<HH", HANGUL_FAR_STUB & 0xFFFF, CODE_SEG_7A)
        out += far_jmp(ret_ip & 0xFFFF, CODE_SEG_7A)

    emit_walker(SITE1_MOVES, SITE1_RETURN, "walker1")
    emit_walker(SITE2_MOVES, site2_return, "walker2")

    # leaf: multi-bank map
    parts["leaf"] = len(out)
    out += b"\x80\x3E" + struct.pack("<H", WRAM_FLAG) + b"\x01"
    je_leaf = len(out)
    out += b"\x74\x00"  # ext3 flag set -> enter the cave handler below
    not_jmp = len(out)
    out += b"\xE9\x00\x00"  # long branch around the handler to the stock path
    leaf_body = len(out)
    out += b"\xC6\x06" + struct.pack("<H", WRAM_FLAG) + b"\x00"
    out += LEAF_EXPECT
    out += b"\x51\x52\x56\x57"
    out += b"\x89\x46\xFC\x89\x5E\xFE"
    out += b"\x8B\xFA"  # mov di, dx
    out += b"\x8B\x36" + struct.pack("<H", WRAM_INDEX)  # mov si, index
    # Save the pre-map ROM1 bank. The copied phrase loop below restores it
    # inside this handler after the last ES:phrase read; it never relies on
    # stock 7A:074C for the ext3 path.
    out += b"\x9A" + struct.pack("<HH", BANK_SAVE_OFF, BANK_MAP_SEG)
    out += b"\x50"  # push ax (owned by this handler; popped below)
    # AL = 0x11 + ((index - 0x1000) >> 12); SI = local slot
    out += b"\x89\xF0"  # mov ax, si
    out += b"\x2D" + struct.pack("<H", INDEX_BASE)  # sub ax, 1000
    out += b"\x89\xC3"  # mov bx, ax (full offset)
    out += b"\xB1\x0C\xD3\xE8"  # mov cl,12; shr ax, cl
    out += b"\x04" + bytes([EXP3_SEG0])  # add al, 0x11
    out += b"\x53"  # push bx (offset across DEB5)
    out += b"\x9A" + struct.pack("<HH", BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\xBB\x00\x30\x8E\xC3"  # es = 3000
    out += b"\x5E"  # pop si (offset)
    out += b"\x81\xE6\xFF\x0F"  # and si, 0x0FFF
    out += b"\xD1\xE6"  # shl si, 1
    out += b"\x26\x8B\x84" + struct.pack("<H", EXP3_PTR_OFF)
    out += b"\x9A" + struct.pack("<HH", FAD0_OFF, FAD0_SEG)
    out += b"\x89\x46\xF8\x89\x5E\xFA"

    # Copy the stock phrase loop into the cave so its ES:phrase reads remain
    # on the expansion bank until NUL. The stock near CALL to LEAF cannot be
    # copied into the F000 cave, so use a same-CS A000 trampoline.
    stream_entry = len(out)
    out += b"\xC4\x5E\xF8\x26\x80\x3F\x00\x75\x00"
    stream_loop = len(out)
    out += bytes.fromhex(
        "ff46f8 c45ef8 4b 268a17 32f6 81fae000 7212 "
        "b108 d3e2 ff46f8 c45ef8 4b 268a1f 32ff 0bd3 "
        "8b46fc 8b5efe 8bca 8bd7"
    )
    out += b"\x9A" + struct.pack("<HH", LEAF_FAR_TRAMP & 0xFFFF, CODE_SEG_7A)
    out += bytes.fromhex("c45ef8 26803f00")
    stream_repeat = len(out)
    out += b"\x75\x00"
    _patch_rel8(out, stream_entry + 7, stream_loop)
    _patch_rel8(out, stream_repeat, stream_loop)

    # The ext3 path owns both the saved bank and the stock-register saves.
    # Hand only the frame tail (mov sp,bp; pop bp; ret) back to stock.
    out += b"\x58"  # pop ax: old bank
    out += b"\x9A" + struct.pack("<HH", BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\x5F\x5E\x5A\x59"  # pop di, si, dx, cx
    out += far_jmp(LEAF_CLEANUP & 0xFFFF, CODE_SEG_7A)

    not_flag = len(out)
    _patch_rel8(out, je_leaf, leaf_body)
    out[not_jmp : not_jmp + 3] = near_jmp(
        CAVE3 + not_jmp, CAVE3 + not_flag
    )
    out += LEAF_EXPECT
    out += far_jmp(LEAF_CONTINUE & 0xFFFF, CODE_SEG_7A)
    if len(out) > CAVE3_MAX:
        raise RuntimeError(f"cave3 too large: {len(out)} > {CAVE3_MAX}")
    return bytes(out), parts


def format_ext3_bank() -> bytes:
    bank = bytearray([0xFF] * BANK_SIZE)
    empty_at = EXP3_SLOTS * 2
    bank[empty_at] = 0x00
    for i in range(EXP3_SLOTS):
        struct.pack_into("<H", bank, i * 2, empty_at)
    return bytes(bank)


# Back-compat alias
format_bank11 = format_ext3_bank


def token_from_ext3_index(index: int, *, num_banks: int = DEFAULT_NUM_BANKS) -> bytes:
    if not INDEX_BASE <= index <= index_end(num_banks):
        raise ValueError(f"ext3 index out of range: {index:#x}")
    slot = index - INDEX_BASE
    if (slot & 0xFF) == 0:
        raise ValueError(f"ext3 index trail would be NUL: {index:#x}")
    return bytes([LEAD0, LEAD1, (slot >> 8) & 0xFF, slot & 0xFF])


def list_free_ext3_indices(
    rom: bytes | bytearray, *, num_banks: int = DEFAULT_NUM_BANKS
) -> List[int]:
    """Indices whose phrase is empty (ptr → lone NUL)."""
    free: List[int] = []
    empty_at = EXP3_SLOTS * 2
    for bi in range(num_banks):
        seg = EXP3_SEG0 + bi
        bank = slice_expansion_bank(rom, seg)
        # Unformatted bank (all FF) → treat all safe locals as free after format
        if all(b == 0xFF for b in bank[:64]):
            for local in range(EXP3_SLOTS):
                idx = INDEX_BASE + (bi << 12) + local
                raw = idx - INDEX_BASE
                # E5 18 xx yy lives inside a NUL-terminated outer record.
                # Both xx and yy must be non-zero; xx==00 caused the bank61
                # trailing-JP-glyph regression reported on 2026-08-07.
                if ((raw >> 8) & 0xFF) != 0 and (raw & 0xFF) != 0:
                    free.append(idx)
            continue
        for local in range(EXP3_SLOTS):
            idx = INDEX_BASE + (bi << 12) + local
            raw = idx - INDEX_BASE
            if ((raw >> 8) & 0xFF) == 0 or (raw & 0xFF) == 0:
                continue
            poff = le16(bank, local * 2)
            if poff >= BANK_SIZE:
                free.append(idx)
                continue
            if poff == empty_at or bank[poff] == 0:
                free.append(idx)
    return free


def write_ext3_dictionary_slots(
    rom: bytearray,
    slot_payload: Dict[int, bytes],
    *,
    num_banks: int = DEFAULT_NUM_BANKS,
) -> dict:
    """Write absolute indices across banks 0x11.."""
    if not slot_payload:
        return {"written": 0, "by_bank": {}, "skipped_overflow": 0}

    # Ensure banks exist / formatted
    for bi in range(num_banks):
        seg = EXP3_SEG0 + bi
        bank = slice_expansion_bank(rom, seg)
        if all(b == 0xFF for b in bank[:64]):
            patch_expansion_bank(rom, seg, format_ext3_bank())

    by_bank: Dict[int, Dict[int, bytes]] = {}
    for index, enc in slot_payload.items():
        seg, local = bank_local_for_index(index)
        if seg >= EXP3_SEG0 + num_banks:
            raise RuntimeError(f"Index {index:#x} needs bank {seg:#x} > configured")
        by_bank.setdefault(seg, {})[local] = enc

    written = 0
    skipped_overflow = 0
    phrase_ends: Dict[str, int] = {}
    empty_at = EXP3_SLOTS * 2

    for seg, locals_map in sorted(by_bank.items()):
        bank = bytearray(slice_expansion_bank(rom, seg))
        cursor = empty_at + 1
        for i in range(EXP3_SLOTS):
            poff = le16(bank, i * 2)
            if poff < empty_at or poff >= BANK_SIZE:
                continue
            end = poff
            while end < BANK_SIZE and bank[end] != 0:
                end += 1
            end += 1
            cursor = max(cursor, end)

        for local, encoded in sorted(locals_map.items()):
            if b"\x00" in encoded:
                raise RuntimeError(f"NUL inside ext3 payload local {local:#x}")
            need = len(encoded) + 1
            if cursor + need > BANK_SIZE:
                skipped_overflow += 1
                continue
            bank[cursor : cursor + len(encoded)] = encoded
            bank[cursor + len(encoded)] = 0
            struct.pack_into("<H", bank, local * 2, cursor)
            cursor += need
            written += 1
        patch_expansion_bank(rom, seg, bank)
        phrase_ends[f"{seg:02X}"] = cursor

    return {
        "written": written,
        "by_bank": phrase_ends,
        "skipped_overflow": skipped_overflow,
    }


def build_hangul_far_stub() -> bytes:
    stub = near_call(HANGUL_FAR_STUB, 0x7A0000 | HANGUL_STICKY) + b"\xCB"
    if len(stub) > HANGUL_FAR_STUB_MAX:
        raise RuntimeError("hangul far stub too large")
    return stub


def build_leaf_far_tramp() -> bytes:
    """Same-CS wrapper: near-call the stock leaf, then far-return to F000."""
    tramp = near_call(LEAF_FAR_TRAMP, LEAF) + b"\xC3\xCB"
    if len(tramp) > LEAF_FAR_TRAMP_MAX:
        raise RuntimeError("leaf far trampoline too large")
    return tramp

def install(
    rom: bytearray,
    *,
    force_format: bool = False,
    num_banks: int = DEFAULT_NUM_BANKS,
    compact3: bool = False,
) -> dict:
    if not is_expanded_rom(rom):
        raise RuntimeError("16MiB ROM required")
    if not 1 <= num_banks <= 16:
        raise RuntimeError(f"num_banks must be 1..16, got {num_banks}")
    sites = verify_rom(rom)
    site2 = int(sites["site2"])
    site2_ret = int(sites["site2_return"])

    stub = build_hangul_far_stub()
    stub_region = bytes(
        rom[sab(rom, HANGUL_FAR_STUB) : sab(rom, HANGUL_FAR_STUB) + len(stub)]
    )
    if not all(b == 0xFF for b in stub_region) and stub_region[0] not in (0xE8, 0x9A):
        raise RuntimeError(
            f"hangul far stub region {HANGUL_FAR_STUB:06X} busy: {stub_region.hex()}"
        )
    rom[sab(rom, HANGUL_FAR_STUB) : sab(rom, HANGUL_FAR_STUB) + len(stub)] = stub

    leaf_tramp = build_leaf_far_tramp()
    leaf_tramp_region = bytes(
        rom[sab(rom, LEAF_FAR_TRAMP) : sab(rom, LEAF_FAR_TRAMP) + len(leaf_tramp)]
    )
    if not all(b == 0xFF for b in leaf_tramp_region) and leaf_tramp_region != leaf_tramp:
        raise RuntimeError(
            f"leaf far trampoline {LEAF_FAR_TRAMP:06X} busy: {leaf_tramp_region.hex()}"
        )
    rom[sab(rom, LEAF_FAR_TRAMP) : sab(rom, LEAF_FAR_TRAMP) + len(leaf_tramp)] = leaf_tramp
    blob, parts = build_handlers(site2_ret, compact3=compact3)
    cave_file = sab(rom, CAVE3)
    region = bytes(rom[cave_file : cave_file + len(blob)])
    # Allow reinstall over prior ext3 cave (starts with cmp dx, MAGIC).
    if not all(b == 0xFF for b in region) and region[:2] not in (
        b"\x81\xFA",
        b"\x80\x3E",
    ):
        # Walker1 starts with 81 FA; if leaf-only remnant, still refuse unknown.
        if region[0] not in (0x81, 0x80, 0xEA, 0x9A):
            raise RuntimeError(f"cave3 {CAVE3:06X} not free: {region[:8].hex()}")
    rom[cave_file : cave_file + len(blob)] = blob

    w1 = (CAVE3 + parts["walker1"]) & 0xFFFF
    w2 = (CAVE3 + parts["walker2"]) & 0xFFFF
    leaf_h = (CAVE3 + parts["leaf"]) & 0xFFFF

    rom[sab(rom, SITE1) : sab(rom, SITE1) + HOOK_LEN] = far_jmp(w1, EXT_CAVE_SEG)
    rom[sab(rom, site2) : sab(rom, site2) + HOOK_LEN] = far_jmp(w2, EXT_CAVE_SEG)
    rom[sab(rom, LEAF) : sab(rom, LEAF) + 6] = far_jmp(leaf_h, EXT_CAVE_SEG) + b"\x90"

    formatted = []
    for bi in range(num_banks):
        seg = EXP3_SEG0 + bi
        bank = slice_expansion_bank(rom, seg)
        need = force_format or all(b == 0xFF for b in bank[:64])
        # Never wipe bank0 (0x11) unless force_format — preserves prior phrases.
        if bi == 0 and not force_format and not all(b == 0xFF for b in bank[:64]):
            need = False
        if need:
            patch_expansion_bank(rom, seg, format_ext3_bank())
            formatted.append(seg)

    return {
        "magic": f"{MAGIC:04X}",
        "encoding": "E5 18 xx yy -> index 0x1000+((xx<<8)|yy)",
        "token_bytes": 4,
        "compact3": bool(compact3),
        "compact3_encoding": (
            "E5 19 bb -> index 0xC000+bb" if compact3 else None
        ),
        "compact3_token_bytes": 3 if compact3 else None,
        "compact3_index_base": COMPACT3_INDEX_BASE if compact3 else None,
        "compact3_index_end": COMPACT3_INDEX_END if compact3 else None,
        "wram_index": f"{WRAM_INDEX:04X}",
        "wram_flag": f"{WRAM_FLAG:04X}",
        "cave": f"{CAVE3:06X}",
        "cave_len": len(blob),
        "parts": {k: f"{CAVE3 + v:06X}" for k, v in parts.items()},
        "site1": f"{SITE1:06X}",
        "site2": f"{site2:06X}",
        "leaf": f"{LEAF:06X}",
        "leaf_far_tramp": f"{LEAF_FAR_TRAMP:06X}",
        "hangul_far_stub": f"{HANGUL_FAR_STUB:06X}",
        "exp_seg0": f"{EXP3_SEG0:02X}",
        "num_banks": num_banks,
        "banks": [f"{EXP3_SEG0 + i:02X}" for i in range(num_banks)],
        "formatted_banks": [f"{s:02X}" for s in formatted],
        "slots_per_bank": EXP3_SLOTS,
        "index_base": INDEX_BASE,
        "index_end": index_end(num_banks),
        "note": "multi-bank ext3; bank = 0x11 + ((index-0x1000)>>12)",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rom", type=Path, default=ROOT / "out/patch/monoeye_ko_expanded.wsc")
    ap.add_argument("--out-rom", type=Path, default=ROOT / "out/patch/monoeye_ko_3byte_work.wsc")
    ap.add_argument(
        "--out-meta",
        type=Path,
        default=ROOT / "out/patch/ext3_dictionary_meta.json",
    )
    ap.add_argument("--force-format", action="store_true")
    ap.add_argument("--num-banks", type=int, default=DEFAULT_NUM_BANKS)
    ap.add_argument("--compact3", action="store_true")
    args = ap.parse_args()

    rom = bytearray(load_rom(args.rom))
    meta = install(rom, force_format=args.force_format, num_banks=args.num_banks, compact3=args.compact3)
    cs = update_ws_checksum(rom)
    meta["checksum"] = f"{cs:04X}"
    args.out_rom.parent.mkdir(parents=True, exist_ok=True)
    args.out_rom.write_bytes(rom)
    args.out_meta.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=2))
    print("->", args.out_rom)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
