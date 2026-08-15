#!/usr/bin/env python3
"""Build a read-only manifest for fixed-stride script data.

Banks 64..6F are not dialogue records.  This audit intentionally does not
decode, translate, or write them; it records the exact rows that need a
route-specific decoder and makes their review state explicit.  In particular,
legacy ``ko`` values are never treated as approved translations or sizing
proxies.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHEET = ROOT / "out/script/translation_sheet.csv"
ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
OUT = ROOT / "out/script/fixed_data_decoder_review_manifest.json"
INVENTORY = ROOT / "out/patch/bank64_6f_structure_inventory.json"
FIXED_BANKS = set(range(0x64, 0x70))
JP_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
CONTROL_RE = re.compile(r"<(?!E62F>)[A-Fa-f0-9]{2,8}>")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def is_hangul(ch: str) -> bool:
    return "\uac00" <= ch <= "\ud7a3"


def hex_len(value: str) -> int:
    cleaned = "".join(str(value or "").split())
    if not cleaned:
        return 0
    if len(cleaned) % 2 or any(c not in "0123456789abcdefABCDEF" for c in cleaned):
        return -1
    return len(bytes.fromhex(cleaned))


def main() -> int:
    csv.field_size_limit(10**9)
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8")) if INVENTORY.is_file() else {}
    structurally_excluded = bool(
        inventory.get("ok")
        and (inventory.get("checks") or {}).get("zero_production_targets")
        and (inventory.get("checks") or {}).get("promoted_tip_exact")
    )
    with SHEET.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    fixed = [
        row for row in rows
        if len(str(row.get("abs") or "")) >= 2
        and int(str(row["abs"]), 16) >> 16 in FIXED_BANKS
    ]
    fixed.sort(key=lambda row: int(str(row["abs"]), 16))

    details: list[dict[str, object]] = []
    by_bank: dict[str, Counter[str]] = defaultdict(Counter)
    status_counts: Counter[str] = Counter()
    for index, row in enumerate(fixed):
        address = int(str(row["abs"]), 16)
        bank = f"{address >> 16:02X}"
        ko = str(row.get("ko") or "")
        prefix_len = hex_len(str(row.get("prefix_hex") or ""))
        body_len = hex_len(str(row.get("body_hex") or ""))
        next_address = None
        if index + 1 < len(fixed):
            candidate = int(str(fixed[index + 1]["abs"]), 16)
            if candidate >> 16 == address >> 16:
                next_address = candidate
        gap = next_address - address if next_address is not None else None
        has_japanese = bool(JP_RE.search(ko))
        has_control = bool(CONTROL_RE.search(ko) or "\x00" in ko)
        empty = not ko.strip()
        status = (
            "structural_excluded_non_dialogue"
            if structurally_excluded
            else "quality_review_retranslation_required"
            if has_japanese or has_control or empty
            else "llm_review_required_no_explicit_provenance"
        )
        status_counts[status] += 1
        by_bank[bank][status] += 1
        by_bank[bank]["rows"] += 1
        by_bank[bank][f"body_len_{body_len}"] += 1
        details.append({
            "abs": f"{address:06X}",
            "bank": bank,
            "id": str(row.get("id") or ""),
            "jp": str(row.get("jp") or ""),
            "legacy_ko": ko,
            "prefix_hex": str(row.get("prefix_hex") or ""),
            "body_hex": str(row.get("body_hex") or ""),
            "prefix_len": prefix_len,
            "body_len": body_len,
            "next_record_abs": f"{next_address:06X}" if next_address is not None else "",
            "next_record_gap": gap,
            "residual_japanese_in_legacy_ko": has_japanese,
            "control_or_nul_in_legacy_ko": has_control,
            "empty_legacy_ko": empty,
            "review_status": status,
            "route": "fixed_stride_dedicated_decoder",
            "translation_source_policy": "legacy_ko_is_not_an_application_source",
            "application_allowed": False,
            "structural_inventory_excluded": structurally_excluded,
        })

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_fixed_data_decoder_static.py",
        "read_only": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "scope": {
            "logical_banks": ["64", "65", "66", "67", "68", "69", "6A", "6B", "6C", "6D", "6E", "6F"],
            "route": "fixed_stride_dedicated_decoder",
            "generic_dialogue_decoder_allowed": False,
            "legacy_probe_material_authoritative": False,
        },
        "inputs": {
            "sheet": str(SHEET.relative_to(ROOT)).replace("\\", "/"),
            "sheet_sha256": sha(SHEET),
            "main_rom": str(ROM.relative_to(ROOT)).replace("\\", "/"),
            "main_rom_sha256": sha(ROM),
            "fixed_inventory": str(INVENTORY.relative_to(ROOT)).replace("\\", "/"),
            "fixed_inventory_sha256": sha(INVENTORY) if INVENTORY.is_file() else "",
        },
        "counts": {
            "rows": len(details),
            "status": dict(sorted(status_counts.items())),
            "banks": len({row["bank"] for row in details}),
            "application_allowed": 0,
            "structurally_excluded": sum(bool(row["structural_inventory_excluded"]) for row in details),
        },
        "by_bank": {bank: dict(sorted(counter.items())) for bank, counter in sorted(by_bank.items())},
        "next_actions": [
            "Attach a dedicated fixed-stride decoder contract per bank/record family.",
            "LLM-review each legacy Korean value from the Japanese source; do not reuse the legacy value as a translation or capacity proxy.",
            "Prove record extent, terminator/padding, and storage encoding statically before preparing any candidate.",
            "Keep all rows out of generic dialogue promotion until the dedicated contract is complete.",
        ],
        "records": details,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT.relative_to(ROOT)), "counts": report["counts"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
