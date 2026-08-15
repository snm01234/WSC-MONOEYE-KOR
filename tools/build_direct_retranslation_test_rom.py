#!/usr/bin/env python3
"""Apply current-TIP retranslation text directly into existing record extents.

Only active runtime contracts are considered.  A row is written when its
native TBL payload fits the existing body capacity and all static rules hold;
otherwise it is retained in the exclusion manifest.  Prefix/portrait metadata,
terminators, separator NULs, and following control boundaries are never
rewritten.  This is a test ROM, not a promotion.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from dialogue_runtime_contracts import (  # noqa: E402
    audit_manifest,
    boundary_signature,
    build_manifest,
    physical_widths,
    scan_portals,
    semantic_widths,
)
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import load_rom, read_encoded_z_safe, stock_base, update_ws_checksum  # noqa: E402
from normalize_ko_text import is_low_quality_ko, normalize_ko_text, try_encode_ko_text  # noqa: E402
from monoeye_rom import Tbl  # noqa: E402

csv.field_size_limit(100_000_000)

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
CONTRACT = ROOT / "out/script/dialogue_runtime_contracts.json"
SCENARIO_RESULTS = ROOT / "out/script/main_translation_llm_review/results"
SPECIAL_STORAGE = ROOT / "out/script/special_route_storage_static_audit.json"
SCENARIO_STORAGE = ROOT / "out/script/scenario_storage_static_audit.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT_ROM = ROOT / "out/patch/direct_retranslation_static_test_candidate.wsc"
OUT_MANIFEST = ROOT / "out/script/direct_retranslation_static_test_candidate_contracts.json"
OUT_REPORT = ROOT / "out/patch/direct_retranslation_static_test_candidate.json"

JP_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
CONTROL_TAG_RE = re.compile(r"<(?:E62F|[0-9A-Fa-f]{2,8})>")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def scenario_texts() -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for path in sorted(SCENARIO_RESULTS.glob("MR*_reviewed.csv")):
        for row in read_csv(path):
            address = str(row.get("abs") or "").upper()
            if not address:
                continue
            if str(row.get("new_review_status") or row.get("review_status") or "") != "llm_retranslated_structural_hold":
                continue
            text = str(row.get("proposed_ko") or "")
            if text:
                rows[address] = {
                    "text": text,
                    "source_kind": "scenario",
                    "source_file": str(path.relative_to(ROOT).as_posix()),
                    "bundle_id": row.get("bundle_id") or "",
                    "route": row.get("route") or "",
                }
    return rows


def special_texts() -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    storage = load_json(SPECIAL_STORAGE)
    for row in storage.get("rows") or []:
        address = str(row.get("abs") or "").upper()
        text = str(row.get("proposed_korean") or "")
        if not address or not text:
            continue
        rows[address] = {
            "text": text,
            "source_kind": "special",
            "source_file": str(SPECIAL_STORAGE.relative_to(ROOT).as_posix()),
            "bundle_id": row.get("batch_id") or "",
            "route": row.get("route") or "",
            "semantic_quality_ok": bool(row.get("semantic_quality_ok")),
        }
    return rows


def main() -> int:
    main_bytes = bytes(load_rom(MAIN))
    original_bytes = bytes(load_rom(ORIGINAL))
    candidate = bytearray(main_bytes)
    base = stock_base(main_bytes)
    tbl = Tbl.load(TBL_PATH)
    marker = marker_code()
    marker_bytes = bytes([(marker >> 8) & 0xFF, marker & 0xFF])
    contracts = {
        str(row.get("address") or "").upper(): row
        for row in load_json(CONTRACT).get("contracts") or []
    }
    sources = {}
    sources.update(scenario_texts())
    for address, row in special_texts().items():
        sources.setdefault(address, row)

    selected: list[dict[str, object]] = []
    excluded: list[dict[str, object]] = []
    touched: set[int] = set()
    for address, source in sorted(sources.items(), key=lambda item: int(item[0], 16)):
        contract = contracts.get(address)
        text = str(source.get("text") or "")
        common = {
            "abs": address,
            "source_kind": source.get("source_kind"),
            "source_file": source.get("source_file"),
            "route": contract.get("route") if contract else source.get("route"),
            "bundle_id": source.get("bundle_id") or contract.get("bundle_id") if contract else source.get("bundle_id"),
            "text": text,
        }
        if not contract:
            excluded.append({**common, "reason": "contract_missing"})
            continue
        if str(contract.get("status") or "") != "active":
            excluded.append({**common, "reason": "contract_not_active"})
            continue
        route = str(contract.get("route") or "")
        if route not in {"scenario_first", "scenario_continuation", "battle_tagged", "battle_body_only", "id_first", "id_continuation"}:
            excluded.append({**common, "reason": "route_not_static_dialogue_route"})
            continue
        if source.get("source_kind") == "special" and source.get("semantic_quality_ok") is False:
            excluded.append({**common, "reason": "semantic_quality_quarantine"})
            continue
        normalized = normalize_ko_text(text)
        if not normalized:
            excluded.append({**common, "reason": "empty_translation"})
            continue
        if JP_RE.search(normalized):
            excluded.append({**common, "reason": "japanese_residual"})
            continue
        if is_low_quality_ko(normalized):
            excluded.append({**common, "reason": "low_quality_translation"})
            continue
        semantic = semantic_widths(normalized)
        physical = physical_widths(normalized)
        if any(width > 20 for width in semantic):
            excluded.append({**common, "reason": "semantic_width_over_20", "semantic_widths": semantic})
            continue
        if bool(contract.get("width_enforced")) and any(width > 20 for width in physical):
            excluded.append({**common, "reason": "physical_width_over_20", "physical_widths": physical})
            continue
        encoded = try_encode_ko_text(normalized, tbl, hangul_marker_code=marker, hangul_marker_mode="run")
        if not encoded:
            excluded.append({**common, "reason": "not_encodable_in_native_tbl"})
            continue
        if b"\x00" in encoded:
            excluded.append({**common, "reason": "embedded_nul"})
            continue
        portals = scan_portals(encoded)
        if any(item.get("kind") == "compact3" for item in portals):
            excluded.append({**common, "reason": "compact3_forbidden"})
            continue
        decoder = contract.get("decoder") or {}
        if any(item.get("kind") in {"ext3", "truncated_ext3"} for item in portals) and not bool(decoder.get("ext3")):
            excluded.append({**common, "reason": "ext3_not_proven_for_route", "portals": portals})
            continue
        if route in {"battle_body_only", "id_continuation"} and marker_bytes in encoded and not bool(decoder.get("ext3")):
            excluded.append({**common, "reason": "e518_marker_forbidden_on_special_continuation"})
            continue
        capacity = int(contract.get("body_capacity") or 0)
        if len(encoded) > capacity:
            excluded.append({**common, "reason": "body_capacity_exceeded", "capacity": capacity, "encoded_len": len(encoded)})
            continue
        logical = int(address, 16)
        record = read_encoded_z_safe(main_bytes, base + logical, max_len=256)
        if record is None:
            excluded.append({**common, "reason": "unreadable_record"})
            continue
        before = bytes(record[0])
        extent = int(contract.get("record_extent") or 0)
        if len(before) != extent:
            excluded.append({**common, "reason": "record_extent_mismatch"})
            continue
        prefix_hex = str(contract.get("metadata_hex") or "") + str(contract.get("control_prefix_hex") or "")
        try:
            prefix = bytes.fromhex(prefix_hex)
        except ValueError:
            excluded.append({**common, "reason": "invalid_control_prefix_hex"})
            continue
        if prefix and not before.startswith(prefix):
            excluded.append({**common, "reason": "current_control_prefix_mismatch"})
            continue
        replacement = prefix + bytes(encoded) + (b"\x01" * (capacity - len(encoded)))
        if len(replacement) != len(before):
            excluded.append({**common, "reason": "replacement_extent_mismatch"})
            continue
        start = base + logical
        end = start + len(before)
        if any(offset in touched for offset in range(start, end)):
            excluded.append({**common, "reason": "overlapping_record_range"})
            continue
        before_boundary = boundary_signature(main_bytes, logical + len(before))
        candidate[start:end] = replacement
        after_boundary = boundary_signature(bytes(candidate), logical + len(before))
        if before_boundary != after_boundary:
            candidate[start:end] = before
            excluded.append({**common, "reason": "following_boundary_changed"})
            continue
        touched.update(range(start, end))
        selected.append({
            **common,
            "encoded_hex": bytes(encoded).hex().upper(),
            "before_hex": before.hex().upper(),
            "after_hex": replacement.hex().upper(),
            "record_extent": len(before),
            "body_capacity": capacity,
            "changed": before != replacement,
            "control_prefix_hex": prefix_hex,
            "semantic_widths": semantic,
            "physical_widths": physical,
            "boundary": before_boundary,
        })

    update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    manifest = build_manifest(original_bytes, candidate_bytes, target_path=OUT_ROM)
    safety = audit_manifest(candidate_bytes, manifest, target_path=OUT_ROM)
    OUT_ROM.write_bytes(candidate_bytes)
    OUT_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    storage_scenario = load_json(SCENARIO_STORAGE)
    storage_special = load_json(SPECIAL_STORAGE)
    exclusion_counts = Counter(str(row.get("reason") or "") for row in excluded)
    report = {
        "schema_version": 1,
        "artifact": "direct-retranslation-static-test-candidate/v1",
        "generated_by": "tools/build_direct_retranslation_test_rom.py",
        "read_only_test_artifact": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "promotion_allowed": False,
        "promotion_block_reason": "direct overwrite test candidate; dynamic runtime approval not performed",
        "main_unchanged": sha(MAIN.read_bytes()) == sha(main_bytes),
        "saveram_changed": False,
        "source_main_sha256": sha(main_bytes),
        "candidate_sha256": sha(candidate_bytes),
        "selected": selected,
        "excluded": excluded,
        "counts": {
            "source_rows": len(sources),
            "selected_rows": len(selected),
            "changed_rows": sum(bool(row.get("changed")) for row in selected),
            "unchanged_rows": sum(not bool(row.get("changed")) for row in selected),
            "excluded_rows": len(excluded),
            "scenario_selected_rows": sum(row.get("source_kind") == "scenario" for row in selected),
            "special_selected_rows": sum(row.get("source_kind") == "special" for row in selected),
            "candidate_hard_failures": int((safety.get("counts") or {}).get("hard_failures") or 0),
            "candidate_review_items": int((safety.get("counts") or {}).get("review_items") or 0),
            "scenario_storage_rows": int((storage_scenario.get("counts") or {}).get("rows") or 0),
            "special_storage_rows": int((storage_special.get("counts") or {}).get("rows") or 0),
        },
        "exclusion_reason_counts": dict(sorted(exclusion_counts.items())),
        "candidate_safety": safety,
        "candidate_manifest": str(OUT_MANIFEST.relative_to(ROOT)).replace("\\", "/"),
        "policy": {
            "overwrite_scope": "existing record body only; prefix/metadata and record extent are fixed",
            "control_rows": "control prefix/portrait metadata remains byte-exact; rows failing static rules are untouched",
            "static_exclusions": "inactive/quarantine contracts, Japanese or low-quality text, capacity overflow, NUL, unproven ext3/compact3, width overflow, or boundary drift",
            "storage_audit_references": [str(SCENARIO_STORAGE.relative_to(ROOT)).replace("\\", "/"), str(SPECIAL_STORAGE.relative_to(ROOT)).replace("\\", "/")],
        },
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ok = (
        report["main_unchanged"]
        and not report["saveram_changed"]
        and int((safety.get("counts") or {}).get("hard_failures") or 0) == 0
        and int((safety.get("counts") or {}).get("review_items") or 0) == 0
    )
    print(json.dumps({"ok": ok, "candidate": str(OUT_ROM), "counts": report["counts"], "exclusion_reason_counts": report["exclusion_reason_counts"]}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
