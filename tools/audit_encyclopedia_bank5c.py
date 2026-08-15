#!/usr/bin/env python3
"""Audit untranslated/partially translated encyclopedia records in bank 5C.

The encyclopedia stores one visible line as an independent NUL-terminated
record.  Existing broad residue audits focused on previously classified source
populations and therefore missed many records whose shared dictionary tokens
were already partly Korean.  This audit walks Original-derived boundaries,
decodes the current TIP with the active ext3 dictionary, and reports every
record that still contains Japanese characters.

No ROM or SaveRAM is modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from expand_dictionary import _walk_zstring_range
from mixed_residual_classification import is_japanese_character
from monoeye_rom import Dictionary, Tbl, read_encoded_z_safe, stock_base

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
DEFAULT_OUT = ROOT / "out/patch/encyclopedia_bank5c_residual_audit.json"

# Encyclopedia-only labels that use a different spelling from the canonical
# unit-data name.  Fullwidth Latin suffixes such as ＪＧ are not Japanese
# characters, so a character-class audit cannot find these mismatches.
KNOWN_NAME_ALIASES = {
    "ゲルググＪＧ": "겔구그　예거",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_hex(value: str) -> int:
    return int(value.replace(":", "").replace("0x", ""), 16)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rom", type=Path, default=TIP)
    parser.add_argument("--start", default="5C0000")
    parser.add_argument("--end", default="5C7900")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    start = parse_hex(args.start)
    end = parse_hex(args.end)
    if not (0 <= start < end):
        raise SystemExit("invalid range")

    original = ORIGINAL.read_bytes()
    target_path = args.rom.resolve()
    tip = target_path.read_bytes()
    tbl = Tbl.load(TBL_PATH)
    d_original = Dictionary(original)
    d_tip = make_dictionary_ext3(tip, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))

    records: list[dict[str, Any]] = []
    scanned = 0
    for logical, original_payload, kind in _walk_zstring_range(
        original, start, end, region="bank5c_encyclopedia", max_len=256
    ):
        scanned += 1
        got = read_encoded_z_safe(tip, stock_base(tip) + logical, max_len=256)
        if not got:
            records.append(
                {
                    "abs": f"{logical:06X}",
                    "kind": kind,
                    "status": "current_record_unreadable",
                    "jp": d_original.expand(original_payload, tbl),
                    "original_payload_hex": bytes(original_payload).hex().upper(),
                }
            )
            continue
        current_payload = bytes(got[0])
        jp = d_original.expand(original_payload, tbl)
        current = d_tip.expand(current_payload, tbl)
        japanese_count = sum(is_japanese_character(ch) for ch in current)
        alias_target = KNOWN_NAME_ALIASES.get(jp)
        alias_mismatch = bool(alias_target and current.rstrip("　 \\t") != alias_target.rstrip("　 \\t"))
        if not japanese_count and not alias_mismatch:
            continue
        records.append(
            {
                "abs": f"{logical:06X}",
                "kind": kind,
                "status": "name_alias_mismatch" if alias_mismatch and not japanese_count else "japanese_residual",
                "jp": jp,
                "current": current,
                "japanese_count": japanese_count,
                "payload_len": len(original_payload),
                "original_payload_hex": bytes(original_payload).hex().upper(),
                "current_payload_hex": current_payload.hex().upper(),
                "translation_source": "",
                "review_status": "",
                "ko": alias_target or "",
            }
        )

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_encyclopedia_bank5c.py",
        "read_only": True,
        "inputs": {
            "tip": {
                "path": str(target_path),
                "size": len(tip),
                "sha256": sha256_bytes(tip),
            },
            "original": {
                "path": str(ORIGINAL.resolve()),
                "size": len(original),
                "sha256": sha256_bytes(original),
            },
        },
        "scope": {"start": f"{start:06X}", "end_exclusive": f"{end:06X}"},
        "counts": {
            "scanned_records": scanned,
            "japanese_residual_records": sum(
                row.get("status") == "japanese_residual" for row in records
            ),
            "name_alias_mismatches": sum(
                row.get("status") == "name_alias_mismatch" for row in records
            ),
            "actionable_records": sum(
                row.get("status") in {"japanese_residual", "name_alias_mismatch"} for row in records
            ),
            "unreadable_records": sum(
                row.get("status") == "current_record_unreadable" for row in records
            ),
            "short_body_under_4": sum(
                row.get("status") in {"japanese_residual", "name_alias_mismatch"} and int(row.get("payload_len", 0)) < 4
                for row in records
            ),
        },
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False))
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
