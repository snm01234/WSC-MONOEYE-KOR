#!/usr/bin/env python3
"""Audit current ID-command, indirect-command, and shooting-label residuals."""
from __future__ import annotations

import collections
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from expand_dictionary import _walk_zstring_range
from extract_script import split_prefix_body
from mixed_residual_classification import hangul_character_count, japanese_character_count
from monoeye_rom import Dictionary, Tbl, find_rom, load_rom, read_encoded_z_safe, stock_base

TIP = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/current_tip_id_indirect_command_residual_audit.json"
TERMS = ("間接", "ＩＤコマンド", "ミノフスキ－粒子散布")
RANGES = ((0x5C0000, 0x5D0000), (0x5F2500, 0x5F4000), (0x750000, 0x760000))
EXTRA_EXACT = {0x5F3B8B: {"category": "shooting_command_label", "original": "射撃"}}


class AuditError(RuntimeError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    tip = bytes(load_rom(TIP))
    original_path = find_rom(ROOT)
    original = bytes(load_rom(original_path))
    tbl = Tbl.load(TBL_PATH)
    od = Dictionary(original)
    cd = make_dictionary_ext3(tip, load_ext_meta(EXT_META), load_ext_meta(EXT3_META))
    sb = stock_base(tip)

    matches: list[dict[str, Any]] = []
    seen: set[int] = set()
    for lo, hi in RANGES:
        for logical, op, _kind in _walk_zstring_range(original, lo, hi, region="id_indirect", max_len=256):
            try:
                full = od.expand(op, tbl)
            except Exception:
                continue
            if not any(term in full for term in TERMS):
                continue
            prefix, original_body, _kind = split_prefix_body(op)
            got = read_encoded_z_safe(tip, sb + logical, max_len=256)
            if not got:
                continue
            cp = bytes(got[0])
            current_body = cp[len(prefix):] if prefix and cp.startswith(prefix) else cp
            try:
                ot = od.expand(original_body if original_body else op, tbl).rstrip("\u3000 \t")
                ct = cd.expand(current_body, tbl).rstrip("\u3000 \t")
            except Exception:
                continue
            if "ＩＤコマンド" in full:
                category = "id_command_text"
            elif "ミノフスキ－粒子散布" in full:
                category = "id_command_activation"
            else:
                category = "indirect_command_text"
            matches.append(
                {
                    "abs": f"{logical:06X}",
                    "category": category,
                    "prefix_hex": prefix.hex().upper(),
                    "original_body": ot,
                    "current_body": ct,
                    "japanese_residual": japanese_character_count(ct),
                    "hangul": hangul_character_count(ct),
                }
            )
            seen.add(logical)

    for logical, meta in EXTRA_EXACT.items():
        if logical in seen:
            continue
        op = read_encoded_z_safe(original, stock_base(original) + logical, max_len=64)
        cp = read_encoded_z_safe(tip, sb + logical, max_len=64)
        if not op or not cp:
            raise AuditError(f"extra exact record missing at {logical:06X}")
        ot = od.expand(op[0], tbl).rstrip("\u3000 \t")
        ct = cd.expand(cp[0], tbl).rstrip("\u3000 \t")
        if ot != meta["original"]:
            raise AuditError(f"extra exact original drifted at {logical:06X}")
        matches.append(
            {
                "abs": f"{logical:06X}",
                "category": meta["category"],
                "prefix_hex": "",
                "original_body": ot,
                "current_body": ct,
                "japanese_residual": japanese_character_count(ct),
                "hangul": hangul_character_count(ct),
                "extra_exact": True,
            }
        )

    residuals = [row for row in matches if int(row["japanese_residual"]) > 0]
    clean = [row for row in matches if int(row["japanese_residual"]) == 0]
    counts = {
        "term_matches_without_extra": len(matches) - len(EXTRA_EXACT),
        "extra_exact_labels": len(EXTRA_EXACT),
        "total_checked": len(matches),
        "actionable_residuals": len(residuals),
        "already_clean": len(clean),
        "residual_by_category": dict(collections.Counter(row["category"] for row in residuals)),
        "residual_jp_only": sum(int(row["hangul"]) == 0 for row in residuals),
        "residual_mixed": sum(int(row["hangul"]) > 0 for row in residuals),
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_id_indirect_command_residuals.py",
        "read_only": True,
        "ok": True,
        "tip": {"path": str(TIP.relative_to(ROOT)), "size": len(tip), "sha256": sha(tip)},
        "counts": counts,
        "actionable": sorted(residuals, key=lambda row: int(row["abs"], 16)),
        "already_clean": sorted(clean, key=lambda row: int(row["abs"], 16)),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"ok": True, "counts": counts, "out": str(OUT.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
