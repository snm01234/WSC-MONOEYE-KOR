#!/usr/bin/env python3
"""
Apply seed Korean translations onto a ROM that already has Hangul glyphs.

Strategy:
  1) Encode each KO line with hangul_patch_pad3.tbl (no dictionary compression).
  2) Recycle the last N dictionary slots (F0–FE only allows indices ≤ 0xEFF).
  3) Store KO bytes in the free region after the pointer table (0x99BA+).
  4) Rewrite each dialogue body to a 2-byte dict token; keep record size.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract_script import split_prefix_body  # noqa: E402
from translation_source_policy import assert_translation_source_allowed  # noqa: E402
from monoeye_rom import (  # noqa: E402
    BANK_SIZE,
    DICT_PTR_START,
    Dictionary,
    SEG_DICT,
    Tbl,
    dict_index_from_token,
    encode_plaintext,
    find_rom,
    is_dict_token,
    is_kanji_lead,
    load_rom,
    patch_bank,
    read_encoded_z,
    slice_bank,
    token_from_dict_index,
    update_ws_checksum,
)


def read_record_at(rom: bytearray, abs_off: int) -> bytes:
    payload, _ = read_encoded_z(rom, abs_off)
    return payload


def encode_marked_plaintext(text: str, tbl: Tbl, marker_code: int | None) -> bytes:
    """Prefix each Hangul syllable with the runtime-only provenance marker."""
    out = bytearray()
    marker = (
        bytes([(marker_code >> 8) & 0xFF, marker_code & 0xFF])
        if marker_code is not None
        else b""
    )
    for ch in text:
        if marker and "가" <= ch <= "힣":
            out.extend(marker)
        out.extend(tbl.encode_char(ch))
    return bytes(out)


def find_unused_dictionary_slots(rom: bytearray, dictionary: Dictionary) -> List[int]:
    """
    Dictionary entries with no external consumers.

    Uses referenced_dict_closure (script + name75 + nested) so unit/weapon
    label tokens are never treated as free / opening-dedicated.
    """
    from expand_dictionary import referenced_dict_closure  # noqa: WPS433

    keep = referenced_dict_closure(rom, dictionary)
    return [index for index in range(dictionary.count) if index not in keep]


def apply_translations(
    rom: bytearray,
    tbl: Tbl,
    lines: List[dict],
    *,
    marker_code: int | None = None,
) -> dict:
    d = Dictionary(rom)
    if len(lines) > d.count:
        raise RuntimeError("More translation lines than dictionary entries")

    bank5f = bytearray(slice_bank(rom, SEG_DICT))
    ptrs = list(d.ptrs)

    unused_slots = find_unused_dictionary_slots(rom, d)
    if len(lines) > len(unused_slots):
        raise RuntimeError(
            f"Need {len(lines)} dictionary slots but only {len(unused_slots)} are unused"
        )

    # Free space after stock pointer table (was 0xFF padding).
    phrase_cursor = 0x99BA

    results = []
    for n, line in enumerate(lines):
        abs_off = int(line["abs"], 16)
        ko = line["ko"].replace(" ", "　")
        encoded = (
            encode_marked_plaintext(ko, tbl, marker_code)
            if marker_code is not None
            else encode_plaintext(ko, tbl)
        )

        dict_index = unused_slots[n]
        if phrase_cursor + len(encoded) + 1 >= BANK_SIZE:
            raise RuntimeError("Dictionary bank overflow while writing KO phrases")
        bank5f[phrase_cursor : phrase_cursor + len(encoded)] = encoded
        bank5f[phrase_cursor + len(encoded)] = 0
        ptrs[dict_index] = phrase_cursor
        phrase_cursor += len(encoded) + 1

        token = token_from_dict_index(dict_index)

        original = read_record_at(rom, abs_off)
        prefix, body, _kind = split_prefix_body(original)
        if len(body) < 2:
            raise RuntimeError(
                f"@{line['abs']} body too short ({len(body)} bytes) for dict token"
            )

        new_payload = bytearray(prefix) + bytearray(token)
        pad = len(original) - len(new_payload)
        if pad < 0:
            raise RuntimeError(f"@{line['abs']} prefix+token exceeds original record")
        # Space fill — NUL pad makes the next sequential line read as empty.
        new_payload.extend(b"\x01" * pad)
        rom[abs_off : abs_off + len(original)] = new_payload

        results.append(
            {
                "abs": line["abs"],
                "jp": line.get("jp"),
                "ko": ko,
                "encoded_hex": " ".join(f"{b:02X}" for b in encoded),
                "dict_index": dict_index,
                "token": " ".join(f"{b:02X}" for b in token),
                "prefix_hex": " ".join(f"{b:02X}" for b in prefix),
                "old_body_len": len(body),
                "pad": pad,
                "decode_check": None,
            }
        )

    for i, p in enumerate(ptrs):
        off = DICT_PTR_START + i * 2
        bank5f[off] = p & 0xFF
        bank5f[off + 1] = (p >> 8) & 0xFF
    patch_bank(rom, SEG_DICT, bank5f)

    d2 = Dictionary(rom)
    for r in results:
        r["decode_check"] = d2.expand_index(r["dict_index"], tbl)

    return {
        "dict_count": len(ptrs),
        "unused_slots_available": len(unused_slots),
        "slots_used": unused_slots[: len(lines)],
        "hangul_marker_code": (
            f"{marker_code:04X}" if marker_code is not None else None
        ),
        "lines_patched": len(results),
        "results": results,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--rom",
        type=Path,
        default=ROOT / "out" / "patch" / "rom_font_only.wsc",
        help="Input ROM (preferably font-patched)",
    )
    ap.add_argument(
        "--tbl",
        type=Path,
        default=ROOT / "out" / "patch" / "hangul_patch_pad3.tbl",
    )
    ap.add_argument(
        "--translations",
        type=Path,
        default=ROOT / "data" / "translations_seed.json",
    )
    ap.add_argument(
        "--hangul-marker",
        type=lambda s: int(s, 16),
        default=None,
        help="Prefix each Hangul syllable with this two-byte runtime marker",
    )
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "patch")
    args = ap.parse_args()

    assert_translation_source_allowed(
        args.translations,
        role="direct translation application",
    )

    if not args.rom.exists():
        args.rom = find_rom(ROOT)
        print(f"WARNING: font ROM missing, using {args.rom}")

    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    rom = load_rom(args.rom)
    tbl = Tbl.load(args.tbl)
    seed = json.loads(args.translations.read_text(encoding="utf-8"))

    report = apply_translations(
        rom,
        tbl,
        seed["lines"],
        marker_code=args.hangul_marker,
    )
    report["checksum"] = f"{update_ws_checksum(rom):04X}"
    out_rom = out / "monoeye_ko_seed.wsc"
    out_rom.write_bytes(rom)
    report_path = out / "apply_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"Patched {report['lines_patched']} lines using safe unused dictionary slots"
    )
    for r in report["results"]:
        ok = r["decode_check"] == r["ko"]
        mark = "OK" if ok else "FAIL"
        try:
            print(f"  [{mark}] @{r['abs']} -> {r['decode_check']}")
        except UnicodeEncodeError:
            print(
                f"  [{mark}] @{r['abs']} -> "
                f"{r['decode_check'].encode('unicode_escape').decode()}"
            )
    print(f"Wrote {out_rom}")
    print(f"Wrote {report_path}")

    if any(r["decode_check"] != r["ko"] for r in report["results"]):
        sys.exit(1)


if __name__ == "__main__":
    main()
