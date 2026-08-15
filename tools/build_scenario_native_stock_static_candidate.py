#!/usr/bin/env python3
"""Build a non-promotable scenario candidate using exact native stock tokens."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CONTRACT = ROOT / "out/script/dialogue_runtime_contracts.json"
STORAGE = ROOT / "out/script/scenario_storage_static_audit.json"
OUT_ROM = ROOT / "out/patch/scenario_native_stock_static_candidate.wsc"
OUT_REPORT = ROOT / "out/patch/scenario_native_stock_static_candidate.json"
OUT_MANIFEST = ROOT / "out/script/scenario_native_stock_static_candidate_contracts.json"
sys.path.insert(0, str(ROOT / "tools"))
from dialogue_runtime_contracts import audit_manifest, boundary_signature, build_manifest  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base, update_ws_checksum  # noqa: E402
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    main_bytes = bytes(load_rom(MAIN))
    original_bytes = bytes(load_rom(ORIGINAL))
    storage = json.loads(STORAGE.read_text(encoding="utf-8"))
    contracts = json.loads(CONTRACT.read_text(encoding="utf-8")).get("contracts") or []
    by_address = {
        str(row.get("address") or "").upper(): row
        for row in contracts
        if row.get("status") == "active" and row.get("route") == "scenario_first"
    }
    candidate = bytearray(main_bytes)
    base = stock_base(main_bytes)
    tbl = Tbl.load(ROOT / "out/patch/hangul_patch_pad3.tbl")
    selected: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    touched: set[int] = set()
    for item in storage.get("rows") or []:
        if item.get("decision") not in {
            "native_stock_token_candidate_structural_hold",
            "direct_payload_candidate_structural_hold",
        }:
            continue
        address = str(item.get("abs") or "").upper()
        contract = by_address.get(address)
        if not contract:
            rejected.append({"abs": address, "reason": "active_scenario_first_contract_missing"})
            continue
        storage_strategy = str(item.get("decision") or "")
        if storage_strategy == "direct_payload_candidate_structural_hold":
            encoded = try_encode_ko_text(
                normalize_ko_text(str(item.get("proposed_korean") or "")), tbl,
                hangul_marker_code=marker_code(), hangul_marker_mode="run",
            )
            token = encoded or b""
            token_hex = token.hex().upper()
        else:
            token_hex = str(item.get("native_stock_dictionary_token_hex") or "")
            try:
                token = bytes.fromhex(token_hex)
            except ValueError:
                rejected.append({"abs": address, "reason": "invalid_native_token_hex"})
                continue
        capacity = int(contract.get("body_capacity") or 0)
        if not token or b"\x00" in token or len(token) > capacity:
            rejected.append({"abs": address, "reason": "native_token_capacity_or_nul"})
            continue
        logical = int(address, 16)
        payload_result = read_encoded_z_safe(main_bytes, base + logical, max_len=256)
        if payload_result is None:
            rejected.append({"abs": address, "reason": "unreadable_record"})
            continue
        payload = bytes(payload_result[0])
        prefix = bytes.fromhex(str(contract.get("control_prefix_hex") or ""))
        extent = int(contract.get("record_extent") or 0)
        if len(payload) != extent or not payload.startswith(prefix):
            rejected.append({"abs": address, "reason": "prefix_or_extent_drift"})
            continue
        start = base + logical
        end = start + len(payload)
        if any(offset in touched for offset in range(start, end)):
            rejected.append({"abs": address, "reason": "overlapping_record_range"})
            continue
        body = token + (b"\x01" * (capacity - len(token)))
        replacement = prefix + body
        if len(replacement) != len(payload):
            rejected.append({"abs": address, "reason": "extent_change"})
            continue
        before_boundary = boundary_signature(main_bytes, logical + len(payload))
        candidate[start:end] = replacement
        after_boundary = boundary_signature(bytes(candidate), logical + len(payload))
        if before_boundary != after_boundary:
            rejected.append({"abs": address, "reason": "boundary_change"})
            candidate[start:end] = payload
            continue
        touched.update(range(start, end))
        selected.append({
            "abs": address,
            "proposed_korean": item.get("proposed_korean"),
            "storage_strategy": storage_strategy,
            "native_stock_dictionary_index": item.get("native_stock_dictionary_index"),
            "token_hex": token_hex,
            "record_extent": len(payload),
            "before_hex": payload.hex().upper(),
            "after_hex": replacement.hex().upper(),
            "boundary": before_boundary,
        })
    update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    manifest = build_manifest(original_bytes, candidate_bytes, target_path=OUT_ROM)
    safety = audit_manifest(candidate_bytes, manifest, target_path=OUT_ROM)
    OUT_ROM.write_bytes(candidate_bytes)
    OUT_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_scenario_native_stock_static_candidate.py",
        "read_only": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "promotion_allowed": False,
        "promotion_block_reason": "native stock static candidate only; runtime confirmation and user approval absent",
        "main_unchanged": sha(MAIN.read_bytes()) == sha(main_bytes),
        "saveram_changed": False,
        "selected": selected,
        "rejected": rejected,
        "counts": {
            "storage_rows_considered": sum(
                item.get("decision") in {
                    "native_stock_token_candidate_structural_hold",
                    "direct_payload_candidate_structural_hold",
                }
                for item in storage.get("rows") or []
            ),
            "native_stock_rows_considered": sum(
                item.get("decision") == "native_stock_token_candidate_structural_hold"
                for item in storage.get("rows") or []
            ),
            "direct_payload_rows_considered": sum(
                item.get("decision") == "direct_payload_candidate_structural_hold"
                for item in storage.get("rows") or []
            ),
            "selected": len(selected),
            "rejected": len(rejected),
            "candidate_hard_failures": safety["counts"]["hard_failures"],
            "candidate_review_items": safety["counts"].get("review_items", 0),
        },
        "candidate_safety": safety,
        "candidate_manifest": str(OUT_MANIFEST.relative_to(ROOT)).replace("\\", "/"),
        "main_sha256": sha(main_bytes),
        "candidate_sha256": sha(candidate_bytes),
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"candidate": str(OUT_ROM), "counts": report["counts"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
