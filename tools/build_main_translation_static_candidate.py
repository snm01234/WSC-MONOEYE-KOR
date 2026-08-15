#!/usr/bin/env python3
"""Build a non-promotable contract-aware candidate from semantic-complete rows.

Only active ``scenario_first`` records with a completed source-grounded review
are considered.  Every accepted payload must fit its existing body capacity;
the record prefix, extent, terminator, separator NULs, and following boundary
are preserved byte-for-byte.  This is a static preclear artifact only: no
runtime validation or promotion is implied.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from dialogue_runtime_contracts import boundary_signature, build_manifest, audit_manifest  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base, update_ws_checksum  # noqa: E402
from normalize_ko_text import is_low_quality_ko, normalize_ko_text, try_encode_ko_text  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
RESULT_DIR = ROOT / "out/script/main_translation_llm_review/results"
CONTRACT_PATH = ROOT / "out/script/dialogue_runtime_contracts.json"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT_ROM = ROOT / "out/patch/main_translation_static_candidate.wsc"
OUT_REPORT = ROOT / "out/patch/main_translation_static_candidate.json"
OUT_MANIFEST = ROOT / "out/script/main_translation_static_candidate_contracts.json"

JP_SYLLABARY = re.compile(r"[\u3040-\u30ff]")


def translation_quality_reason(text: str) -> str | None:
    """Reject values that are structurally encodable but not Korean output.

    A static contract preclear must not turn Japanese punctuation/kana or an
    unmarked garbage/stub value into a candidate merely because its bytes fit.
    Full semantic review remains a separate task; this is only a fail-closed
    boundary before any ROM candidate is produced.
    """
    normalized = normalize_ko_text(text)
    if not normalized:
        return "empty_translation"
    if JP_SYLLABARY.search(normalized):
        return "japanese_syllabary_residual"
    # Candidate rows are scenario text.  A non-empty Korean run is required;
    # punctuation-only values are retained in the review queue instead of
    # being silently promoted into the static candidate.
    if not re.search(r"[\uac00-\ud7a3]", normalized):
        return "no_hangul_text"
    if is_low_quality_ko(normalized):
        return "low_quality_translation"
    return None


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_semantic_results() -> dict[str, str]:
    reviewed: dict[str, str] = {}
    for result_path in RESULT_DIR.glob("MR*_reviewed.csv"):
        manifest_path = result_path.with_name(result_path.name.replace("_reviewed.csv", "_result_manifest.json"))
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("semantic_review") != "complete":
            continue
        with result_path.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                reviewed[str(row["abs"]).upper()] = str(row.get("proposed_ko") or "")
    return reviewed


def main() -> None:
    main_bytes = bytes(load_rom(MAIN))
    original_bytes = bytes(load_rom(ORIGINAL))
    candidate = bytearray(main_bytes)
    base = stock_base(main_bytes)
    contracts = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))["contracts"]
    reviewed = read_semantic_results()
    tbl = Tbl.load(TBL_PATH)
    selected: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    touched: set[int] = set()
    for address, contract in sorted(
        ((str(row["address"]).upper(), row) for row in contracts),
        key=lambda item: int(item[0], 16),
    ):
        if contract.get("status") != "active" or contract.get("route") != "scenario_first":
            continue
        text = reviewed.get(address)
        if not text:
            rejected.append({"abs": address, "reason": "semantic_review_pending_or_missing"})
            continue
        quality_reason = translation_quality_reason(text)
        if quality_reason:
            rejected.append({"abs": address, "reason": quality_reason})
            continue
        encoded = try_encode_ko_text(
            normalize_ko_text(text), tbl,
            hangul_marker_code=marker_code(), hangul_marker_mode="run",
        )
        capacity = int(contract["body_capacity"])
        if not encoded or b"\x00" in encoded or len(encoded) > capacity:
            rejected.append({
                "abs": address,
                "reason": "reviewed_payload_does_not_fit",
                "capacity": capacity,
                "encoded_len": len(encoded) if encoded else None,
            })
            continue
        logical = int(address, 16)
        payload_result = read_encoded_z_safe(main_bytes, base + logical, max_len=256)
        if payload_result is None:
            rejected.append({"abs": address, "reason": "unreadable_record"})
            continue
        payload = bytes(payload_result[0])
        prefix = bytes.fromhex(str(contract.get("control_prefix_hex") or ""))
        extent = int(contract["record_extent"])
        if not payload.startswith(prefix) or len(payload) != extent:
            rejected.append({"abs": address, "reason": "prefix_or_extent_drift"})
            continue
        start = base + logical
        end = start + len(payload)
        if any(offset in touched for offset in range(start, end)):
            rejected.append({"abs": address, "reason": "overlapping_record_range"})
            continue
        body = bytes(encoded) + (b"\x01" * (capacity - len(encoded)))
        replacement = prefix + body
        if len(replacement) != len(payload):
            rejected.append({"abs": address, "reason": "extent_change"})
            continue
        before_boundary = boundary_signature(main_bytes, logical + len(payload))
        candidate[start:end] = replacement
        after_boundary = boundary_signature(bytes(candidate), logical + len(payload))
        if before_boundary != after_boundary:
            raise RuntimeError(f"boundary changed at {address}")
        touched.update(range(start, end))
        selected.append({
            "abs": address,
            "text": text,
            "encoded_hex": bytes(encoded).hex().upper(),
            "body_capacity": capacity,
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
        "generated_by": "tools/build_main_translation_static_candidate.py",
        "promotion_allowed": False,
        "runtime_validation_required": True,
        "main_unchanged": sha_bytes(MAIN.read_bytes()) == sha_bytes(main_bytes),
        "saveram_changed": False,
        "semantic_complete_rows_considered": len(reviewed),
        "selected": selected,
        "rejected": rejected,
        "counts": {
            "selected": len(selected),
            "rejected": len(rejected),
            "candidate_hard_failures": safety["counts"]["hard_failures"],
        },
        "candidate_safety": safety,
        "candidate_manifest": str(OUT_MANIFEST.relative_to(ROOT)).replace("\\", "/"),
        "main_sha256": sha_bytes(main_bytes),
        "candidate_sha256": sha_bytes(candidate_bytes),
        "note": "Static contract preclear only; do not promote before user runtime approval.",
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "selected": len(selected),
        "rejected": len(rejected),
        "candidate_hard_failures": safety["counts"]["hard_failures"],
        "candidate": str(OUT_ROM),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
