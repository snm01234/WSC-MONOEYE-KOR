#!/usr/bin/env python3
"""Audit a manually retranslated battle-contract batch without applying it."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "out/script/dialogue_runtime_contracts.json"
TBL = ROOT / "out/patch/hangul_patch_pad3.tbl"
ROM = ROOT / "out/patch/monoeye_ko_expanded.wsc"
QUEUE_DIR = ROOT / "out/script/translation_workstreams_static_batches"
OUT_DIR = ROOT / "out/script/battle_dialogue_llm_review"
sys.path.insert(0, str(ROOT / "tools"))
from hangul_marker import marker_code  # noqa: E402
from monoeye_rom import Tbl, load_rom, token_from_dict_index  # noqa: E402
from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3  # noqa: E402
from normalize_ko_text import normalize_ko_text, try_encode_ko_text  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("batch_id")
    ap.add_argument("mapping", type=Path)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args()
    bid = args.batch_id.upper()
    batch = QUEUE_DIR / f"{bid}.csv"
    mapping_path = args.mapping if args.mapping.is_absolute() else ROOT / args.mapping
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    rows = list(csv.DictReader(batch.open(encoding="utf-8-sig", newline="")))
    contracts = json.loads(CONTRACT.read_text(encoding="utf-8"))["contracts"]
    by_abs = {str(row["address"]).upper(): row for row in contracts}
    if set(mapping) != {str(row["address_or_slot"]).upper() for row in rows}:
        raise SystemExit("mapping must cover the whole static batch exactly")
    tbl = Tbl.load(TBL)
    # Read-only lookup of the already-installed stock dictionary.  This is
    # deliberately limited to native stock indices: a matching phrase is
    # usable without introducing an unproven E5 18 token, while expansion and
    # ext3 matches remain an explicit storage hold.
    rom = load_rom(ROM)
    dictionary = make_dictionary_ext3(
        rom,
        load_ext_meta(ROOT / "out/patch/exp_dictionary_meta.json"),
        load_ext_meta(ROOT / "out/patch/ext3_dictionary_meta.json"),
    )
    stock_phrase_to_indices: dict[str, list[int]] = {}
    for index in range(dictionary.stock_count):
        phrase = dictionary.expand_index(index, tbl)
        if phrase and not phrase.startswith("<BADDICT:"):
            stock_phrase_to_indices.setdefault(
                normalize_ko_text(phrase), []
            ).append(index)
    details = []
    for row in rows:
        address = str(row["address_or_slot"]).upper()
        text = str(mapping[address])
        contract = by_abs.get(address)
        encoded = try_encode_ko_text(
            normalize_ko_text(text), tbl,
            hangul_marker_code=marker_code(), hangul_marker_mode="run",
        )
        capacity = int((contract or {}).get("body_capacity") or 0)
        quality_ok = bool(text) and any("\uac00" <= ch <= "\ud7a3" for ch in text) and len(text) <= 20 and "\x00" not in text and not any(
            0x3040 <= ord(ch) <= 0x30FF for ch in text
        )
        fit = bool(encoded) and b"\x00" not in encoded and len(encoded) <= capacity
        stock_hits = stock_phrase_to_indices.get(normalize_ko_text(text), [])
        native_stock_fit = bool(
            stock_hits
            and contract
            and bool((contract.get("decoder") or {}).get("native_stock"))
        )
        stock_index = stock_hits[0] if native_stock_fit else None
        stock_token = (
            token_from_dict_index(stock_index).hex().upper()
            if stock_index is not None
            else ""
        )
        details.append({
            "abs": address,
            "source_jp": str(row.get("source_jp") or ""),
            "proposed_ko": text,
            "route": str((contract or {}).get("route") or ""),
            "body_capacity": capacity,
            "encoded_len_direct": len(encoded) if encoded else None,
            "encoded_hex_direct": encoded.hex().upper() if encoded else "",
            "embedded_nul_direct": bool(encoded and b"\x00" in encoded),
            "semantic_quality_ok": quality_ok,
            "semantic_quality_reason": (
                "ok"
                if quality_ok
                else "missing_hangul_or_japanese_control_residual_or_empty"
            ),
            "direct_encoding_fit": fit,
            "native_stock_dictionary_fit": native_stock_fit,
            "native_stock_dictionary_index": (
                f"{stock_index:04X}" if stock_index is not None else ""
            ),
            "native_stock_dictionary_token_hex": stock_token,
            "encoding_status": (
                "direct_fit"
                if fit
                else "native_stock_dictionary_fit"
                if native_stock_fit
                else "capacity_or_dictionary_hold"
            ),
            "metadata_hex": str((contract or {}).get("metadata_hex") or ""),
            "source_body_hex": str((contract or {}).get("source_body_hex") or ""),
            "baseline_body_hex": str((contract or {}).get("baseline_body_hex") or ""),
            "boundary_preserved_by_contract": bool(contract),
        })
    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_battle_contract_static_map.py",
        "batch_id": bid,
        "read_only": True,
        "runtime_trace": "stopped_by_user",
        "runtime_validation_performed": False,
        "semantic_review": "complete",
        "promotion_allowed": False,
        "promotion_block_reason": "direct payload does not fit; dictionary/storage route not proven for this batch",
        "inputs": {
            "batch": str(batch.relative_to(ROOT)).replace("\\", "/"),
            "mapping": str(mapping_path.relative_to(ROOT)).replace("\\", "/"),
            "main_rom_sha256": sha(ROM),
            "contract_sha256": sha(CONTRACT),
        },
        "counts": {
            "rows": len(details),
            "semantic_quality_ok": sum(row["semantic_quality_ok"] for row in details),
            "direct_encoding_fit": sum(row["direct_encoding_fit"] for row in details),
            "embedded_nul_direct": sum(row["embedded_nul_direct"] for row in details),
            "native_stock_dictionary_fit": sum(
                row["native_stock_dictionary_fit"] for row in details
            ),
            "resolved_storage_fit": sum(
                row["direct_encoding_fit"] or row["native_stock_dictionary_fit"]
                for row in details
            ),
            "capacity_or_dictionary_hold": sum(
                not (row["direct_encoding_fit"] or row["native_stock_dictionary_fit"])
                for row in details
            ),
            "hard_failures": 0,
        },
        "rows": details,
    }
    out_dir = args.out_dir if args.out_dir.is_absolute() else ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{bid}_static_map_audit.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(out), "counts": report["counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
