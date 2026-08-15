#!/usr/bin/env python3
"""Read-only static probes for the 2026-08-15 terrain/dialogue/type report.

This deliberately compares the stock-mapped half of the expanded TIP with the
pristine 8 MiB ROM.  It does not launch an emulator and never writes a ROM.
"""
from __future__ import annotations

import sys
from pathlib import Path

from capstone import CS_ARCH_X86, CS_MODE_16, Cs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from expand_dictionary import _walk_zstring_range  # noqa: E402
from monoeye_rom import Dictionary, Tbl, stock_base  # noqa: E402
from monoeye_rom import dict_index_from_ext3_token  # noqa: E402

ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"


def stock_view(data: bytes) -> bytes:
    base = stock_base(data)
    return data[base : base + 0x800000]


def diff_runs(a: bytes, b: bytes, lo: int, hi: int) -> list[tuple[int, bytes, bytes]]:
    rows: list[tuple[int, bytes, bytes]] = []
    start: int | None = None
    for pos in range(lo, hi):
        different = a[pos] != b[pos]
        if different and start is None:
            start = pos
        elif not different and start is not None:
            rows.append((start, a[start:pos], b[start:pos]))
            start = None
    if start is not None:
        rows.append((start, a[start:hi], b[start:hi]))
    return rows


def scan_resolved_far_pointers(rom: bytes, targets: set[int]) -> dict[int, list[int]]:
    """Find every unaligned off16:seg16 whose 20-bit address is a target."""
    out = {target: [] for target in targets}
    for pos in range(len(rom) - 3):
        offset = rom[pos] | (rom[pos + 1] << 8)
        segment = rom[pos + 2] | (rom[pos + 3] << 8)
        physical = ((segment << 4) + offset) & 0xFFFFF
        if physical in out:
            out[physical].append(pos)
    return out


def dump_disasm(rom: bytes, logical: int, before: int, after: int) -> None:
    md = Cs(CS_ARCH_X86, CS_MODE_16)
    md.skipdata = True
    start = logical - before
    bank = logical >> 16
    origin = start & 0xFFFF
    print(f"\nDISASM {bank:02X}:{origin:04X}..{(logical + after) & 0xFFFF:04X}")
    for insn in md.disasm(rom[start : logical + after], origin):
        marker = ">" if insn.address <= (logical & 0xFFFF) < insn.address + insn.size else " "
        print(
            f"{marker}{bank:02X}:{insn.address:04X} {insn.bytes.hex().upper():<18} "
            f"{insn.mnemonic:<8} {insn.op_str}"
        )


def main() -> int:
    original_whole = ORIGINAL.read_bytes()
    tip_whole = TIP.read_bytes()
    original = stock_view(original_whole)
    tip = stock_view(tip_whole)
    tbl = Tbl.load(TBL_PATH)
    d_original = Dictionary(original_whole)
    d_tip = make_dictionary_ext3(
        tip_whole, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )

    print("PROGRAM DIFFS")
    for bank in range(0x78, 0x80):
        rows = diff_runs(original, tip, bank << 16, (bank + 1) << 16)
        if not rows:
            continue
        print(f"bank {bank:02X}: runs={len(rows)} bytes={sum(len(a) for _, a, _ in rows)}")
        for start, old, new in rows[:80]:
            print(
                f"  {start:06X}+{len(old):04X} "
                f"{old[:32].hex().upper()} -> {new[:32].hex().upper()}"
            )

    print("\nTERRAIN-LABEL ZSTRINGS")
    addresses: list[int] = []
    for logical, raw, kind in _walk_zstring_range(
        tip_whole, 0x75B3C0, 0x75B4A0, region="name75", max_len=64
    ):
        text = d_tip.expand(raw, tbl)
        addresses.append(logical)
        print(f"  {logical:06X} {bytes(raw).hex().upper():<32} {kind:<8} {text!r}")

    # The runtime far addresses point into the 20-bit stock ROM mapping.  Bank
    # 75 logical Bxxx strings resolve to physical 5Bxxx.
    targets = {0x50000 | (logical & 0xFFFF) for logical in addresses}
    refs = scan_resolved_far_pointers(tip, targets)
    print("\nRESOLVED FAR POINTERS TO TERRAIN-LABEL RANGE")
    for logical in addresses:
        physical = 0x50000 | (logical & 0xFFFF)
        hits = refs[physical]
        if hits:
            print(
                f"  {logical:06X} phys={physical:05X}: "
                + " ".join(f"{p >> 16:02X}:{p & 0xFFFF:04X}" for p in hits)
            )

    print("\nORIGINAL/TIP TARGET RECORDS")
    for logical in (0x75B3CE, 0x75B457, 0x75B45D, 0x75E58C, 0x75E59A, 0x75BD77):
        def one(data: bytes, dictionary: Dictionary) -> tuple[str, str]:
            start = stock_base(data) + logical
            end = data.index(0, start, start + 64)
            raw = data[start:end]
            return raw.hex().upper(), dictionary.expand(raw, tbl)

        oh, ot = one(original_whole, d_original)
        th, tt = one(tip_whole, d_tip)
        print(f"  {logical:06X} orig={oh} {ot!r} | tip={th} {tt!r}")

    print("\nEXT3 SLOT STORAGE")
    for label, token in (
        ("abaoa_EC41", bytes.fromhex("E518EC41")),
        ("abaoa_B445", bytes.fromhex("E518B445")),
        ("hit_B4D8", bytes.fromhex("E518B4D8")),
    ):
        index = dict_index_from_ext3_token(*token)
        entry = d_tip.entry_abs(index)
        raw = d_tip.raw_entry(index)
        print(
            f"  {label}: index={index:05X} entry={entry:06X} "
            f"bank={entry >> 16:02X} off={entry & 0xFFFF:04X} "
            f"len={len(raw)} end={(entry + len(raw)) & 0xFFFF:04X} "
            f"raw={raw.hex().upper()} text={d_tip.expand(token, tbl)!r}"
        )

    print("\nDIALOGUE/TYPE TARGETS")
    for index in (0x00FD, 0x0044, 0x08A6, 0x0244):
        print(
            f"  dict {index:04X}: current_raw={d_tip.raw_entry(index).hex().upper()} "
            f"current={d_tip.expand_index(index, tbl)!r} "
            f"original_raw={d_original.raw_entry(index).hex().upper()} "
            f"original={d_original.expand_index(index, tbl)!r}"
        )
    for wanted_text in ("히", "트", "히트", "히트　", "병기", "무기", "데미지"):
        hits = [
            index
            for index in range(d_tip.stock_count)
            if d_tip.expand_index(index, tbl) == wanted_text
        ]
        print(f"  stock exact {wanted_text!r}: " + " ".join(f"{x:04X}" for x in hits))
    starts = [
        (index, d_tip.expand_index(index, tbl))
        for index in range(d_tip.stock_count)
        if d_tip.expand_index(index, tbl).startswith("히트")
    ]
    print("  stock starts '히트': " + " | ".join(f"{i:04X}:{s!r}" for i, s in starts))
    for logical, extent in (
        (0x6053BF, 16),
        (0x61E234, 16),
        (0x62663E, 8),
        (0x627FB5, 16),
        (0x672552, 16),
        (0x67E9F7, 10),
    ):
        print(
            f"  {logical:06X}: original={original[logical:logical + extent].hex().upper()} "
            f"current={tip[logical:logical + extent].hex().upper()}"
        )
    for logical, prefix_len in ((0x62663E, 3), (0x672552, 3)):
        end = tip.find(b"\x00", logical, logical + 128)
        raw = tip[logical:end]
        print(
            f"  render {logical:06X}: raw={raw.hex().upper()} "
            f"text={d_tip.expand(raw[prefix_len:], tbl)!r}"
        )

    print("\nTERRAIN RECORD TABLE 75:E720 (13-byte stride)")
    for index in range(64):
        logical = 0x75E720 + index * 13
        opos = logical
        tpos = logical
        old = original[opos : opos + 13]
        new = tip[tpos : tpos + 13]
        offset = old[0] | (old[1] << 8)
        segment = old[2] | (old[3] << 8)
        cpu = ((segment << 4) + offset) & 0xFFFFF
        target = 0x700000 + cpu
        try:
            end = original_whole.index(0, target, min(target + 64, len(original_whole)))
            source = original_whole[target:end]
            rendered = d_original.expand(source, tbl)
        except (ValueError, IndexError):
            rendered = "<unreadable>"
        changed = old != new
        print(
            f"  {index:02d} {logical:06X} ptr={offset:04X}:{segment:04X} "
            f"target={target:06X} stats={old[4:].hex().upper()} "
            f"changed={changed} text={rendered!r} new={new.hex().upper()}"
        )

    print("\nEXACT ORIGINAL PAYLOAD OCCURRENCES")
    for label, pattern in (
        ("space_token", bytes.fromhex("F08F00")),
        ("abaoaqu_z", bytes.fromhex("262AF6522AF47F00")),
        ("hit_correction_z", bytes.fromhex("E00852F17C00")),
        ("evade_correction_z", bytes.fromhex("F505F17C00")),
    ):
        hits: list[int] = []
        pos = original.find(pattern)
        while pos >= 0:
            hits.append(pos)
            pos = original.find(pattern, pos + 1)
        print(
            f"  {label}: {len(hits)} "
            + " ".join(f"{p >> 16:02X}:{p & 0xFFFF:04X}" for p in hits)
        )

    print("\nIMMEDIATE-POINTER CODE PATTERNS")
    for label, pattern in (
        ("mov_bx_0177", bytes.fromhex("BB7701")),
        ("mov_bx_017D", bytes.fromhex("BB7D01")),
        ("mov_ax_5B2E", bytes.fromhex("B82E5B")),
        ("push_B457_canonical", bytes.fromhex("BB7701B82E5B")),
        ("push_B45D_canonical", bytes.fromhex("BB7D01B82E5B")),
        ("space_far_immediate", bytes.fromhex("B8EE00BB2E5B")),
        ("mov_bx_B457", bytes.fromhex("BB57B4")),
        ("mov_bx_B45D", bytes.fromhex("BB5DB4")),
        ("mov_bx_B3CE", bytes.fromhex("BBCEB3")),
        ("mov_bx_E58C", bytes.fromhex("BB8CE5")),
        ("mov_bx_BD77", bytes.fromhex("BB77BD")),
        ("mov_bx_398E", bytes.fromhex("BB8E39")),
        ("mov_si_398E", bytes.fromhex("BE8E39")),
        ("imm_398E_any", bytes.fromhex("8E39")),
        ("far_4000_398E", bytes.fromhex("8E390040")),
        ("mov_axbx_4000_398E", bytes.fromhex("B88E39BB0040")),
        ("mov_bxax_398E_4000", bytes.fromhex("BB8E39B80040")),
        ("disp_398E", bytes.fromhex("8E39")),
        ("imm_EE4A", bytes.fromhex("4AEE")),
        ("imm_3B6A", bytes.fromhex("6A3B")),
        (
            "window_x6_y5_w16_h8",
            bytes.fromhex(
                "33C050B8080050B8100050B80038BB0000B90600BA05009A"
            ),
        ),
        ("call_string_lookup_B76B", bytes.fromhex("9A6BB70080")),
        ("call_farptr_7041_0000", bytes.fromhex("9A00004170")),
        ("push_h8_w16", bytes.fromhex("B8080050B8100050")),
        ("push_h16_w8", bytes.fromhex("B8100050B8080050")),
    ):
        hits: list[int] = []
        pos = tip.find(pattern, 0x700000)
        while pos >= 0:
            hits.append(pos)
            pos = tip.find(pattern, pos + 1)
        print(
            f"  {label}: {len(hits)} "
            + " ".join(f"{p >> 16:02X}:{p & 0xFFFF:04X}" for p in hits[:80])
        )

    print("\nWHOLE-STOCK TABLE-POINTER PATTERNS")
    for label, pattern in (
        ("far_4000_398E", bytes.fromhex("8E390040")),
        ("far_4000_390E", bytes.fromhex("0E390040")),
        ("far_5B2E_00EA", bytes.fromhex("EA002E5B")),
        ("far_5B2E_00E1", bytes.fromhex("E1002E5B")),
        ("far_5B2E_0131", bytes.fromhex("31012E5B")),
        ("far_5B2E_0177", bytes.fromhex("77012E5B")),
        ("far_5B2E_017D", bytes.fromhex("7D012E5B")),
        ("far_5B2E_32AC", bytes.fromhex("AC322E5B")),
        ("far_5B2E_0A97", bytes.fromhex("970A2E5B")),
        ("far_5B2E_3B6A", bytes.fromhex("6A3B2E5B")),
    ):
        hits: list[int] = []
        pos = tip.find(pattern)
        while pos >= 0:
            hits.append(pos)
            pos = tip.find(pattern, pos + 1)
        print(
            f"  {label}: {len(hits)} "
            + " ".join(f"{p >> 16:02X}:{p & 0xFFFF:04X}" for p in hits[:80])
        )

    for logical in (0x7A0521, 0x7A06CE, 0x7A0700, 0x7A0736, 0x7A07A0, 0x7A080D):
        dump_disasm(tip, logical, 16, 64)
    for logical, before, after in (
        (0x777BCD, 0, 1096),
        (0x71307E, 192, 384),
        (0x700410, 128, 512),
        (0x773E9C, 256, 512),
        (0x773EC0, 256, 512),
        (0x78B588, 384, 768),
        (0x78B76B, 192, 640),
        (0x78BB5F, 384, 768),
        (0x78BDD4, 384, 768),
        (0x779D76, 384, 768),
        (0x77A222, 384, 1024),
        (0x777EBC, 384, 1024),
        (0x7B0B9B, 384, 768),
        (0x7B7C72, 384, 768),
        (0x7BB536, 384, 768),
        (0x7E8A81, 384, 768),
        (0x7E8F55, 192, 1024),
        (0x78DE5A, 96, 224),
        (0x78DEB5, 48, 96),
        (0x78AA72, 96, 160),
        (0x78B313, 192, 384),
        (0x7B5D82, 256, 640),
        (0x7B7F3A, 256, 640),
        (0x7BB6AB, 256, 640),
        (0x7C0C26, 512, 1024),
        (0x7C0F91, 256, 768),
        (0x7C0FD8, 256, 768),
        (0x7C112C, 256, 768),
        (0x7EB25B, 256, 640),
        (0x7C9EF4, 96, 160),
        (0x7CA1C1, 192, 320),
        (0x7AB877, 384, 640),
        (0x7B0D9C, 256, 640),
    ):
        dump_disasm(tip, logical, before, after)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
