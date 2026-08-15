#!/usr/bin/env python3
"""Inventory current-main modifications inside structurally excluded fixed data.

This is a read-only audit.  It does not declare every modification a bug.
Instead it separates:
- generic-dialogue provenance violations (high risk),
- modifications with an explicit dedicated-tool reference,
- modified excluded records with no exact-address tool provenance (manual review).

The report is intended to prevent another 69:3D54-style false-positive repair.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STOCK = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
MANIFEST = ROOT / "out/script/fixed_data_decoder_review_manifest.json"
DEFAULT_OUT = ROOT / "out/patch/current_main_structural_excluded_modifications_audit.json"
GENERIC_PATTERNS = (
    "runtime_measured_followup",
    "duplicate_partial",
    "duplicate_residual",
    "dialogue_retranslation",
    "translation_sheet",
)


def load_records() -> list[dict]:
    obj = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    for key in ("records", "rows", "items"):
        value = obj.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    raise RuntimeError("unsupported fixed-data manifest schema")


def contiguous_runs(offsets: list[int]) -> list[list[int]]:
    if not offsets:
        return []
    out: list[list[int]] = []
    start = prev = offsets[0]
    for value in offsets[1:]:
        if value == prev + 1:
            prev = value
            continue
        out.append([start, prev])
        start = prev = value
    out.append([start, prev])
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    stock = STOCK.read_bytes()
    main = MAIN.read_bytes()
    stock_off = len(main) - len(stock)

    tool_texts: list[tuple[str, str]] = []
    for path in sorted((ROOT / "tools").glob("*.py")):
        try:
            tool_texts.append((path.name, path.read_text(encoding="utf-8", errors="ignore").upper()))
        except OSError:
            pass

    modified = []
    excluded_total = 0
    for row in load_records():
        if row.get("review_status") != "structural_excluded_non_dialogue" or row.get("application_allowed") is not False:
            continue
        raw_abs = row.get("abs")
        body_len = row.get("body_len")
        if not raw_abs or body_len is None:
            continue
        excluded_total += 1
        logical = int(str(raw_abs), 16)
        size = int(body_len)
        before = stock[logical:logical + size]
        after = main[stock_off + logical:stock_off + logical + size]
        if before == after:
            continue
        changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        addr_text = f"{logical:06X}"
        refs = [name for name, text in tool_texts if addr_text in text]
        generic_refs = [name for name in refs if any(p in name.lower() for p in GENERIC_PATTERNS)]
        dedicated_refs = [name for name in refs if name not in generic_refs]
        if generic_refs:
            classification = "generic_dialogue_provenance_violation_high_risk"
        elif dedicated_refs:
            classification = "modified_excluded_with_explicit_tool_reference_review"
        else:
            classification = "modified_excluded_unattributed_manual_review"
        modified.append({
            "abs": addr_text,
            "bank": addr_text[:2],
            "jp": row.get("jp"),
            "body_len": size,
            "next_record_gap": row.get("next_record_gap"),
            "route": row.get("route"),
            "review_status": row.get("review_status"),
            "application_allowed": row.get("application_allowed"),
            "changed_byte_count": len(changed),
            "changed_relative_runs": contiguous_runs(changed),
            "stock_hex": before.hex().upper(),
            "main_hex": after.hex().upper(),
            "exact_address_tool_refs": refs,
            "generic_tool_refs": generic_refs,
            "dedicated_or_other_tool_refs": dedicated_refs,
            "classification": classification,
        })

    # 69:3D54 is deliberately included as a resolved example even though it is
    # no longer a current modified-excluded hit after promotion.
    resolved = {
        "abs": "693D54",
        "status": "confirmed_false_positive_restored_in_main",
        "evidence": [
            "manifest structural_excluded_non_dialogue/application_allowed=false",
            "active ending resource bank69:3C4E with 4-byte entries around 3D54",
            "state35 cross-ROM oracle isolated current-main fault to physical E9 bank",
            "restoring F33F01->F24403 produced stock-identical 80/80 BG words and user runtime fix",
        ],
    }
    high_risk = [x for x in modified if x["classification"] == "generic_dialogue_provenance_violation_high_risk"]
    report = {
        "schema_version": 1,
        "ok": True,
        "policy": {
            "authority": "fixed_data_decoder_review_manifest structural exclusion overrides generic CSV dialogue labels",
            "automatic_action": "never auto-revert all hits; block new generic writes, then prove resource/text format per address",
            "dedicated_override": "explicit per-address allow only after dedicated decoder/format proof and runtime validation",
        },
        "excluded_record_count": excluded_total,
        "modified_excluded_count": len(modified),
        "high_risk_generic_provenance_count": len(high_risk),
        "high_risk_generic_provenance": high_risk,
        "all_modified_excluded": modified,
        "resolved_false_positive": resolved,
        "specific_review": {
            "67AF01_67C0EC": {
                "status": "quarantine_keep_current_pending_dedicated_consumer_proof",
                "reason": "both violate generic-write policy, but neighboring records form a coherent fixed-data label/string table; blind stock restore could reintroduce Japanese or alter intended fixed-data localization",
                "safe_next_step": "identify actual consumer/decoder, then either validate current 8-byte tokenized form or restore/re-encode with the dedicated format; do not change main merely from raw duplicate evidence",
            }
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "modified_excluded_count": len(modified),
        "high_risk_generic_provenance_count": len(high_risk),
        "high_risk_addresses": [x["abs"] for x in high_risk],
        "report": str(args.out.relative_to(ROOT)).replace("\\", "/"),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
