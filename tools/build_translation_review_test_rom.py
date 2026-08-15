#!/usr/bin/env python3
"""Compose the current-TIP static translation candidates into a test ROM.

This is intentionally a test artifact, not a promotion.  It applies only
rows already accepted by the existing static storage candidates, keeps every
control/portrait prefix byte-exact, and records all remaining retranslated
rows as deferred with their source reason.  No runtime/BizHawk execution is
performed here.
"""

from __future__ import annotations

import hashlib
import json
import csv
from collections import Counter
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
STORAGE_SCENARIO = ROOT / "out/script/scenario_storage_static_audit.json"
STORAGE_SPECIAL = ROOT / "out/script/special_route_storage_static_audit.json"
SCENARIO_REVIEW_RESULTS = ROOT / "out/script/main_translation_llm_review/results"
QUEUE = ROOT / "out/script/translation_workstreams_static_queue.csv"
OUT_ROM = ROOT / "out/patch/translation_review_static_test_candidate.wsc"
OUT_MANIFEST = ROOT / "out/script/translation_review_static_test_candidate_contracts.json"
OUT_REPORT = ROOT / "out/patch/translation_review_static_test_candidate.json"

csv.field_size_limit(100_000_000)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    main_bytes = bytes(load_rom(MAIN))
    original_bytes = bytes(load_rom(ORIGINAL))
    candidate = bytearray(main_bytes)
    base = stock_base(main_bytes)
    contract_doc = load_json(CONTRACT)
    contracts = {str(row["address"]).upper(): row for row in contract_doc.get("contracts") or []}
    candidate_sources = [
        ("scenario", SCENARIO_REPORT),
        ("special", SPECIAL_REPORT),
    ]
    changes: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    touched: set[int] = set()
    selected_by_abs: dict[str, dict[str, object]] = {}

    for source_kind, report_path in candidate_sources:
        report = load_json(report_path)
        for item in report.get("selected") or []:
            address = str(item.get("abs") or "").upper()
            if address in selected_by_abs:
                rejected.append({"abs": address, "reason": "duplicate_selected_candidate", "sources": [selected_by_abs[address]["source_kind"], source_kind]})
                continue
            contract = contracts.get(address)
            if not contract:
                rejected.append({"abs": address, "reason": "contract_missing", "source_kind": source_kind})
                continue
            try:
                before = bytes.fromhex(str(item.get("before_hex") or ""))
                after = bytes.fromhex(str(item.get("after_hex") or ""))
            except ValueError:
                rejected.append({"abs": address, "reason": "invalid_before_after_hex", "source_kind": source_kind})
                continue
            logical = int(address, 16)
            record = read_encoded_z_safe(main_bytes, base + logical, max_len=256)
            if record is None:
                rejected.append({"abs": address, "reason": "unreadable_record", "source_kind": source_kind})
                continue
            current_payload = bytes(record[0])
            if current_payload != before:
                rejected.append({"abs": address, "reason": "main_before_bytes_mismatch", "source_kind": source_kind})
                continue
            if len(after) != len(before):
                rejected.append({"abs": address, "reason": "record_extent_change", "source_kind": source_kind})
                continue
            start = base + logical
            end = start + len(before)
            if any(offset in touched for offset in range(start, end)):
                rejected.append({"abs": address, "reason": "overlapping_record_range", "source_kind": source_kind})
                continue
            # Prefix/metadata/control bytes are immutable.  A tagged battle
            # row has metadata_hex followed by control_prefix_hex; scenario
            # and body-only routes only use control_prefix_hex.
            prefix_hex = str(contract.get("metadata_hex") or "") + str(contract.get("control_prefix_hex") or "")
            try:
                prefix = bytes.fromhex(prefix_hex)
            except ValueError:
                prefix = b""
            if prefix and (not before.startswith(prefix) or not after.startswith(prefix)):
                rejected.append({"abs": address, "reason": "control_or_metadata_prefix_changed", "source_kind": source_kind})
                continue
            before_boundary = boundary_signature(main_bytes, logical + len(before))
            candidate[start:end] = after
            after_boundary = boundary_signature(bytes(candidate), logical + len(before))
            if before_boundary != after_boundary:
                candidate[start:end] = before
                rejected.append({"abs": address, "reason": "following_boundary_changed", "source_kind": source_kind})
                continue
            touched.update(range(start, end))
            selected_by_abs[address] = {
                "abs": address,
                "source_kind": source_kind,
                "route": contract.get("route"),
                "family": contract.get("family"),
                "record_extent": len(before),
                "changed": before != after,
                "control_prefix_hex": prefix_hex,
                "before_hex": before.hex().upper(),
                "after_hex": after.hex().upper(),
                "boundary": before_boundary,
                "proposed_text": item.get("proposed_korean") or item.get("text") or "",
                "storage_strategy": item.get("storage_strategy") or "native_stock",
            }

    changes = list(sorted(selected_by_abs.values(), key=lambda row: int(str(row["abs"]), 16)))
    update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)

    # Verify control/portrait prefixes for every contract, not only changed
    # rows.  This makes the requested "control rows remain" condition explicit.
    control_rows = 0
    control_rows_unchanged = 0
    control_failures: list[dict[str, object]] = []
    for address, contract in contracts.items():
        prefix_hex = str(contract.get("metadata_hex") or "") + str(contract.get("control_prefix_hex") or "")
        if not prefix_hex:
            continue
        try:
            prefix = bytes.fromhex(prefix_hex)
        except ValueError:
            continue
        logical = int(address, 16)
        main_record = read_encoded_z_safe(main_bytes, base + logical, max_len=256)
        candidate_record = read_encoded_z_safe(candidate_bytes, base + logical, max_len=256)
        if main_record is None or candidate_record is None:
            continue
        control_rows += 1
        main_payload = bytes(main_record[0])
        candidate_payload = bytes(candidate_record[0])
        if main_payload.startswith(prefix) and candidate_payload.startswith(prefix):
            control_rows_unchanged += 1
        else:
            control_failures.append({"abs": address, "prefix_hex": prefix_hex})

    manifest = build_manifest(original_bytes, candidate_bytes, target_path=OUT_ROM)
    safety = audit_manifest(candidate_bytes, manifest, target_path=OUT_ROM)
    OUT_ROM.write_bytes(candidate_bytes)
    OUT_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    scenario_storage = load_json(STORAGE_SCENARIO)
    special_storage = load_json(STORAGE_SPECIAL)
    scenario_semantic_rows = sum(
        1
        for result_path in SCENARIO_REVIEW_RESULTS.glob("MR*_reviewed.csv")
        for row in csv.DictReader(result_path.open(encoding="utf-8-sig", newline=""))
        if str(row.get("review_status") or "") == "llm_retranslated_structural_hold"
    )
    special_rows = int((special_storage.get("counts") or {}).get("rows") or 0)
    selected_count = len(changes)
    report = {
        "schema_version": 1,
        "artifact": "translation-review-static-test-candidate/v1",
        "generated_by": "tools/build_translation_review_test_rom.py",
        "read_only_test_artifact": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "promotion_allowed": False,
        "promotion_block_reason": "static test ROM only; runtime/BizHawk approval was not performed",
        "main_unchanged": sha(MAIN.read_bytes()) == sha(main_bytes),
        "saveram_changed": False,
        "source_main_sha256": sha(main_bytes),
        "candidate_sha256": sha(candidate_bytes),
        "selected": changes,
        "rejected": rejected,
        "counts": {
            "selected_rows": len(changes),
            "changed_rows": sum(bool(row["changed"]) for row in changes),
            "unchanged_reused_rows": sum(not bool(row["changed"]) for row in changes),
            "rejected_rows": len(rejected),
            "scenario_selected_rows": sum(row["source_kind"] == "scenario" for row in changes),
            "special_selected_rows": sum(row["source_kind"] == "special" for row in changes),
            "control_rows_checked": control_rows,
            "control_rows_prefix_preserved": control_rows_unchanged,
            "control_prefix_failures": len(control_failures),
            "scenario_storage_rows_not_selected": int((scenario_storage.get("counts") or {}).get("capacity_or_dictionary_holds") or 0),
            "special_storage_rows_not_selected": int((special_storage.get("counts") or {}).get("dictionary_or_capacity_holds") or 0),
            "candidate_hard_failures": int((safety.get("counts") or {}).get("hard_failures") or 0),
            "candidate_review_items": int((safety.get("counts") or {}).get("review_items") or 0),
        },
        "retranslated_scope": {
            "scenario_semantic_rows": scenario_semantic_rows,
            "scenario_selected_rows": sum(row["source_kind"] == "scenario" for row in changes),
            "scenario_deferred_rows": max(0, scenario_semantic_rows - sum(row["source_kind"] == "scenario" for row in changes)),
            "special_route_rows": special_rows,
            "special_selected_rows": sum(row["source_kind"] == "special" for row in changes),
            "special_deferred_rows": max(0, special_rows - sum(row["source_kind"] == "special" for row in changes)),
            "total_retranslated_rows_considered": scenario_semantic_rows + special_rows,
            "total_selected_rows": selected_count,
            "total_deferred_rows": max(0, scenario_semantic_rows + special_rows - selected_count),
            "deferred_reason": "capacity/dictionary limits, unproven route, or structural quarantine; no unsafe fallback encoding used",
        },
        "candidate_safety": safety,
        "candidate_manifest": str(OUT_MANIFEST.relative_to(ROOT)).replace("\\", "/"),
        "control_prefix_failures": control_failures,
        "deferred_scope": {
            "scenario_storage_audit": str(STORAGE_SCENARIO.relative_to(ROOT)).replace("\\", "/"),
            "special_storage_audit": str(STORAGE_SPECIAL.relative_to(ROOT)).replace("\\", "/"),
            "reason": "remaining retranslated rows exceed native body capacity, require unproven dictionary/ext3, or have unresolved route/structural quarantine",
            "control_rows_policy": "metadata/control prefixes remain byte-exact; no global renderer or automatic wrap changes",
        },
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok = (
        report["main_unchanged"]
        and not report["saveram_changed"]
        and len(rejected) == 0
        and not control_failures
        and int((safety.get("counts") or {}).get("hard_failures") or 0) == 0
        and int((safety.get("counts") or {}).get("review_items") or 0) == 0
    )
    print(json.dumps({"ok": ok, "candidate": str(OUT_ROM), "counts": report["counts"], "report": str(OUT_REPORT)}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
