#!/usr/bin/env python3
"""Audit the UI/unit-name follow-up candidate or promoted main TIP."""
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
from build_ui_unit_name_followup_candidate import load_catalog, payload_at
from expand_dictionary import _walk_zstring_range
from mixed_residual_classification import is_japanese_character
from monoeye_rom import Dictionary, Tbl, load_rom
from normalize_ko_text import normalize_ko_text

ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/ext_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
REPORT = ROOT / "out/patch/ui_unit_name_followup_report.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def has_japanese(text: str) -> bool:
    return any(is_japanese_character(character) for character in text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        type=Path,
        default=ROOT / "out/patch/ui_unit_name_followup_candidate.wsc",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "out/patch/ui_unit_name_followup_audit.json",
    )
    args = parser.parse_args()

    original = bytes(load_rom(ORIGINAL))
    target = bytes(load_rom(args.target))
    tbl = Tbl.load(TBL_PATH)
    d_original = Dictionary(original)
    d_target = make_dictionary_ext3(
        target, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    catalog = load_catalog()

    target_checks: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for row in report.get("records") or []:
        logical = int(str(row["abs"]), 16)
        rendered = d_target.expand(payload_at(target, logical), tbl).rstrip("　 \t")
        expected = normalize_ko_text(str(row["ko"])).rstrip("　 \t")
        ok = rendered == expected
        check = {
            "abs": str(row["abs"]),
            "region": str(row["region"]),
            "jp": str(row["jp"]),
            "expected": expected,
            "rendered": rendered,
            "ok": ok,
        }
        target_checks.append(check)
        if not ok:
            failures.append(check)

    residuals: list[dict[str, Any]] = []
    for lo, hi, region in (
        (0x5C0000, 0x5C7900, "bank5c_unit_name_table"),
        (0x75B000, 0x75C000, "bank75_ui_name_table"),
    ):
        for logical, original_payload, _kind in _walk_zstring_range(
            original, lo, hi, region=region, max_len=128
        ):
            jp = d_original.expand(original_payload, tbl)
            ko = catalog.get(jp)
            if not ko:
                continue
            rendered = d_target.expand(payload_at(target, logical), tbl).rstrip("　 \t")
            expected = normalize_ko_text(ko).rstrip("　 \t")
            if has_japanese(rendered) or (jp == "ムサイ" and rendered != "무사이"):
                residuals.append(
                    {
                        "abs": f"{logical:06X}",
                        "region": region,
                        "jp": jp,
                        "expected": expected,
                        "rendered": rendered,
                    }
                )

    shared = {
        "index": "06C3",
        "rendered": d_target.expand_index(0x06C3, tbl),
        "expected": "무사이",
    }
    shared["ok"] = shared["rendered"] == shared["expected"]
    if not shared["ok"]:
        failures.append({"kind": "shared_Musai", **shared})

    examples = {}
    for logical in (
        0x75B2F3,
        0x75B3B7,
        0x75B3BD,
        0x75B3C1,
        0x75B3C5,
        0x75B3F1,
        0x75B3F4,
        0x75B411,
        0x5C4832,
        0x5C73BC,
        0x5C721F,
    ):
        examples[f"{logical:06X}"] = d_target.expand(payload_at(target, logical), tbl).rstrip(
            "　 \t"
        )

    document = {
        "schema_version": 1,
        "generated_by": "tools/audit_ui_unit_name_followup.py",
        "target": {
            "path": str(args.target.resolve()),
            "size": len(target),
            "sha256": sha256_bytes(target),
        },
        "counts": {
            "target_records": len(target_checks),
            "target_exact": sum(1 for row in target_checks if row["ok"]),
            "target_failures": len(failures),
            "catalog_japanese_residuals": len(residuals),
        },
        "shared_Musai": shared,
        "examples": examples,
        "residuals": residuals,
        "target_failures": failures,
        "ok": not failures and not residuals,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"counts": document["counts"], "examples": examples, "ok": document["ok"]}, ensure_ascii=False, indent=2))
    return 0 if document["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
