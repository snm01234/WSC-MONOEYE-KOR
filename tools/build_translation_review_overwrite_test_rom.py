#!/usr/bin/env python3
"""Materialize the static-approved retranslation overwrite test ROM.

The source candidate reports already proved that each selected replacement
fits its existing record extent.  This compositor writes those exact
before/after payloads onto the current TIP, rechecks prefix/metadata and
following boundaries, then rebuilds the authoritative runtime contract.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from dialogue_runtime_contracts import audit_manifest, boundary_signature, build_manifest  # noqa: E402
from monoeye_rom import load_rom, read_encoded_z_safe, stock_base, update_ws_checksum  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CONTRACT = ROOT / "out/script/dialogue_runtime_contracts.json"
SCENARIO_REPORT = ROOT / "out/patch/scenario_native_stock_static_candidate.json"
SPECIAL_REPORT = ROOT / "out/patch/special_native_stock_static_candidate.json"
DIRECT_REPORT = ROOT / "out/patch/direct_retranslation_static_test_candidate.json"
OUT_ROM = ROOT / "out/patch/translation_review_overwrite_static_test_candidate.wsc"
OUT_MANIFEST = ROOT / "out/script/translation_review_overwrite_static_test_candidate_contracts.json"
OUT_REPORT = ROOT / "out/patch/translation_review_overwrite_static_test_candidate.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    main_bytes = bytes(load_rom(MAIN))
    original_bytes = bytes(load_rom(ORIGINAL))
    candidate = bytearray(main_bytes)
    base = stock_base(main_bytes)
    contracts = {
        str(row.get("address") or "").upper(): row
        for row in load_json(CONTRACT).get("contracts") or []
    }
    selected_sources = [
        ("scenario_native_stock_or_direct", SCENARIO_REPORT),
        ("special_native_stock", SPECIAL_REPORT),
    ]
    applied: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    touched: set[int] = set()
    seen: set[str] = set()
    for source_kind, report_path in selected_sources:
        for item in load_json(report_path).get("selected") or []:
            address = str(item.get("abs") or "").upper()
            if address in seen:
                rejected.append({"abs": address, "reason": "duplicate_selected_address", "source": source_kind})
                continue
            seen.add(address)
            contract = contracts.get(address)
            try:
                before = bytes.fromhex(str(item.get("before_hex") or ""))
                after = bytes.fromhex(str(item.get("after_hex") or ""))
            except ValueError:
                rejected.append({"abs": address, "reason": "invalid_selected_hex", "source": source_kind})
                continue
            if not contract:
                rejected.append({"abs": address, "reason": "contract_missing", "source": source_kind})
                continue
            if str(contract.get("status") or "") != "active":
                rejected.append({"abs": address, "reason": "contract_not_active", "source": source_kind})
                continue
            logical = int(address, 16)
            record = read_encoded_z_safe(main_bytes, base + logical, max_len=256)
            if record is None or bytes(record[0]) != before:
                rejected.append({"abs": address, "reason": "main_before_payload_mismatch", "source": source_kind})
                continue
            if len(before) != len(after) or len(before) != int(contract.get("record_extent") or 0):
                rejected.append({"abs": address, "reason": "record_extent_changed", "source": source_kind})
                continue
            prefix_hex = str(contract.get("metadata_hex") or "") + str(contract.get("control_prefix_hex") or "")
            try:
                prefix = bytes.fromhex(prefix_hex)
            except ValueError:
                rejected.append({"abs": address, "reason": "invalid_prefix_hex", "source": source_kind})
                continue
            if prefix and (not before.startswith(prefix) or not after.startswith(prefix)):
                rejected.append({"abs": address, "reason": "control_or_metadata_prefix_changed", "source": source_kind})
                continue
            start = base + logical
            end = start + len(before)
            if any(offset in touched for offset in range(start, end)):
                rejected.append({"abs": address, "reason": "overlap", "source": source_kind})
                continue
            before_boundary = boundary_signature(main_bytes, logical + len(before))
            candidate[start:end] = after
            after_boundary = boundary_signature(bytes(candidate), logical + len(before))
            if before_boundary != after_boundary:
                candidate[start:end] = before
                rejected.append({"abs": address, "reason": "following_boundary_changed", "source": source_kind})
                continue
            touched.update(range(start, end))
            applied.append({
                "abs": address,
                "source_kind": source_kind,
                "route": contract.get("route"),
                "storage_strategy": item.get("storage_strategy") or "native_stock",
                "changed": before != after,
                "record_extent": len(before),
                "control_prefix_hex": prefix_hex,
                "before_hex": before.hex().upper(),
                "after_hex": after.hex().upper(),
                "boundary": before_boundary,
                "text": item.get("proposed_korean") or item.get("text") or "",
            })

    update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    control_rows_checked = 0
    control_rows_preserved = 0
    control_prefix_failures: list[dict[str, str]] = []
    for address, contract in contracts.items():
        prefix_hex = str(contract.get("metadata_hex") or "") + str(contract.get("control_prefix_hex") or "")
        if not prefix_hex:
            continue
        try:
            prefix = bytes.fromhex(prefix_hex)
        except ValueError:
            continue
        logical = int(address, 16)
        before_record = read_encoded_z_safe(main_bytes, base + logical, max_len=256)
        after_record = read_encoded_z_safe(candidate_bytes, base + logical, max_len=256)
        if before_record is None or after_record is None:
            continue
        control_rows_checked += 1
        before_payload = bytes(before_record[0])
        after_payload = bytes(after_record[0])
        if before_payload.startswith(prefix) and after_payload.startswith(prefix):
            control_rows_preserved += 1
        else:
            control_prefix_failures.append({"abs": address, "prefix_hex": prefix_hex})
    manifest = build_manifest(original_bytes, candidate_bytes, target_path=OUT_ROM)
    safety = audit_manifest(candidate_bytes, manifest, target_path=OUT_ROM)
    OUT_ROM.write_bytes(candidate_bytes)
    OUT_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    direct = load_json(DIRECT_REPORT)
    report = {
        "schema_version": 1,
        "artifact": "translation-review-overwrite-static-test-candidate/v1",
        "generated_by": "tools/build_translation_review_overwrite_test_rom.py",
        "read_only_test_artifact": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "promotion_allowed": False,
        "promotion_block_reason": "static overwrite test ROM; dynamic runtime/BizHawk approval is pending",
        "main_unchanged": sha(MAIN.read_bytes()) == sha(main_bytes),
        "saveram_changed": False,
        "source_main_sha256": sha(main_bytes),
        "candidate_sha256": sha(candidate_bytes),
        "applied": applied,
        "rejected": rejected,
        "counts": {
            "applied_rows": len(applied),
            "changed_rows": sum(bool(row["changed"]) for row in applied),
            "unchanged_native_reuse_rows": sum(not bool(row["changed"]) for row in applied),
            "rejected_rows": len(rejected),
            "scenario_rows": sum(row["source_kind"] == "scenario_native_stock_or_direct" for row in applied),
            "special_rows": sum(row["source_kind"] == "special_native_stock" for row in applied),
            "direct_payload_rows_available": int((direct.get("counts") or {}).get("selected_rows") or 0),
            "direct_payload_rows_excluded": int((direct.get("counts") or {}).get("excluded_rows") or 0),
            "candidate_hard_failures": int((safety.get("counts") or {}).get("hard_failures") or 0),
            "candidate_review_items": int((safety.get("counts") or {}).get("review_items") or 0),
            "control_rows_checked": control_rows_checked,
            "control_rows_prefix_preserved": control_rows_preserved,
            "control_prefix_failures": len(control_prefix_failures),
        },
        "direct_overwrite_exclusion_reason_counts": direct.get("exclusion_reason_counts") or {},
        "candidate_safety": safety,
        "candidate_manifest": str(OUT_MANIFEST.relative_to(ROOT)).replace("\\", "/"),
        "control_prefix_failures": control_prefix_failures,
        "policy": {
            "overwrite_scope": "selected retranslation payloads overwrite only their existing body extents",
            "control_rows": "metadata/control prefixes and following control boundaries are byte-exact",
            "static_exclusion": "any contract, extent, capacity, NUL, width, dictionary, ext3/compact3, quality, or boundary violation remains untouched",
            "dynamic_runtime": "not performed in this build step",
        },
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok = (
        report["main_unchanged"]
        and not report["saveram_changed"]
        and not rejected
        and not control_prefix_failures
        and int((safety.get("counts") or {}).get("hard_failures") or 0) == 0
        and int((safety.get("counts") or {}).get("review_items") or 0) == 0
    )
    print(json.dumps({"ok": ok, "candidate": str(OUT_ROM), "counts": report["counts"], "report": str(OUT_REPORT)}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
