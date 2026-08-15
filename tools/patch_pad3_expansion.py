#!/usr/bin/env python3
"""
Install pad3 Hangul glyphs in the 16MB prepended expansion region.

Slot map (marked Hangul, bit15-tagged):
  0–95     pad1  bank40:F9F8     (stock AL=C0 window, no switch)
  96–527   pad2  bank41:E4F4     OUT C3 AL=C1, then restore
  528+     pad3  expand bank00   OUT C3 AL=00 (16MB prepend only)

Primary cave stays ≤62B at 7F:FC4E-FC8B and far-jumps to pad_hi for slot≥96.
pad_hi dispatches pad2 vs pad3; restore helper maps bank1 back to stock 40.

Builds on monoeye_ko_expanded_8mb.wsc (8MiB tip before 16MB promotion).
Do not feed the promoted 16MB monoeye_ko_expanded.wsc back in as input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from build_hangul_font import render_compact_glyph  # noqa: E402
from font_pipeline import find_system_font  # noqa: E402
from monoeye_rom import (  # noqa: E402
    COMPACT_FONT_RECORD_SIZE,
    ROM_SIZE_16MB,
    bank_al_expansion,
    bank_al_stock,
    encode_compact_font_record,
    expand_rom_to_16mb,
    expansion_bank_offset,
    is_expanded_rom,
    load_rom,
    logical_bank_offset,
    stock_base,
    update_ws_checksum,
    ws_header,
)
from patch_font_hangul_hook import (  # noqa: E402
    CODE_SEG_7A,
    EXT_CAVE,
    EXT_CAVE_SEG,
    HANGUL_PRIMARY_BUDGET,
    PAD1_FILE,
    PAD1_OFF,
    PAD1_SLOTS,
    PAD2_FILE,
    PAD2_OFF,
    PRIMARY_RETURN,
    PRIMARY_SITE,
    TAG_BIT,
    far_jmp,
    patch_rel8,
)

# Shared with _patch_pad2_bankswitch.py
BANK_MAP_SEG = 0x8000
BANK_MAP_OFF = 0xDEB5
BANK_RESTORE_FLAG = 0x19FE
PAD2_HELPER = 0x7FFCAB
POST_POP_SITE = 0x7A0548
POST_POP_STOCK = bytes.fromhex("5be80efe")

PAD2_SLOTS = 432
PAD2_BANK_AL = bank_al_stock(0x41)  # C1
PAD3_BANK = 0x00
PAD3_OFF = 0x0000
PAD3_BANK_AL = bank_al_expansion(PAD3_BANK)  # 00
PAD12_SLOTS = PAD1_SLOTS + PAD2_SLOTS  # 528

# The 8MiB cold-rebuild input is intentionally based on the pristine ROM, not
# on the old translated tip. The pristine image does not contain the glyph
# records that the old padding-store build generated, however. These are the
# only three stock-relative ranges that are allowed to be copied from the
# dedicated font source. Keeping the scope explicit prevents old script,
# dictionary, event, or UI changes from leaking into a cold rebuild.
PAD3_MIGRATE_END = 1027

# bank3F store used by hangul_char_map for slots 528–1026 (pad2-relative)
BANK3F_PAD2_FILE = 0x3FC5CE

FONT_PADDING_RANGES = (
    ('pad1', PAD1_FILE, PAD1_SLOTS * COMPACT_FONT_RECORD_SIZE),
    ('pad2', PAD2_FILE, PAD2_SLOTS * COMPACT_FONT_RECORD_SIZE),
    (
        'legacy_pad3_seed',
        BANK3F_PAD2_FILE + (PAD12_SLOTS - PAD1_SLOTS) * COMPACT_FONT_RECORD_SIZE,
        (PAD3_MIGRATE_END - PAD12_SLOTS) * COMPACT_FONT_RECORD_SIZE,
    ),
)


def sab(rom: bytearray | bytes, off: int) -> int:
    """Stock-absolute file offset (8MB identity; 16MB prepend +0x800000)."""
    return stock_base(rom) + off


def build_restore_helper() -> bytes:
    out = bytearray()
    out += b"\x50"
    out += b"\x80\x3E" + struct.pack("<H", BANK_RESTORE_FLAG) + b"\x01"
    jne_at = len(out)
    out += b"\x75\x00"
    out += b"\xB0" + bytes([bank_al_stock(0x40)])
    out += b"\x9A" + struct.pack("<HH", BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\xC6\x06" + struct.pack("<H", BANK_RESTORE_FLAG) + b"\x00"
    skip = len(out)
    out += b"\x58"
    out += b"\xCB"
    patch_rel8(out, jne_at, skip)
    return bytes(out)


def build_primary_cave(
    base_index: int, *, pad_hi_off: int, restore_off: int
) -> bytes:
    """Tagged: pad1 inline; slot≥96 → pad_hi. Untagged stock path."""
    out = bytearray()
    out += b"\x9A" + struct.pack("<HH", restore_off & 0xFFFF, EXT_CAVE_SEG)
    out += b"\xF7\xC3" + struct.pack("<H", TAG_BIT)
    jz_normal_at = len(out)
    out += b"\x74\x00"
    out += b"\x81\xE3\xFF\x7F"
    out += b"\x81\xEB" + struct.pack("<H", base_index)
    out += b"\x81\xFB" + struct.pack("<H", PAD1_SLOTS)
    jae_hi_at = len(out)
    out += b"\x73\x00"
    # pad1
    out += b"\xC1\xE3\x04"
    out += b"\xBA" + struct.pack("<H", PAD1_OFF)
    out += b"\x03\xD3"
    out += far_jmp(PRIMARY_RETURN & 0xFFFF, CODE_SEG_7A)
    hi_at = len(out)
    out += far_jmp(pad_hi_off & 0xFFFF, EXT_CAVE_SEG)
    normal_off = len(out)
    out += b"\xC1\xE3\x04"
    out += b"\x03\xD3"
    out += far_jmp(PRIMARY_RETURN & 0xFFFF, CODE_SEG_7A)
    patch_rel8(out, jz_normal_at, normal_off)
    patch_rel8(out, jae_hi_at, hi_at)
    return bytes(out)


def build_pad_hi_helper(
    *,
    pad2_bank_al: int = PAD2_BANK_AL,
    pad2_off: int = PAD2_OFF,
    pad3_bank_al: int = PAD3_BANK_AL,
    pad3_off: int = PAD3_OFF,
    pad12_slots: int = PAD12_SLOTS,
) -> bytes:
    """
    Entry: BX = slot (0-based, already ≥ PAD1_SLOTS).
    pad2 if slot < 528; else pad3 in expansion bank.
    """
    out = bytearray()
    out += b"\x81\xFB" + struct.pack("<H", pad12_slots)  # cmp bx,528
    jae_pad3_at = len(out)
    out += b"\x73\x00"

    # pad2
    out += b"\x81\xEB" + struct.pack("<H", PAD1_SLOTS)
    out += b"\xC1\xE3\x04"
    out += b"\xB0" + bytes([pad2_bank_al & 0xFF])
    out += b"\x9A" + struct.pack("<HH", BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\xC6\x06" + struct.pack("<H", BANK_RESTORE_FLAG) + b"\x01"
    out += b"\xBA" + struct.pack("<H", pad2_off)
    out += b"\x03\xD3"
    out += b"\xB9\x00\x30"
    out += far_jmp(PRIMARY_RETURN & 0xFFFF, CODE_SEG_7A)

    pad3_at = len(out)
    out += b"\x81\xEB" + struct.pack("<H", pad12_slots)
    out += b"\xC1\xE3\x04"
    out += b"\xB0" + bytes([pad3_bank_al & 0xFF])
    out += b"\x9A" + struct.pack("<HH", BANK_MAP_OFF, BANK_MAP_SEG)
    out += b"\xC6\x06" + struct.pack("<H", BANK_RESTORE_FLAG) + b"\x01"
    out += b"\xBA" + struct.pack("<H", pad3_off)
    out += b"\x03\xD3"
    out += b"\xB9\x00\x30"
    out += far_jmp(PRIMARY_RETURN & 0xFFFF, CODE_SEG_7A)

    patch_rel8(out, jae_pad3_at, pad3_at)
    return bytes(out)


def pad3_file_offset(rom: bytearray | bytes, slot: int) -> int:
    """File offset for logical Hangul slot in pad3 (slot ≥ 528)."""
    if slot < PAD12_SLOTS:
        raise ValueError(f"pad3 slot must be >= {PAD12_SLOTS}, got {slot}")
    if not is_expanded_rom(rom):
        raise RuntimeError("pad3 requires 16MB ROM")
    idx = slot - PAD12_SLOTS
    return expansion_bank_offset(PAD3_BANK, PAD3_OFF + idx * COMPACT_FONT_RECORD_SIZE)


def bank3f_file_offset(rom: bytearray | bytes, slot: int) -> int:
    """Legacy map location: bank3F pad2-relative (slot ≥ 96)."""
    return sab(rom, BANK3F_PAD2_FILE + (slot - PAD1_SLOTS) * COMPACT_FONT_RECORD_SIZE)


def copy_font_padding_source(rom: bytearray, source_path: Path) -> dict:
    """Copy only known glyph-padding records from a separate font source.

    A cold rebuild must not use the old 8MiB translated tip as its ROM base:
    that image carries historical data-bank and UI invasions. It is safe to
    use it as a font source only when the copy is restricted to the three
    ranges above. The source is loaded as raw bytes so it cannot change the
    global stock-base state used by the 16MiB target.
    """
    if not source_path.exists():
        raise RuntimeError(f"font source does not exist: {source_path}")
    source = bytearray(source_path.read_bytes())
    source_base = stock_base(source)
    target_base = stock_base(rom)
    copied = []
    for name, logical_start, length in FONT_PADDING_RANGES:
        src_start = source_base + logical_start
        dst_start = target_base + logical_start
        if src_start < 0 or src_start + length > len(source):
            raise RuntimeError(
                f"font source range out of bounds: {name} "
                f"{logical_start:06X}+{length:X}"
            )
        if dst_start < 0 or dst_start + length > len(rom):
            raise RuntimeError(
                f"target range out of bounds: {name} "
                f"{logical_start:06X}+{length:X}"
            )
        payload = bytes(source[src_start : src_start + length])
        non_ff = sum(b != 0xFF for b in payload)
        non_zero = sum(b != 0 for b in payload)
        if non_ff == 0 or non_zero == 0:
            raise RuntimeError(
                f"font source range is empty: {name} "
                f"{logical_start:06X}+{length:X}"
            )
        rom[dst_start : dst_start + length] = payload
        copied.append(
            {
                "name": name,
                "stock_offset": f"{logical_start:06X}",
                "length": length,
                "non_ff": non_ff,
                "non_zero": non_zero,
            }
        )
    return {
        "source": str(source_path),
        "source_size": len(source),
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "scope": "font_padding_only",
        "ranges": copied,
        "note": (
            "Only pad1, pad2, and the 499 legacy bank3F records used to seed "
            "pad3 are copied; all other source bytes are ignored."
        ),
    }


def install_hooks(
    rom: bytearray,
    *,
    base_index: int = 0x820,
    sticky_count: int,
) -> dict:
    if not is_expanded_rom(rom):
        raise RuntimeError("install_hooks requires 16MB prepended ROM")
    if rom[sab(rom, PRIMARY_SITE)] != 0xE9:
        raise RuntimeError("primary site missing near jmp — base Hangul hook required")

    rom[sab(rom, POST_POP_SITE) : sab(rom, POST_POP_SITE) + 4] = POST_POP_STOCK

    pad_hi = build_pad_hi_helper()
    restore = build_restore_helper()
    restore_off = PAD2_HELPER + len(pad_hi)
    primary = build_primary_cave(
        base_index, pad_hi_off=PAD2_HELPER, restore_off=restore_off
    )
    if len(primary) > HANGUL_PRIMARY_BUDGET:
        raise RuntimeError(
            f"primary {len(primary)}B exceeds budget {HANGUL_PRIMARY_BUDGET}"
        )

    # Do not touch the fixed ext_dict helper at 7F:FC8C .. PAD2_HELPER.
    rom[sab(rom, PAD2_HELPER) : sab(rom, PAD2_HELPER) + len(pad_hi)] = pad_hi
    rom[sab(rom, restore_off) : sab(rom, restore_off) + len(restore)] = restore
    # Do not scrub past restore — ext3 dict cave begins at 7FFD10.
    scrub = sab(rom, restore_off) + len(restore)
    cave3 = sab(rom, 0x7FFD10)
    if scrub < cave3:
        rom[scrub:cave3] = b"\xFF" * (cave3 - scrub)

    rom[sab(rom, EXT_CAVE) : sab(rom, EXT_CAVE) + len(primary)] = primary
    if len(primary) < HANGUL_PRIMARY_BUDGET:
        a = sab(rom, EXT_CAVE) + len(primary)
        rom[a : sab(rom, EXT_CAVE) + HANGUL_PRIMARY_BUDGET] = b"\xFF" * (
            HANGUL_PRIMARY_BUDGET - len(primary)
        )

    # upgrade_store_cave_sticky uses 8MB-absolute addresses; use 16MB-aware path.
    sticky = _upgrade_store_sticky_16mb(rom, base_index=base_index, count=sticky_count)

    return {
        "primary_len": len(primary),
        "pad_hi_len": len(pad_hi),
        "pad_hi": f"{PAD2_HELPER:06X}",
        "restore": f"{restore_off:06X}",
        "pad2_al": f"{PAD2_BANK_AL:02X}",
        "pad3_al": f"{PAD3_BANK_AL:02X}",
        "pad3_bank": f"{PAD3_BANK:02X}",
        "sticky": sticky,
        "checksum": f"{update_ws_checksum(rom):04X}",
    }


def _upgrade_store_sticky_16mb(
    rom: bytearray, *, base_index: int, count: int
) -> dict:
    """Sticky store rewrite with correct 16MB stock_base."""
    from patch_font_hangul_hook import STORE_SITE, build_store_cave, MAIN_CAVE, MAIN_CAVE_MAX

    site = sab(rom, STORE_SITE)
    if rom[site] != 0xE8:
        raise RuntimeError("Store site is not a near call")
    rel = struct.unpack_from("<H", rom, site + 1)[0]
    store_ip = (STORE_SITE + 3 + rel) & 0xFFFF
    store_abs = sab(rom, (STORE_SITE & 0xFF0000) | store_ip)
    new_store = build_store_cave(base_index, count)
    cave_end = sab(rom, MAIN_CAVE + MAIN_CAVE_MAX)
    if store_abs + len(new_store) > cave_end:
        raise RuntimeError("Sticky store cave does not fit")
    rom[store_abs : store_abs + len(new_store)] = new_store
    return {
        "store_abs": f"{store_abs:06X}",
        "store_cave_len": len(new_store),
        "base_index": base_index,
        "count": count,
        "mode": "sticky_16mb",
    }


def migrate_bank3f_to_pad3(
    rom: bytearray, *, from_slot: int = PAD12_SLOTS, to_slot_excl: int
) -> dict:
    """Copy glyphs from legacy bank3F map locations into expansion pad3."""
    copied = 0
    empty = 0
    for slot in range(from_slot, to_slot_excl):
        src = bank3f_file_offset(rom, slot)
        dst = pad3_file_offset(rom, slot)
        rec = bytes(rom[src : src + COMPACT_FONT_RECORD_SIZE])
        if all(b == 0xFF for b in rec) or all(b == 0 for b in rec):
            empty += 1
        rom[dst : dst + COMPACT_FONT_RECORD_SIZE] = rec
        copied += 1
    return {"copied": copied, "empty_src": empty, "from": from_slot, "to": to_slot_excl}


def bake_overflow_chars(
    rom: bytearray,
    mapping: dict,
    *,
    font_path: str,
    start_slot: int,
    chars: List[str],
) -> dict:
    """Assign overflow Hangul to pad3 slots starting at start_slot; bake glyphs."""
    pad = mapping.setdefault("padding_store", {})
    base_code = int(pad.get("base_code", "E740"), 16)
    baked = []
    for i, ch in enumerate(chars):
        slot = start_slot + i
        code = base_code + slot
        off = pad3_file_offset(rom, slot)
        pixels = render_compact_glyph(ch, font_path)
        record = encode_compact_font_record(pixels)
        rom[off : off + COMPACT_FONT_RECORD_SIZE] = record
        mapping.setdefault("mapping", {})[ch] = {
            "code": f"{code:04X}",
            "reuse": False,
            "pool": "padding_store_pad3",
            "glyph_index": code - 0xDF20,
            "file_offset": off,
            "stock_glyph_untouched": True,
            "pad3_slot": slot - PAD12_SLOTS,
        }
        baked.append(ch)
    pad["count"] = start_slot + len(chars)
    pad["pad3_bank"] = f"{PAD3_BANK:02X}"
    pad["pad3_off"] = f"{PAD3_OFF:04X}"
    pad["pad3_al"] = f"{PAD3_BANK_AL:02X}"
    pad["pad12_slots"] = PAD12_SLOTS
    pad["pad_total_slots"] = pad["count"]
    mapping["glyph_formula"] = (
        f"codes E740+; marker E3DB; "
        f"slots0-95@40:F9F8; slots96-527@41:E4F4; "
        f"slots{PAD12_SLOTS}+@expand:{PAD3_BANK:02X}:{PAD3_OFF:04X}"
    )
    return {"baked": len(baked), "start_slot": start_slot, "chars": baked}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "-i",
        "--input",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_expanded_8mb.wsc",
        help="8MiB source (pre-promotion backup). Not the 16MB tip ROM.",
    )
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_expanded.wsc",
        help="Writes the tip ROM. Cold rebuild from _8mb replaces tip with pad3-only.",
    )
    ap.add_argument(
        "--map",
        type=Path,
        default=ROOT / "out" / "patch" / "hangul_char_map.json",
    )
    ap.add_argument(
        "--map-out",
        type=Path,
        default=ROOT / "out" / "patch" / "hangul_char_map_pad3.json",
    )
    ap.add_argument(
        "--font-source",
        type=Path,
        default=ROOT / "out" / "patch" / "monoeye_ko_expanded_8mb.wsc",
        help=(
            "8MiB/16MiB font-only source. Only known padding glyph ranges "
            "are copied; its script/data bytes are never used as the ROM base."
        ),
    )
    ap.add_argument("--mapped-slots", type=int, default=1027)
    ap.add_argument("--bake-overflow", action="store_true", default=True)
    ap.add_argument("--no-bake-overflow", action="store_false", dest="bake_overflow")
    args = ap.parse_args()

    mapping = json.loads(args.map.read_text(encoding="utf-8"))
    base_code = int((mapping.get("padding_store") or {}).get("base_code", "E740"), 16)
    base_index = base_code - 0xDF20

    rom = load_rom(args.input)
    rom = expand_rom_to_16mb(rom)
    assert len(rom) == ROM_SIZE_16MB

    font_source = copy_font_padding_source(rom, args.font_source)

    mapped = args.mapped_slots
    mig = migrate_bank3f_to_pad3(rom, from_slot=PAD12_SLOTS, to_slot_excl=mapped)

    overflow_chars: List[str] = list(mapping.get("overflow_chars") or [])
    bake_info: dict | None = None
    sticky = mapped
    if args.bake_overflow and overflow_chars:
        font = mapping.get("font") or find_system_font()
        bake_info = bake_overflow_chars(
            rom,
            mapping,
            font_path=font,
            start_slot=mapped,
            chars=overflow_chars,
        )
        sticky = mapped + len(overflow_chars)

    hook = install_hooks(rom, base_index=base_index, sticky_count=sticky)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(rom)
    args.map_out.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    # Static probes
    probes = {}
    for slot in (528, 1026, sticky - 1 if sticky > mapped else 528):
        if slot < PAD12_SLOTS:
            continue
        off = pad3_file_offset(rom, slot)
        rec = bytes(rom[off : off + 4])
        probes[f"slot_{slot}"] = {"file": f"{off:06X}", "head": rec.hex()}

    report = {
        "output": str(args.output),
        "map_out": str(args.map_out),
        "font_source": font_source,
        "header": ws_header(rom),
        "migrate": mig,
        "bake_overflow": bake_info,
        "hook": hook,
        "sticky_count": sticky,
        "slot_map": {
            "pad1": "0-95 bank40",
            "pad2": "96-527 bank41 AL=C1",
            "pad3": f"528+ expand bank{PAD3_BANK:02X} AL={PAD3_BANK_AL:02X}",
        },
        "probes": probes,
    }
    rep_path = args.output.with_suffix(".pad3.json")
    rep_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
