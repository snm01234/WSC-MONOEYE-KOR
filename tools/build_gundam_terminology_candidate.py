#!/usr/bin/env python3
"""Build a terminology-standardization candidate from the current main TIP.

Scope is intentionally narrow:
* patch five canonical stock-name dictionary entries first;
* reuse those stock tokens (plus the existing 몬시아 token) inside ext3 phrases;
* directly rewrite fixed-length unit names and the two Atomic Bazooka phrases;
* add only the two Hangul glyphs required by the requested spellings (잭, 믹);
* retarget the Hangul run marker with the project's established safety rules;
* never change scenario/name record lengths, prefixes, or NUL terminators. Their
  only direct byte change is the length-preserving marker substitution.

The current main TIP and SaveRAM are inputs only. Defaults write a separate
candidate and a copied SaveRAM snapshot; promotion is a separate user-approved
step.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from audit_gundam_terminology_standard import (  # noqa: E402
    dictionary_hits,
    entries as standard_entries,
    forbidden_index,
    record_addresses,
    rendered_record_hits,
)
from build_hangul_font import render_compact_glyph  # noqa: E402
from font_pipeline import find_system_font  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    COMPACT_FONT_RECORD_SIZE,
    Tbl,
    encode_compact_font_record,
    load_rom,
    read_encoded_z,
    read_encoded_z_safe,
    stock_base,
    token_from_dict_index,
    update_ws_checksum,
    write_le16,
)
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402
from patch_font_hangul_hook import (  # noqa: E402
    MAIN_CAVE,
    MAIN_CAVE_MAX,
    STORE_SITE,
    build_store_cave,
)
from patch_pad3_expansion import PAD12_SLOTS  # noqa: E402
from retarget_hangul_marker import (  # noqa: E402
    TEXT_BANKS,
    check_new_code,
    find_original_marker_sites,
    patch_compare_operand,
)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MAIN_SAVE = ROOT / "sram/monoeye_ko_expanded.sav"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT_ROM = ROOT / "out/patch/gundam_terminology_candidate.wsc"
OUT_SAVE = ROOT / "sram/gundam_terminology_candidate.sav"
OUT_TBL = ROOT / "out/patch/gundam_terminology_candidate.tbl"
OUT_MAP = ROOT / "out/patch/gundam_terminology_candidate_glyph_map.json"
OUT_REPORT = ROOT / "out/patch/gundam_terminology_candidate_report.json"
EXPECTED_MAIN = "be5cdb102a589faecd487780b99d3c30dd358e938e66cdb5aeb76ebcc8f4959c"
EXPECTED_MARKER_EXP_REPLACEMENTS = 99_620
EXPECTED_MARKER_STOCK_REPLACEMENTS = 3_332
ROM_SIZE = 16_777_216
SAVE_SIZE = 32_768

# Current runtime is sticky for exactly 1344 glyph slots: E740..EC7F. EC80 is
# the live Hangul-run marker, so extending the sticky range directly would turn
# the marker into a glyph. Move the marker to a code proven absent from original
# text, then reuse EC80/EC81 as the two required new glyph slots.
EXPECTED_CURRENT_MARKER = 0xEC80
CANDIDATE_MARKER = 0xEC8D
GLYPH_BASE_CODE = 0xE740
EXPECTED_STICKY_COUNT = 1344
CANDIDATE_STICKY_COUNT = 1346
NEW_GLYPH_CODES = {
    "잭": 0xEC80,
    "믹": 0xEC81,
}

# Fundamental names get canonical stock dictionary entries. This makes them
# reusable as compact 2-byte tokens inside long ext3 phrases.
STOCK_CANONICAL = {
    0x093B: "데라즈",
    0x0B82: "에규",
    0x0C82: "블랙스",
    0x0716: "올바",
    0x0B96: "크와트로",
}
TOKEN_REPLACEMENTS = (
    ("에기유", 0x0B82, "에규"),
    ("델라즈", 0x093B, "데라즈"),
    ("브렉스", 0x0C82, "블랙스"),
    ("블렉스", 0x0C82, "블랙스"),
    ("오르바", 0x0716, "올바"),
    ("콰트로", 0x0B96, "크와트로"),
    # Existing exact stock dictionary entry, already canonical in the main TIP.
    ("몬샤", 0x08D3, "몬시아"),
)
DIRECT_REPLACEMENTS = (
    ("하이자크", "하이잭"),
    ("시스크드", "시스쿠드"),
    ("사이사리스", "사이살리스"),
    ("갸프란", "갸프랑"),
    ("켐퍼", "캠퍼"),
    ("핵　바주카", "아토믹　바주카"),
    ("원자　바주카", "아토믹　바주카"),
    ("핵 바주카", "아토믹 바주카"),
    ("원자 바주카", "아토믹 바주카"),
)


class BuildError(RuntimeError):
    pass


def sha(data: bytes | bytearray) -> str:
    return hashlib.sha256(bytes(data)).hexdigest()


def strip_pad(text: str) -> str:
    return text.rstrip("　 \t")


def canonical_text(text: str) -> str:
    out = text
    for old, new in DIRECT_REPLACEMENTS:
        out = out.replace(old, new)
    for old, _index, new in TOKEN_REPLACEMENTS:
        out = out.replace(old, new)
    return out


def encode_plain(text: str, tbl: Tbl) -> bytes:
    if not text:
        return b""
    encoded = try_encode_ko_text(
        normalize_ko_text(text),
        tbl,
        hangul_marker_code=CANDIDATE_MARKER,
        hangul_marker_mode="run",
    )
    if encoded is None:
        missing = sorted({ch for ch in text if "가" <= ch <= "힣" and ch not in tbl.char_to_code})
        raise BuildError(f"encode failed for {text!r}; missing={missing}")
    return encoded


def _ambiguous_direct_atoms(raw: bytes, tbl: Tbl) -> list[dict[str, Any]]:
    """Return direct glyph atoms whose visible TBL character has multiple codes.

    Some fixed-width UI tiles intentionally decode to the same audit placeholder
    (currently ``█``) even though their raw codes select different graphics.  A
    normal text re-encode chooses only the first code and can therefore turn a
    left/right icon pair into two copies of the same half.  Dictionary tokens are
    skipped here because their identity is handled by the dictionary layer.
    """
    by_char: defaultdict[str, list[int]] = defaultdict(list)
    for code, ch in tbl.code_to_char.items():
        if ch:
            by_char[ch].append(code)
    ambiguous = {ch for ch, codes in by_char.items() if len(codes) > 1}

    atoms: list[dict[str, Any]] = []
    pos = 0
    while pos < len(raw):
        lead = raw[pos]
        if 0xF0 <= lead <= 0xFF:
            if pos + 1 >= len(raw):
                raise BuildError("truncated dictionary token while preserving ambiguous codes")
            pos += 2
            continue
        if 0xE0 <= lead <= 0xEF:
            if pos + 1 >= len(raw):
                raise BuildError("truncated two-byte glyph while preserving ambiguous codes")
            width = 2
            code = (lead << 8) | raw[pos + 1]
        else:
            width = 1
            code = lead
        ch = tbl.code_to_char.get(code)
        if ch in ambiguous:
            atoms.append({"char": ch, "code": code, "offset": pos, "width": width})
        pos += width
    return atoms


def preserve_ambiguous_direct_codes(
    source_raw: bytes, encoded: bytes, tbl: Tbl
) -> tuple[bytes, list[dict[str, Any]]]:
    """Keep raw identities for duplicate TBL mappings across a text rewrite."""
    source = _ambiguous_direct_atoms(source_raw, tbl)
    target = _ambiguous_direct_atoms(encoded, tbl)
    source_chars = [row["char"] for row in source]
    target_chars = [row["char"] for row in target]
    if source_chars != target_chars:
        raise BuildError(
            "ambiguous direct-glyph sequence changed during rewrite: "
            f"source={source_chars!r} target={target_chars!r}"
        )

    out = bytearray(encoded)
    report: list[dict[str, Any]] = []
    for src, dst in zip(source, target):
        if src["width"] != dst["width"]:
            raise BuildError(
                f"ambiguous glyph width changed for {src['char']!r}: "
                f"{src['width']} -> {dst['width']}"
            )
        if src["code"] == dst["code"]:
            continue
        width = int(dst["width"])
        offset = int(dst["offset"])
        out[offset : offset + width] = int(src["code"]).to_bytes(width, "big")
        report.append(
            {
                "char": src["char"],
                "source_code": f"{int(src['code']):04X}",
                "reencoded_code": f"{int(dst['code']):04X}",
                "encoded_offset": offset,
            }
        )
    return bytes(out), report


def encode_ext3_with_stock_tokens(text: str, tbl: Tbl) -> tuple[bytes, str, list[dict[str, Any]]]:
    """Encode a bad ext3 phrase while replacing bad person names by stock tokens."""
    work = text
    for old, new in DIRECT_REPLACEMENTS:
        work = work.replace(old, new)

    out = bytearray()
    pos = 0
    substitutions: list[dict[str, Any]] = []
    while pos < len(work):
        candidates = []
        for old, index, canonical in TOKEN_REPLACEMENTS:
            at = work.find(old, pos)
            if at >= 0:
                candidates.append((at, -len(old), old, index, canonical))
        if not candidates:
            out += encode_plain(work[pos:], tbl)
            break
        at, _neg_len, old, index, canonical = min(candidates)
        if at > pos:
            out += encode_plain(work[pos:at], tbl)
        out += token_from_dict_index(index)
        substitutions.append(
            {
                "old": old,
                "canonical": canonical,
                "stock_index": f"{index:04X}",
                "token": token_from_dict_index(index).hex().upper(),
            }
        )
        pos = at + len(old)

    expected = canonical_text(text)
    return bytes(out), expected, substitutions


def build_candidate_tbl(base: Tbl) -> Tbl:
    if marker_code() != EXPECTED_CURRENT_MARKER:
        raise BuildError(
            f"installed marker drifted: {marker_code():04X} != {EXPECTED_CURRENT_MARKER:04X}"
        )
    if base.code_to_char.get(EXPECTED_CURRENT_MARKER, "") not in ("", None):
        raise BuildError("current marker code unexpectedly maps to a visible glyph")
    for ch in NEW_GLYPH_CODES:
        if ch in base.char_to_code:
            raise BuildError(f"new canonical glyph already exists unexpectedly: {ch}")
    for ch, code in NEW_GLYPH_CODES.items():
        if code != EXPECTED_CURRENT_MARKER and code in base.code_to_char:
            raise BuildError(
                f"new glyph slot {code:04X} already belongs to {base.code_to_char[code]!r}"
            )
    if CANDIDATE_MARKER in base.code_to_char:
        raise BuildError(
            f"candidate marker {CANDIDATE_MARKER:04X} already exists in TBL"
        )

    codes = dict(base.code_to_char)
    codes[EXPECTED_CURRENT_MARKER] = "잭"
    codes[0xEC81] = "믹"
    codes[CANDIDATE_MARKER] = ""
    char_to_code: dict[str, int] = {}
    for code, ch in codes.items():
        if ch and ch not in char_to_code:
            char_to_code[ch] = code
    return Tbl(codes, char_to_code)


def candidate_tbl_text(tbl: Tbl) -> str:
    lines = [
        "# Mono-Eye Gundam terminology candidate TBL",
        f"# Hangul marker moved {EXPECTED_CURRENT_MARKER:04X}->{CANDIDATE_MARKER:04X}; "
        "EC80/EC81 are now visible glyphs.",
    ]
    for code, ch in sorted(tbl.code_to_char.items()):
        lines.append(f"{code:02X}={ch}" if code <= 0xFF else f"{code:04X}={ch}")
    return "\n".join(lines) + "\n"


def retarget_marker_candidate(
    rom: bytearray,
    *,
    original: bytes,
) -> dict[str, Any]:
    if marker_code() != EXPECTED_CURRENT_MARKER:
        raise BuildError("current marker drifted before candidate build")
    problems = check_new_code(original, CANDIDATE_MARKER)
    if problems:
        raise BuildError(f"candidate marker is unsafe: {problems}")
    if CANDIDATE_MARKER < GLYPH_BASE_CODE + CANDIDATE_STICKY_COUNT:
        raise BuildError("candidate marker overlaps the extended sticky glyph range")

    old_b = EXPECTED_CURRENT_MARKER.to_bytes(2, "big")
    new_b = CANDIDATE_MARKER.to_bytes(2, "big")
    original_sites = find_original_marker_sites(original, EXPECTED_CURRENT_MARKER)
    sb = stock_base(rom)
    original_sb = stock_base(original)
    protected: list[dict[str, Any]] = []
    for logical in original_sites:
        current = bytes(rom[sb + logical : sb + logical + 2])
        expected = bytes(original[original_sb + logical : original_sb + logical + 2])
        if current != expected or current != old_b:
            raise BuildError(f"protected original marker site drifted at {logical:06X}")
        protected.append({"logical": f"{logical:06X}", "bytes": current.hex().upper()})

    expansion_replacements = 0
    cursor = rom.find(old_b, 0, 0x800000)
    while cursor >= 0:
        rom[cursor : cursor + 2] = new_b
        expansion_replacements += 1
        cursor = rom.find(old_b, cursor + 2, 0x800000)

    skip = set(original_sites)
    stock_replacements = 0
    stock_sites: list[str] = []
    for seg in TEXT_BANKS:
        start = sb + seg * BANK_SIZE
        end = start + BANK_SIZE
        cursor = rom.find(old_b, start, end)
        while cursor >= 0:
            logical = cursor - sb
            if logical not in skip:
                rom[cursor : cursor + 2] = new_b
                stock_replacements += 1
                stock_sites.append(f"{logical:06X}")
                cursor = rom.find(old_b, cursor + 2, end)
            else:
                cursor = rom.find(old_b, cursor + 1, end)

    compare_sites = patch_compare_operand(
        rom, EXPECTED_CURRENT_MARKER, CANDIDATE_MARKER
    )
    if [row.get("site") for row in compare_sites] != ["7A:FFBA"]:
        raise BuildError(f"unexpected marker compare sites: {compare_sites}")

    if expansion_replacements != EXPECTED_MARKER_EXP_REPLACEMENTS:
        raise BuildError(
            f"marker expansion population drifted: {expansion_replacements} != "
            f"{EXPECTED_MARKER_EXP_REPLACEMENTS}"
        )
    if stock_replacements != EXPECTED_MARKER_STOCK_REPLACEMENTS:
        raise BuildError(
            f"marker stock-text population drifted: {stock_replacements} != "
            f"{EXPECTED_MARKER_STOCK_REPLACEMENTS}"
        )
    return {
        "old": f"{EXPECTED_CURRENT_MARKER:04X}",
        "new": f"{CANDIDATE_MARKER:04X}",
        "original_collision_count": len(original_sites),
        "protected_original_sites": protected,
        "expansion_replacements": expansion_replacements,
        "stock_text_replacements": stock_replacements,
        "compare_sites": compare_sites,
        "stock_site_sample": stock_sites[:20],
    }


def locate_store_cave(rom: bytes | bytearray) -> int:
    sb = stock_base(rom)
    site = sb + STORE_SITE
    if rom[site] != 0xE8:
        raise BuildError("glyph store site is not the installed near-call hook")
    rel = struct.unpack_from("<H", rom, site + 1)[0]
    store_ip = (STORE_SITE + 3 + rel) & 0xFFFF
    return sb + ((STORE_SITE & 0xFF0000) | store_ip)


def extend_sticky_window(rom: bytearray) -> dict[str, Any]:
    base_index = GLYPH_BASE_CODE - 0xDF20
    store_abs = locate_store_cave(rom)
    before = build_store_cave(base_index, EXPECTED_STICKY_COUNT)
    after = build_store_cave(base_index, CANDIDATE_STICKY_COUNT)
    if len(before) != len(after):
        raise BuildError("sticky store cave length changed unexpectedly")
    if bytes(rom[store_abs : store_abs + len(before)]) != before:
        raise BuildError("installed sticky store cave is not the expected 1344-slot build")
    sb = stock_base(rom)
    cave_end = sb + MAIN_CAVE + MAIN_CAVE_MAX
    if store_abs + len(after) > cave_end:
        raise BuildError("extended sticky store cave exceeds its owned code cave")
    rom[store_abs : store_abs + len(after)] = after
    return {
        "store_abs": f"{store_abs:07X}",
        "base_index": f"{base_index:04X}",
        "before_count": EXPECTED_STICKY_COUNT,
        "after_count": CANDIDATE_STICKY_COUNT,
        "cave_len": len(after),
    }


def bake_candidate_glyphs(rom: bytearray) -> list[dict[str, Any]]:
    font_path = find_system_font()
    rows: list[dict[str, Any]] = []
    for ch, code in NEW_GLYPH_CODES.items():
        slot = code - GLYPH_BASE_CODE
        if len(rom) != ROM_SIZE or slot < PAD12_SLOTS:
            raise BuildError(f"invalid pad3 slot for {ch}: {slot}")
        # pad3 lives in expansion bank 00 at offset 0000. Calculate directly so
        # loading the 8MiB original for marker proof cannot poison monoeye_rom's
        # process-global current-size helper.
        off = (slot - PAD12_SLOTS) * COMPACT_FONT_RECORD_SIZE
        record = encode_compact_font_record(render_compact_glyph(ch, font_path))
        if len(record) != COMPACT_FONT_RECORD_SIZE:
            raise BuildError(f"unexpected compact glyph size for {ch}")
        rom[off : off + COMPACT_FONT_RECORD_SIZE] = record
        rows.append(
            {
                "char": ch,
                "code": f"{code:04X}",
                "slot": slot,
                "file_offset": f"{off:07X}",
                "font": font_path,
            }
        )
    return rows


def stock_tail_cursor(dictionary) -> int:
    cursor = 0
    for index in range(dictionary.stock_count):
        try:
            cursor = max(
                cursor,
                dictionary.entry_offset(index) + len(dictionary.raw_entry(index)) + 1,
            )
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


def patch_stock(rom: bytearray, tbl: Tbl, ext_meta: dict, ext3_meta: dict) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    report: list[dict[str, Any]] = []
    changed_ranges: list[tuple[int, int]] = []
    dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
    cursor = stock_tail_cursor(dictionary)
    bank_end = dictionary.base + BANK_SIZE

    for index, expected in STOCK_CANONICAL.items():
        dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
        before = strip_pad(dictionary.expand_index(index, tbl))
        if before == expected:
            report.append({"index": f"{index:04X}", "before": before, "after": expected, "mode": "already_canonical"})
            continue
        raw = dictionary.raw_entry(index)
        encoded = encode_plain(expected, tbl)
        entry_abs = dictionary.entry_abs(index)
        if len(encoded) <= len(raw):
            rom[entry_abs : entry_abs + len(encoded)] = encoded
            rom[entry_abs + len(encoded)] = 0
            changed_ranges.append((entry_abs, entry_abs + len(encoded) + 1))
            mode = "inplace"
        else:
            need = len(encoded) + 1
            dst = dictionary.base + cursor
            if dst + need > bank_end:
                raise BuildError(f"stock dictionary tail overflow at {index:04X}")
            if any(byte != 0xFF for byte in rom[dst : dst + need]):
                raise BuildError(f"stock dictionary tail is not free at {cursor:04X}")
            rom[dst : dst + len(encoded)] = encoded
            rom[dst + len(encoded)] = 0
            write_le16(rom, dictionary.ptr_file + index * 2, cursor)
            changed_ranges.extend(
                [
                    (dst, dst + need),
                    (dictionary.ptr_file + index * 2, dictionary.ptr_file + index * 2 + 2),
                ]
            )
            mode = "tail_repoint"
            cursor += need
        after_dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
        after = strip_pad(after_dictionary.expand_index(index, tbl))
        if after != expected:
            raise BuildError(f"stock verify failed {index:04X}: {after!r} != {expected!r}")
        report.append(
            {
                "index": f"{index:04X}",
                "before": before,
                "after": after,
                "mode": mode,
                "old_raw_len": len(raw),
                "new_raw_len": len(encoded),
            }
        )
    return report, changed_ranges


def patch_ext3(rom: bytearray, tbl: Tbl, ext_meta: dict, ext3_meta: dict, bad_index) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
    dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
    remaining = dictionary_hits(bytes(rom), tbl, dictionary, bad_index)
    target_indices = sorted(
        {
            int(row["index"], 16)
            for row in remaining
            if int(row["index"], 16) >= 0x1000
        }
    )
    groups: defaultdict[int, list[int]] = defaultdict(list)
    for index in target_indices:
        groups[dictionary.entry_abs(index)].append(index)

    report: list[dict[str, Any]] = []
    changed_ranges: list[tuple[int, int]] = []
    for entry_abs, indices in sorted(groups.items()):
        dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
        first = indices[0]
        before = strip_pad(dictionary.expand_index(first, tbl))
        encoded, expected, substitutions = encode_ext3_with_stock_tokens(before, tbl)
        raw = dictionary.raw_entry(first)
        encoded, ambiguous_preserved = preserve_ambiguous_direct_codes(raw, encoded, tbl)
        seg, _local = dictionary._ext3_bank_local(first)

        # All aliases of the same physical phrase must want the same render.
        for index in indices[1:]:
            alias_before = strip_pad(dictionary.expand_index(index, tbl))
            if alias_before != before or canonical_text(alias_before) != expected:
                raise BuildError(f"physical alias disagreement at {entry_abs:07X}")

        if len(encoded) <= len(raw):
            rom[entry_abs : entry_abs + len(encoded)] = encoded
            rom[entry_abs + len(encoded)] = 0
            changed_ranges.append((entry_abs, entry_abs + len(encoded) + 1))
            mode = "inplace_tokenized" if substitutions else "inplace"
        else:
            cursor = ext3_bank_cursor(rom, seg)
            need = len(encoded) + 1
            base = seg * BANK_SIZE
            if cursor + need > BANK_SIZE:
                raise BuildError(
                    f"ext3 bank {seg:02X} overflow for {first:05X}: "
                    f"cursor={cursor:04X} need={need}"
                )
            if any(byte != 0xFF for byte in rom[base + cursor : base + cursor + need]):
                raise BuildError(f"ext3 bank {seg:02X} tail is not free at {cursor:04X}")
            rom[base + cursor : base + cursor + len(encoded)] = encoded
            rom[base + cursor + len(encoded)] = 0
            changed_ranges.append((base + cursor, base + cursor + need))
            for index in indices:
                physical_seg, local = dictionary._ext3_bank_local(index)
                if physical_seg != seg:
                    raise BuildError("cross-bank physical alias is unsupported")
                ptr_abs = physical_seg * BANK_SIZE + dictionary.ext3_ptr_off + local * 2
                write_le16(rom, ptr_abs, cursor)
                changed_ranges.append((ptr_abs, ptr_abs + 2))
            mode = "append_repoint"

        after_dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
        for index in indices:
            after = strip_pad(after_dictionary.expand_index(index, tbl))
            if after != expected:
                raise BuildError(
                    f"ext3 verify failed {index:05X}: {after!r} != {expected!r}"
                )
        report.append(
            {
                "indices": [f"{index:05X}" for index in indices],
                "entry_abs": f"{entry_abs:07X}",
                "physical_bank": f"{seg:02X}",
                "before": before,
                "after": expected,
                "mode": mode,
                "old_raw_len": len(raw),
                "new_raw_len": len(encoded),
                "stock_token_substitutions": substitutions,
                "ambiguous_code_preservations": ambiguous_preserved,
            }
        )

    ambiguous_groups = sum(bool(row["ambiguous_code_preservations"]) for row in report)
    ambiguous_codes = sum(len(row["ambiguous_code_preservations"]) for row in report)
    if ambiguous_groups != 8 or ambiguous_codes != 9:
        raise BuildError(
            "ambiguous raw-code preservation population drifted: "
            f"groups={ambiguous_groups} codes={ambiguous_codes}, expected 8/9"
        )
    return report, changed_ranges


def record_byte_snapshot(rom: bytes | bytearray) -> dict[int, bytes]:
    sb = stock_base(rom)
    out: dict[int, bytes] = {}
    for logical in sorted(record_addresses()):
        got = read_encoded_z_safe(rom, sb + logical, max_len=512)
        if got is None:
            continue
        payload, term = got
        out[logical] = bytes(payload) + bytes([rom[term]])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--parent", type=Path, default=MAIN)
    ap.add_argument("--save", type=Path, default=MAIN_SAVE)
    ap.add_argument("--out-rom", type=Path, default=OUT_ROM)
    ap.add_argument("--out-save", type=Path, default=OUT_SAVE)
    ap.add_argument("--out-tbl", type=Path, default=OUT_TBL)
    ap.add_argument("--out-map", type=Path, default=OUT_MAP)
    ap.add_argument("--out-report", type=Path, default=OUT_REPORT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    parent = bytes(load_rom(args.parent))
    if len(parent) != ROM_SIZE or sha(parent) != EXPECTED_MAIN:
        raise BuildError(f"current main TIP identity drifted: {sha(parent)}")
    if not args.save.is_file() or args.save.stat().st_size != SAVE_SIZE:
        raise BuildError("current main SaveRAM missing or wrong size")

    if not ORIGINAL.is_file():
        raise BuildError(f"missing original ROM: {ORIGINAL}")
    original = bytes(load_rom(ORIGINAL))
    rom = bytearray(parent)
    base_tbl = Tbl.load(TBL_PATH)
    candidate_tbl = build_candidate_tbl(base_tbl)

    ext_meta = load_ext_meta(EXT_META)
    ext3_meta = load_ext_meta(EXT3_META)
    bad_index = forbidden_index(standard_entries())
    before_dictionary = make_dictionary_ext3(parent, ext_meta, ext3_meta)
    before_dict_hits = dictionary_hits(parent, base_tbl, before_dictionary, bad_index)
    before_record_hits = rendered_record_hits(parent, base_tbl, before_dictionary, bad_index)
    record_before = record_byte_snapshot(parent)

    marker_report = retarget_marker_candidate(rom, original=original)
    sticky_report = extend_sticky_window(rom)
    glyph_info = bake_candidate_glyphs(rom)

    # Prove the requested new spellings are all encodable under the candidate
    # marker/TBL before touching terminology dictionaries.
    requested = [row["canonical_ko"] for row in standard_entries()]
    encode_fail = []
    for text in requested:
        if try_encode_ko_text(
            normalize_ko_text(text),
            candidate_tbl,
            hangul_marker_code=CANDIDATE_MARKER,
            hangul_marker_mode="run",
        ) is None:
            encode_fail.append(text)
    if encode_fail:
        raise BuildError(f"canonical terminology still unencodable: {encode_fail}")

    stock_report, stock_ranges = patch_stock(
        rom, candidate_tbl, ext_meta, ext3_meta
    )
    ext3_report, ext3_ranges = patch_ext3(
        rom, candidate_tbl, ext_meta, ext3_meta, bad_index
    )

    candidate_dictionary = make_dictionary_ext3(rom, ext_meta, ext3_meta)
    after_dict_hits = dictionary_hits(
        bytes(rom), candidate_tbl, candidate_dictionary, bad_index
    )
    after_record_hits = rendered_record_hits(
        bytes(rom), candidate_tbl, candidate_dictionary, bad_index
    )
    if after_dict_hits or after_record_hits:
        raise BuildError(
            f"terminology residuals remain: dict={len(after_dict_hits)} "
            f"records={len(after_record_hits)}"
        )

    # Scenario/name records are not repacked. The only direct record-byte change
    # allowed is the 2-byte EC80->EC8D marker substitution in text banks.
    record_after = record_byte_snapshot(rom)
    if set(record_before) != set(record_after):
        raise BuildError("record inventory changed after terminology candidate")
    old_marker = EXPECTED_CURRENT_MARKER.to_bytes(2, "big")
    new_marker = CANDIDATE_MARKER.to_bytes(2, "big")
    marker_changed_records = 0
    for logical, before_raw in record_before.items():
        expected = before_raw
        if (logical >> 16) in TEXT_BANKS:
            expected = expected.replace(old_marker, new_marker)
        after_raw = record_after[logical]
        if len(after_raw) != len(before_raw):
            raise BuildError(f"record length changed at {logical:06X}")
        if after_raw != expected:
            raise BuildError(f"record bytes changed beyond marker retarget at {logical:06X}")
        if after_raw != before_raw:
            marker_changed_records += 1

    # Marker/sticky changes must not disturb the ext3 runtime cave/sites.
    sb = stock_base(parent)
    ext3_guards = (
        (sb + 0x7FFD10, 314, "ext3_cave"),
        (sb + 0x7A0736, 5, "ext3_site1"),
        (sb + 0x7A080D, 5, "ext3_site2"),
        (sb + 0x7A06CE, 6, "ext3_leaf"),
    )
    for offset, length, label in ext3_guards:
        if parent[offset : offset + length] != bytes(rom[offset : offset + length]):
            raise BuildError(f"{label} changed during marker/glyph extension")

    checksum = update_ws_checksum(rom)
    glyph_map = {
        "marker": marker_report,
        "sticky": sticky_report,
        "glyphs": glyph_info,
        "tbl": {ch: f"{code:04X}" for ch, code in NEW_GLYPH_CODES.items()},
    }
    report = {
        "status": "candidate_ready",
        "parent": {"path": str(args.parent), "sha256": sha(parent), "size": len(parent)},
        "candidate": {"sha256": sha(rom), "size": len(rom), "checksum": f"{checksum:04X}"},
        "save": {"source": str(args.save), "size": args.save.stat().st_size, "copied_unchanged": True},
        "standard": "data/gundam_terminology_standard_ko.json",
        "marker": marker_report,
        "glyphs": glyph_map,
        "stock_dictionary": stock_report,
        "ext3": {
            "physical_groups": len(ext3_report),
            "logical_indices": sum(len(row["indices"]) for row in ext3_report),
            "inplace_groups": sum(row["mode"].startswith("inplace") for row in ext3_report),
            "append_repoint_groups": sum(row["mode"] == "append_repoint" for row in ext3_report),
            "rows": ext3_report,
        },
        "audit": {
            "before_dictionary_hits": len(before_dict_hits),
            "before_rendered_record_hits": len(before_record_hits),
            "after_dictionary_hits": len(after_dict_hits),
            "after_rendered_record_hits": len(after_record_hits),
            "record_marker_only_changes": marker_changed_records,
            "record_lengths_and_terminators_unchanged": True,
            "ext3_runtime_guards_unchanged": True,
            "canonical_encode_fail": encode_fail,
        },
        "dictionary_changed_range_count": len(stock_ranges) + len(ext3_ranges),
        "promotion": "not_performed; emulator/runtime validation required before main TIP promotion",
    }

    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    for path in (args.out_rom, args.out_save, args.out_tbl, args.out_map, args.out_report):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.out_rom.write_bytes(rom)
    shutil.copyfile(args.save, args.out_save)
    args.out_tbl.write_text(candidate_tbl_text(candidate_tbl), encoding="utf-8")
    args.out_map.write_text(json.dumps(glyph_map, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.out_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "candidate_sha256": report["candidate"]["sha256"],
        "before_dictionary_hits": report["audit"]["before_dictionary_hits"],
        "after_dictionary_hits": report["audit"]["after_dictionary_hits"],
        "before_rendered_record_hits": report["audit"]["before_rendered_record_hits"],
        "after_rendered_record_hits": report["audit"]["after_rendered_record_hits"],
        "stock_dictionary": len(stock_report),
        "ext3_groups": len(ext3_report),
        "ext3_append": report["ext3"]["append_repoint_groups"],
        "glyph_codes": glyph_map["tbl"],
        "marker": f"{EXPECTED_CURRENT_MARKER:04X}->{CANDIDATE_MARKER:04X}",
        "out_rom": str(args.out_rom),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
