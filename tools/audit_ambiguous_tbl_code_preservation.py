#!/usr/bin/env python3
"""Audit raw-code identity for characters with duplicate TBL mappings.

Several UI tile codes intentionally decode to the same visible audit placeholder
(e.g. E6C5/E6C9/E736 all decode as ``█``).  A decode/replace/re-encode pass can
therefore preserve rendered audit text while silently replacing distinct left,
right, or padding tiles with the first code in the TBL.  This audit compares a
reference ROM against a candidate ROM and reports any such identity drift.

Dictionary tokens are skipped: only direct glyph/tile atoms are compared.  The
reference TBL defines which visible characters are ambiguous.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from monoeye_rom import Tbl, load_rom  # noqa: E402

DEFAULT_EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
DEFAULT_EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"


class AuditError(RuntimeError):
    pass


def ambiguous_chars(tbl: Tbl) -> dict[str, set[int]]:
    by_char: defaultdict[str, set[int]] = defaultdict(set)
    for code, ch in tbl.code_to_char.items():
        if ch:
            by_char[ch].add(code)
    return {ch: codes for ch, codes in by_char.items() if len(codes) > 1}


def direct_ambiguous_atoms(
    raw: bytes, tbl: Tbl, ambiguous: set[str]
) -> list[dict[str, Any]]:
    atoms: list[dict[str, Any]] = []
    pos = 0
    while pos < len(raw):
        lead = raw[pos]
        if 0xF0 <= lead <= 0xFF:
            if pos + 1 >= len(raw):
                raise AuditError("truncated dictionary token")
            pos += 2
            continue
        if 0xE0 <= lead <= 0xEF:
            if pos + 1 >= len(raw):
                raise AuditError("truncated two-byte direct glyph")
            width = 2
            code = (lead << 8) | raw[pos + 1]
        else:
            width = 1
            code = lead
        ch = tbl.code_to_char.get(code)
        if ch in ambiguous:
            atoms.append(
                {
                    "char": ch,
                    "code": code,
                    "offset": pos,
                    "width": width,
                }
            )
        pos += width
    return atoms


def logical_indices(dictionary) -> list[int]:
    indices = list(range(dictionary.stock_count))
    indices.extend(range(0x1000, 0x1000 + dictionary.ext3_count))
    return indices


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference-tip", type=Path, required=True)
    ap.add_argument("--candidate-tip", type=Path, required=True)
    ap.add_argument("--reference-tbl", type=Path, required=True)
    ap.add_argument("--candidate-tbl", type=Path, required=True)
    ap.add_argument("--ext-meta", type=Path, default=DEFAULT_EXT_META)
    ap.add_argument("--ext3-meta", type=Path, default=DEFAULT_EXT3_META)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--no-fail", action="store_true")
    args = ap.parse_args()

    ref_rom = bytes(load_rom(args.reference_tip))
    cand_rom = bytes(load_rom(args.candidate_tip))
    ref_tbl = Tbl.load(args.reference_tbl)
    cand_tbl = Tbl.load(args.candidate_tbl)
    ext_meta = load_ext_meta(args.ext_meta)
    ext3_meta = load_ext_meta(args.ext3_meta)
    ref_dict = make_dictionary_ext3(ref_rom, ext_meta, ext3_meta)
    cand_dict = make_dictionary_ext3(cand_rom, ext_meta, ext3_meta)

    ambiguous_map = ambiguous_chars(ref_tbl)
    ambiguous = set(ambiguous_map)
    mismatches: list[dict[str, Any]] = []
    checked_with_ambiguous = 0

    for index in logical_indices(ref_dict):
        try:
            ref_raw = ref_dict.raw_entry(index)
            cand_raw = cand_dict.raw_entry(index)
        except Exception:
            continue
        try:
            ref_atoms = direct_ambiguous_atoms(ref_raw, ref_tbl, ambiguous)
            cand_atoms = direct_ambiguous_atoms(cand_raw, cand_tbl, ambiguous)
        except AuditError as exc:
            mismatches.append(
                {
                    "index": f"{index:05X}",
                    "kind": "parse_error",
                    "error": str(exc),
                }
            )
            continue
        if not ref_atoms and not cand_atoms:
            continue
        checked_with_ambiguous += 1
        ref_chars = [row["char"] for row in ref_atoms]
        cand_chars = [row["char"] for row in cand_atoms]
        ref_codes = [row["code"] for row in ref_atoms]
        cand_codes = [row["code"] for row in cand_atoms]
        if ref_chars == cand_chars and ref_codes == cand_codes:
            continue
        mismatches.append(
            {
                "index": f"{index:05X}",
                "reference_entry_abs": f"{ref_dict.entry_abs(index):07X}",
                "candidate_entry_abs": f"{cand_dict.entry_abs(index):07X}",
                "kind": (
                    "raw_identity_changed"
                    if ref_chars == cand_chars
                    else "ambiguous_character_sequence_changed"
                ),
                "reference": [
                    {
                        "char": row["char"],
                        "code": f"{row['code']:04X}",
                        "offset": row["offset"],
                    }
                    for row in ref_atoms
                ],
                "candidate": [
                    {
                        "char": row["char"],
                        "code": f"{row['code']:04X}",
                        "offset": row["offset"],
                    }
                    for row in cand_atoms
                ],
            }
        )

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_ambiguous_tbl_code_preservation.py",
        "reference_tip": str(args.reference_tip),
        "candidate_tip": str(args.candidate_tip),
        "ambiguous_tbl_mappings": {
            ch: [f"{code:04X}" for code in sorted(codes)]
            for ch, codes in sorted(ambiguous_map.items())
        },
        "counts": {
            "dictionary_entries_with_ambiguous_direct_codes": checked_with_ambiguous,
            "mismatches": len(mismatches),
        },
        "mismatches": mismatches,
        "status": "clean" if not mismatches else "violations_found",
    }

    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "status": report["status"],
                "checked": checked_with_ambiguous,
                "mismatches": len(mismatches),
            },
            ensure_ascii=True,
        )
    )
    return 0 if args.no_fail or not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
