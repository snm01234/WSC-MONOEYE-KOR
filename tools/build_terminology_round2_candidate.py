#!/usr/bin/env python3
"""Build the user-requested terminology round-2 candidate from current main TIP.

Requested global standardization:
  퍼널 -> 판넬
  응우엔/응우옌 사드/서드 라인포드 -> 구엔 서드 라인포드
  윌겜 -> 윌게임
  켈리 레즈너 -> 케리 레즈너
  캬라 순/캬라 슨 -> 캐라 슨

User runtime follow-up bundled into the same candidate:
  5D2A4F / 殺してしまうには惜しいけど……
  로 만들어 버리기에는 아깝지만…… -> 죽여 버리기엔 아깝지만……

The patch is dictionary-centric: reusable stock tokens are corrected first, then
all remaining ext3 dictionary phrases carrying forbidden variants are rewritten.
This preserves scenario/battle record bytes and NUL boundaries wherever the
visible text is reached through dictionary tokens.  Any surviving rendered
record hit is a hard failure instead of being patched heuristically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_gundam_terminology_standard import (
    dictionary_hits,
    entries as standard_entries,
    forbidden_index,
    rendered_record_hits,
    source_hits,
)
from monoeye_rom import BANK_SIZE, Tbl, load_rom, read_encoded_z, update_ws_checksum, write_le16
from normalize_ko_text import normalize_ko_text, try_encode_ko_text

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/terminology_round2_candidate.wsc"
OUT_SAVE = ROOT / "sram/terminology_round2_candidate.sav"
OUT_REPORT = ROOT / "out/patch/terminology_round2_candidate_report.json"
SRAM_MIRROR = ROOT / "sram/terminology_round2_candidate.sav"

EXPECTED_PARENT = "5c2d4620809274338bda6d46eb6229fa810e6a3ad9b1c58d41ccb5a503abd67f"
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768
HANGUL_MARKER = 0xEC8D
# Proven orphaned stock-dictionary storage in the current parent.  No current
# dictionary pointer/entry interval reaches this 25-byte range; it is residue
# from earlier repoints.  Two 9-byte canonical strings fit here without
# consuming any live stock slot.
STOCK_ORPHAN_START = 0xDFD58A
STOCK_ORPHAN_END = 0xDFD5A3
EXPECTED_STOCK_ORPHAN_HEX = "ec8de7b0e751e7bd00ec8de784e74e00ec8de772e743e7d600"

# These four stock terms are genuine reusable game terms/names.  0D93 is the
# generic Japanese キャラクター fragment that reused the old 캬라 token; making
# it fully Korean prevents the Chara-name correction from creating 캐라クター.
STOCK_CANONICAL = {
    0x0593: "캐라",
    0x05DF: "케리",
    0x0770: "구엔",
    0x0BD2: "판넬",
    0x0C85: "윌게임",
    0x0D93: "캐릭터",
}

# Runtime evidence from the user's Olba Frost battle screenshot proves that
# record 5D2A4F renders this private ext3 phrase.  The leading Japanese が seen
# in source inventories is structural/portrait-boundary noise; the actual
# translatable body is 殺してしまうには惜しいけど…….
SEMANTIC_EXT3_CORRECTIONS = {
    0x5865: (
        "로　만들어　버리기에는　아깝지만……",
        "죽여　버리기엔　아깝지만……",
    ),
}

REPLACEMENTS = (
    ("응우엔　사드　라인포드", "구엔　서드　라인포드"),
    ("응우엔 사드 라인포드", "구엔 서드 라인포드"),
    ("응우엔　서드　라인포드", "구엔　서드　라인포드"),
    ("응우엔 서드 라인포드", "구엔 서드 라인포드"),
    ("응우옌　사드　라인포드", "구엔　서드　라인포드"),
    ("응우옌 사드 라인포드", "구엔 서드 라인포드"),
    ("응우옌　서드　라인포드", "구엔　서드　라인포드"),
    ("응우옌 서드 라인포드", "구엔 서드 라인포드"),
    ("캬라・순", "캐라・슨"),
    ("캬라　순", "캐라　슨"),
    ("캬라 순", "캐라 슨"),
    ("캬라・슨", "캐라・슨"),
    ("캬라　슨", "캐라　슨"),
    ("캬라 슨", "캐라 슨"),
    ("퍼널", "판넬"),
    ("윌겜", "윌게임"),
    ("켈리", "케리"),
    ("응우엔", "구엔"),
    ("응우옌", "구엔"),
    ("캬라", "캐라"),
)


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def strip_pad(text: str) -> str:
    return text.rstrip("\u3000 \t")


def canonical_text(text: str) -> str:
    out = text
    for old, new in REPLACEMENTS:
        out = out.replace(old, new)
    return out


def encode_plain(text: str, tbl: Tbl) -> bytes:
    encoded = try_encode_ko_text(
        normalize_ko_text(text),
        tbl,
        hangul_marker_code=HANGUL_MARKER,
        hangul_marker_mode="run",
    )
    if encoded is None:
        missing = sorted({ch for ch in text if "가" <= ch <= "힣" and ch not in tbl.char_to_code})
        raise BuildError(f"encode failed for {text!r}; missing={missing}")
    return encoded


def stock_tail_cursor(dictionary) -> int:
    cursor = 0
    for index in range(dictionary.stock_count):
        try:
            cursor = max(cursor, dictionary.entry_offset(index) + len(dictionary.raw_entry(index)) + 1)
        except Exception:
            continue
    return cursor


def ext3_bank_cursor(rom: bytes | bytearray, seg: int) -> int:
    base = seg * BANK_SIZE
    cursor = 0x2000
    for local in range(0x1000):
        ptr = int.from_bytes(rom[base + local * 2 : base + local * 2 + 2], "little")
        if not (0x2000 <= ptr < BANK_SIZE):
            continue
        try:
            raw, _term = read_encoded_z(rom, base + ptr, 512)
        except Exception:
            continue
        cursor = max(cursor, ptr + len(raw) + 1)
    return cursor


def patch_stock(rom: bytearray, tbl: Tbl, ext_meta: dict, ext3_meta: dict):
    report: list[dict[str, Any]] = []
    changed: list[tuple[int, int]] = []
    dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
    cursor = stock_tail_cursor(dictionary)
    bank_end = dictionary.base + BANK_SIZE
    orphan_cursor = STOCK_ORPHAN_START
    orphan_parent = bytes(rom[STOCK_ORPHAN_START:STOCK_ORPHAN_END])
    if orphan_parent.hex() != EXPECTED_STOCK_ORPHAN_HEX:
        raise BuildError("stock orphan storage identity drifted")
    # Prove the orphan range is outside every currently reachable dictionary
    # zstring and outside the pointer table before using it.
    for i in range(dictionary.count):
        try:
            a = dictionary.entry_abs(i)
            b = a + len(dictionary.raw_entry(i)) + 1
        except Exception:
            continue
        if max(a, STOCK_ORPHAN_START) < min(b, STOCK_ORPHAN_END):
            raise BuildError(f"stock orphan range overlaps live dictionary entry {i:04X}")
    ptr_a = dictionary.ptr_file
    ptr_b = dictionary.ptr_file + dictionary.count * 2
    if max(ptr_a, STOCK_ORPHAN_START) < min(ptr_b, STOCK_ORPHAN_END):
        raise BuildError("stock orphan range overlaps dictionary pointer table")

    for index, expected in STOCK_CANONICAL.items():
        dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
        before = strip_pad(dictionary.expand_index(index, tbl))
        raw = dictionary.raw_entry(index)
        encoded = encode_plain(expected, tbl)
        entry_abs = dictionary.entry_abs(index)
        if before == expected:
            report.append({"index": f"{index:04X}", "before": before, "after": expected, "mode": "already_canonical"})
            continue
        if len(encoded) <= len(raw):
            rom[entry_abs : entry_abs + len(encoded)] = encoded
            rom[entry_abs + len(encoded)] = 0
            changed.append((entry_abs, entry_abs + len(encoded) + 1))
            mode = "inplace"
        else:
            need = len(encoded) + 1
            dst = dictionary.base + cursor
            if dst + need <= bank_end and all(byte == 0xFF for byte in rom[dst : dst + need]):
                cursor += need
                mode = "tail_repoint"
            else:
                dst = orphan_cursor
                if dst + need > STOCK_ORPHAN_END:
                    raise BuildError(f"stock orphan storage exhausted at {index:04X}")
                orphan_cursor += need
                mode = "orphan_repoint"
            rom[dst : dst + len(encoded)] = encoded
            rom[dst + len(encoded)] = 0
            ptr_abs = dictionary.ptr_file + index * 2
            write_le16(rom, ptr_abs, dst - dictionary.base)
            changed.extend([(dst, dst + need), (ptr_abs, ptr_abs + 2)])
        after_dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
        after = strip_pad(after_dictionary.expand_index(index, tbl))
        if after != expected:
            raise BuildError(f"stock verify failed {index:04X}: {after!r} != {expected!r}")
        report.append({
            "index": f"{index:04X}", "before": before, "after": after, "mode": mode,
            "old_raw_len": len(raw), "new_raw_len": len(encoded),
        })
    return report, changed


def patch_ext3(rom: bytearray, tbl: Tbl, ext_meta: dict, ext3_meta: dict, bad_index):
    dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
    remaining = dictionary_hits(bytes(rom), tbl, dictionary, bad_index)
    stock_remaining = [row for row in remaining if int(row["index"], 16) < 0x1000]
    if stock_remaining:
        raise BuildError(f"forbidden stock terms remain after stock patch: {stock_remaining[:8]}")

    groups: defaultdict[int, list[int]] = defaultdict(list)
    for row in remaining:
        index = int(row["index"], 16)
        groups[dictionary.entry_abs(index)].append(index)
    for index in SEMANTIC_EXT3_CORRECTIONS:
        groups[dictionary.entry_abs(index)].append(index)
    for entry_abs in list(groups):
        groups[entry_abs] = sorted(set(groups[entry_abs]))

    report: list[dict[str, Any]] = []
    changed: list[tuple[int, int]] = []
    for entry_abs, indices in sorted(groups.items()):
        dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
        first = indices[0]
        before = strip_pad(dictionary.expand_index(first, tbl))
        semantic = [SEMANTIC_EXT3_CORRECTIONS[index] for index in indices if index in SEMANTIC_EXT3_CORRECTIONS]
        if semantic:
            if len(set(semantic)) != 1:
                raise BuildError(f"semantic alias disagreement at {entry_abs:07X}: {semantic!r}")
            expected_before, expected = semantic[0]
            if before != expected_before:
                raise BuildError(
                    f"semantic source drift at {first:05X}: {before!r} != {expected_before!r}"
                )
        else:
            expected = canonical_text(before)
            if expected == before:
                raise BuildError(f"no canonical rewrite for forbidden ext3 {first:05X}: {before!r}")
        for index in indices[1:]:
            alias_before = strip_pad(dictionary.expand_index(index, tbl))
            if alias_before != before:
                raise BuildError(f"physical alias disagreement at {entry_abs:07X}")
        encoded = encode_plain(expected, tbl)
        raw = dictionary.raw_entry(first)
        seg, _local = dictionary._ext3_bank_local(first)

        if len(encoded) <= len(raw):
            rom[entry_abs : entry_abs + len(encoded)] = encoded
            rom[entry_abs + len(encoded)] = 0
            changed.append((entry_abs, entry_abs + len(encoded) + 1))
            mode = "inplace"
        else:
            cursor = ext3_bank_cursor(rom, seg)
            need = len(encoded) + 1
            base = seg * BANK_SIZE
            if cursor + need > BANK_SIZE:
                raise BuildError(f"ext3 bank {seg:02X} overflow at {first:05X}")
            if any(byte != 0xFF for byte in rom[base + cursor : base + cursor + need]):
                raise BuildError(f"ext3 bank {seg:02X} tail not free at {cursor:04X}")
            rom[base + cursor : base + cursor + len(encoded)] = encoded
            rom[base + cursor + len(encoded)] = 0
            changed.append((base + cursor, base + cursor + need))
            for index in indices:
                physical_seg, local = dictionary._ext3_bank_local(index)
                if physical_seg != seg:
                    raise BuildError("cross-bank ext3 alias unsupported")
                ptr_abs = physical_seg * BANK_SIZE + dictionary.ext3_ptr_off + local * 2
                write_le16(rom, ptr_abs, cursor)
                changed.append((ptr_abs, ptr_abs + 2))
            mode = "append_repoint"

        after_dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
        for index in indices:
            after = strip_pad(after_dictionary.expand_index(index, tbl))
            if after != expected:
                raise BuildError(f"ext3 verify failed {index:05X}: {after!r} != {expected!r}")
        report.append({
            "indices": [f"{x:05X}" for x in indices],
            "entry_abs": f"{entry_abs:07X}",
            "physical_bank": f"{seg:02X}",
            "before": before,
            "after": expected,
            "mode": mode,
            "old_raw_len": len(raw),
            "new_raw_len": len(encoded),
        })
    return report, changed


def merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not intervals:
        return []
    out: list[list[int]] = []
    for start, end in sorted(intervals):
        if not out or start > out[-1][1]:
            out.append([start, end])
        else:
            out[-1][1] = max(out[-1][1], end)
    return [(a, b) for a, b in out]


def covered(offset: int, intervals: list[tuple[int, int]]) -> bool:
    return any(a <= offset < b for a, b in intervals)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--parent", type=Path, default=MAIN)
    ap.add_argument("--save", type=Path, default=MAIN_SAVE)
    ap.add_argument("--out-rom", type=Path, default=OUT_ROM)
    ap.add_argument("--out-save", type=Path, default=OUT_SAVE)
    ap.add_argument("--out-report", type=Path, default=OUT_REPORT)
    args = ap.parse_args()

    parent = bytes(load_rom(args.parent))
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_PARENT:
        raise BuildError(f"main identity drifted: {sha(parent)}")
    if not args.save.is_file() or args.save.stat().st_size != SAVE_SIZE:
        raise BuildError("current main SaveRAM missing or wrong size")

    tbl = Tbl.load(TBL_PATH)
    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    bad_index = forbidden_index(standard_entries())
    pre_sources = source_hits(bad_index)
    if pre_sources:
        raise BuildError(f"active canonical source terminology residuals remain: {pre_sources[:8]}")

    before_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    before_dict_hits = dictionary_hits(parent, tbl, before_dictionary, bad_index)
    before_record_hits = rendered_record_hits(parent, tbl, before_dictionary, bad_index)
    if not before_dict_hits:
        raise BuildError("baseline unexpectedly has no terminology hits")

    rom = bytearray(parent)
    stock_report, stock_changed = patch_stock(rom, tbl, ext_meta, ext3_meta)
    ext3_report, ext3_changed = patch_ext3(rom, tbl, ext_meta, ext3_meta, bad_index)

    after_dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
    after_dict_hits = dictionary_hits(bytes(rom), tbl, after_dictionary, bad_index)
    after_record_hits = rendered_record_hits(bytes(rom), tbl, after_dictionary, bad_index)
    if after_dict_hits or after_record_hits:
        raise BuildError(
            f"terminology residuals remain dict={len(after_dict_hits)} records={len(after_record_hits)} "
            f"sample={after_dict_hits[:3] or after_record_hits[:3]}"
        )

    checksum = update_ws_checksum(rom)
    allowed = stock_changed + ext3_changed + [(ROM_SIZE - 2, ROM_SIZE)]
    intervals = merge_intervals(allowed)
    unexpected = [
        i for i, (a, b) in enumerate(zip(parent, rom))
        if a != b and not covered(i, intervals)
    ]
    if unexpected:
        raise BuildError(f"unexpected diff outside dictionary/checksum ranges: {unexpected[:20]}")

    args.out_rom.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out_rom.with_name(f".{args.out_rom.name}.{os.getpid()}.tmp")
    tmp.write_bytes(rom)
    os.replace(tmp, args.out_rom)
    shutil.copyfile(args.save, args.out_save)
    SRAM_MIRROR.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.save, SRAM_MIRROR)

    report = {
        "schema_version": 1,
        "generated_by": "tools/build_terminology_round2_candidate.py",
        "ok": True,
        "parent": {"path": str(args.parent.relative_to(ROOT)), "sha256": sha(parent), "size": len(parent)},
        "candidate": {"path": str(args.out_rom.relative_to(ROOT)), "sha256": sha(rom), "size": len(rom), "ws_checksum": f"{checksum:04X}"},
        "candidate_save": {"path": str(args.out_save.relative_to(ROOT)), "sha256": sha(args.out_save.read_bytes()), "size": args.out_save.stat().st_size},
        "source_policy": {"active_source_hits": 0},
        "counts": {
            "before_dictionary_hits": len(before_dict_hits),
            "before_rendered_record_hits": len(before_record_hits),
            "stock_entries_patched": sum(r["mode"] != "already_canonical" for r in stock_report),
            "ext3_physical_phrases_patched": len(ext3_report),
            "ext3_logical_indices_patched": sum(len(r["indices"]) for r in ext3_report),
            "semantic_runtime_corrections": sum(
                any(int(index, 16) in SEMANTIC_EXT3_CORRECTIONS for index in r["indices"])
                for r in ext3_report
            ),
            "after_dictionary_hits": len(after_dict_hits),
            "after_rendered_record_hits": len(after_record_hits),
            "unexpected_diff_offsets": 0,
        },
        "stock": stock_report,
        "ext3": ext3_report,
        "allowed_intervals": [[a, b] for a, b in intervals],
    }
    args.out_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print(json.dumps(report["candidate"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
