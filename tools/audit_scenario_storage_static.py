#!/usr/bin/env python3
"""Find static stock-dictionary reuse opportunities for reviewed scenario rows."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
RESULT_DIR = ROOT / "out/script/main_translation_llm_review/results"
CONTRACT = ROOT / "out/script/dialogue_runtime_contracts.json"
ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
OUT = ROOT / "out/script/scenario_storage_static_audit.json"
sys.path.insert(0, str(ROOT / "tools"))
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import Tbl, load_rom, token_from_dict_index  # noqa: E402
from normalize_ko_text import is_low_quality_ko, normalize_ko_text, try_encode_ko_text  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    contracts = json.loads(CONTRACT.read_text(encoding="utf-8")).get("contracts") or []
    by_address = {
        str(row.get("address") or "").upper(): row
        for row in contracts
        if row.get("status") == "active" and row.get("route") == "scenario_first"
    }
    reviewed: dict[str, str] = {}
    for path in RESULT_DIR.glob("MR*_reviewed.csv"):
        manifest_path = path.with_name(path.name.replace("_reviewed.csv", "_result_manifest.json"))
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("semantic_review") != "complete":
            continue
        csv.field_size_limit(10**9)
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                reviewed[str(row.get("abs") or "").upper()] = str(row.get("proposed_ko") or "")

    rom = load_rom(ROM)
    tbl = Tbl.load(TBL)
    dictionary = make_dictionary_ext3(
        rom,
        load_ext_meta(ROOT / "out/patch/exp_dictionary_meta.json"),
        load_ext_meta(ROOT / "out/patch/ext3_dictionary_meta.json"),
    )
    stock_by_text: dict[str, list[int]] = {}
    for index in range(dictionary.stock_count):
        text = dictionary.expand_index(index, tbl)
        if text:
            stock_by_text.setdefault(normalize_ko_text(text), []).append(index)

    rows: list[dict[str, object]] = []
    for address, contract in sorted(by_address.items(), key=lambda item: int(item[0], 16)):
        text = reviewed.get(address, "")
        normalized = normalize_ko_text(text)
        encoded = try_encode_ko_text(
            normalized, tbl,
            hangul_marker_code=marker_code(), hangul_marker_mode="run",
        ) if text else None
        quality_ok = bool(text) and any("\uac00" <= ch <= "\ud7a3" for ch in text) and not is_low_quality_ko(text)
        capacity = int(contract.get("body_capacity") or 0)
        direct_fit = bool(quality_ok and encoded) and b"\x00" not in encoded and len(encoded) <= capacity
        hits = stock_by_text.get(normalized, []) if quality_ok else []
        stock_index = hits[0] if hits else None
        token = token_from_dict_index(stock_index) if stock_index is not None else b""
        stock_fit = bool(token) and b"\x00" not in token and len(token) <= capacity
        if not quality_ok:
            decision = "semantic_quality_quarantine"
        elif direct_fit:
            decision = "direct_payload_candidate_structural_hold"
        elif stock_fit:
            decision = "native_stock_token_candidate_structural_hold"
        else:
            decision = "capacity_or_dictionary_hold"
        rows.append({
            "abs": address,
            "bundle_id": contract.get("bundle_id"),
            "proposed_korean": text,
            "body_capacity": capacity,
            "direct_encoding_len": len(encoded) if encoded else None,
            "direct_encoding_fit": direct_fit,
            "native_stock_dictionary_index": f"{stock_index:04X}" if stock_index is not None else "",
            "native_stock_dictionary_token_hex": token.hex().upper(),
            "native_stock_dictionary_fit": stock_fit,
            "semantic_quality_ok": quality_ok,
            "decision": decision,
            "application_allowed": False,
        })
    counts = {
        "rows": len(rows),
        "semantic_quality_ok": sum(bool(row["semantic_quality_ok"]) for row in rows),
        "semantic_quality_quarantine": sum(row["decision"] == "semantic_quality_quarantine" for row in rows),
        "direct_payload_candidates": sum(row["decision"] == "direct_payload_candidate_structural_hold" for row in rows),
        "native_stock_candidates": sum(row["decision"] == "native_stock_token_candidate_structural_hold" for row in rows),
        "capacity_or_dictionary_holds": sum(row["decision"] == "capacity_or_dictionary_hold" for row in rows),
        "application_allowed": 0,
    }
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_scenario_storage_static.py",
        "read_only": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "promotion_allowed": False,
        "promotion_block_reason": "static storage plan only; no ROM/sheet application or runtime confirmation",
        "inputs": {
            "main_rom_sha256": sha(ROM),
            "contract_sha256": sha(CONTRACT),
            "tbl_sha256": sha(TBL),
        },
        "policy": {
            "scenario_first_ext3": "not allocated by this audit",
            "native_stock_reuse": "candidate only; record boundary audit still required",
            "legacy_translation_source": "not used",
        },
        "counts": counts,
        "rows": rows,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT.relative_to(ROOT)), "counts": counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
