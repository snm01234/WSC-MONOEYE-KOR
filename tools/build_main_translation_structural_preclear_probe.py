#!/usr/bin/env python3
"""Build a non-promotable probe for statically preclearable scenario first lines.

Only quarantined scenario-first rows whose reviewed Korean encodes into the
existing body capacity are touched.  Record prefix, extent, terminator and
next-control boundary must remain byte-exact.  The probe is never copied over
the main TIP and does not alter SaveRAM.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from dialogue_runtime_contracts import boundary_signature, build_manifest, audit_manifest  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import Tbl, load_rom, read_encoded_z_safe, stock_base, update_ws_checksum  # noqa: E402
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
RESULT_DIR = ROOT / "out/script/main_translation_llm_review/results"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT_ROM = ROOT / "out/patch/main_translation_structural_preclear_probe.wsc"
OUT_REPORT = ROOT / "out/patch/main_translation_structural_preclear_probe.json"
OUT_MANIFEST = ROOT / "out/script/main_translation_structural_preclear_probe_contracts.json"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_results() -> dict[str, str]:
    out: dict[str, str] = {}
    for p in RESULT_DIR.glob("MR*_reviewed.csv"):
        manifest_path = p.with_name(p.name.replace("_reviewed.csv", "_result_manifest.json"))
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("semantic_review") != "complete":
            continue
        with p.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                out[row["abs"].upper()] = row["proposed_ko"]
    return out


def main() -> None:
    main = bytes(load_rom(MAIN))
    original = bytes(load_rom(ORIGINAL))
    contracts = json.loads((ROOT / "out/script/dialogue_runtime_contracts.json").read_text(encoding="utf-8"))["contracts"]
    by_abs = {str(row["address"]).upper(): row for row in contracts}
    reviewed = read_results()
    tbl = Tbl.load(TBL)
    candidate = bytearray(main)
    base = stock_base(main)
    selected: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    for address, contract in sorted(by_abs.items()):
        if contract.get("status") != "quarantine" or contract.get("route") != "scenario_first":
            continue
        if "semantic width" not in str(contract.get("conflict") or ""):
            continue
        if any(p.get("kind") == "compact3" for p in contract.get("baseline_portals") or []):
            rejected.append({"abs": address, "reason": "compact3_route_requires_native_rewrite"})
            continue
        text = reviewed.get(address, "")
        encoded = try_encode_ko_text(
            normalize_ko_text(text), tbl,
            hangul_marker_code=marker_code(), hangul_marker_mode="run",
        ) if text else None
        capacity = int(contract["body_capacity"])
        if not encoded or b"\x00" in encoded or len(encoded) > capacity:
            rejected.append({"abs": address, "reason": "reviewed_payload_does_not_fit", "capacity": capacity, "encoded_len": len(encoded) if encoded else None})
            continue
        logical = int(address, 16)
        payload_result = read_encoded_z_safe(main, base + logical, max_len=256)
        if payload_result is None:
            rejected.append({"abs": address, "reason": "unreadable_record"})
            continue
        payload, term_file = bytes(payload_result[0]), int(payload_result[1])
        prefix = bytes.fromhex(str(contract.get("control_prefix_hex") or ""))
        if not payload.startswith(prefix) or len(payload) != int(contract["record_extent"]):
            rejected.append({"abs": address, "reason": "prefix_or_extent_drift"})
            continue
        body = bytes(encoded) + (b"\x01" * (capacity - len(encoded)))
        after = prefix + body
        if len(after) != len(payload):
            rejected.append({"abs": address, "reason": "extent_change"})
            continue
        before_boundary = boundary_signature(main, logical + len(payload))
        candidate[base + logical:base + logical + len(payload)] = after
        after_boundary = boundary_signature(bytes(candidate), logical + len(payload))
        if before_boundary != after_boundary:
            raise RuntimeError(f"boundary changed at {address}")
        selected.append({
            "abs": address, "text": text, "encoded_hex": bytes(encoded).hex().upper(),
            "body_capacity": capacity, "record_extent": len(payload),
            "before_hex": payload.hex().upper(), "after_hex": after.hex().upper(),
            "boundary": before_boundary,
        })
    update_ws_checksum(candidate)
    candidate_bytes = bytes(candidate)
    candidate_manifest = build_manifest(original, candidate_bytes, target_path=OUT_ROM)
    safety = audit_manifest(candidate_bytes, candidate_manifest, target_path=OUT_ROM)
    OUT_ROM.write_bytes(candidate_bytes)
    OUT_MANIFEST.write_text(json.dumps(candidate_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "schema_version": 1,
        "generated_by": "tools/build_main_translation_structural_preclear_probe.py",
        "promotion_allowed": False,
        "main_unchanged": sha(MAIN.read_bytes()) == sha(main),
        "saveram_changed": False,
        "selected": selected,
        "rejected": rejected,
        "counts": {
            "selected": len(selected), "rejected": len(rejected),
            "candidate_hard_failures": safety["counts"]["hard_failures"],
        },
        "candidate_safety": safety,
        "candidate_manifest": str(OUT_MANIFEST.relative_to(ROOT)).replace("\\", "/"),
        "main_sha256": sha(main), "candidate_sha256": sha(candidate_bytes),
        "runtime_validation_required": True,
        "note": "This probe only preclears statically fitting scenario-first records; it is not a promotion candidate until runtime validation."
    }
    OUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"selected": len(selected), "rejected": len(rejected), "candidate_hard_failures": safety["counts"]["hard_failures"], "candidate": str(OUT_ROM)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
