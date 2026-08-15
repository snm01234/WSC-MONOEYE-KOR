#!/usr/bin/env python3
"""Read-only audit of Japanese residue in the bank-75 display-name table."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from expand_dictionary import NAME75_RANGES, _walk_zstring_range
from mixed_residual_classification import is_japanese_character
from monoeye_rom import (
    Dictionary,
    Tbl,
    dict_index_from_token,
    is_dict_token,
    load_rom,
    read_encoded_z_safe,
    stock_base,
)



def has_japanese(text: str) -> bool:
    return any(is_japanese_character(character) for character in text)


def identity(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {"path": str(path.resolve()), "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def load_catalog() -> dict[str, list[dict[str, str]]]:
    found: dict[str, list[dict[str, str]]] = {}
    for path in sorted((ROOT / "data").glob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows: list[Any] = []
        if isinstance(doc, dict):
            for key in ("entries", "fragments", "lines", "strings", "overrides"):
                value = doc.get(key)
                if isinstance(value, list):
                    rows.extend(value)
        elif isinstance(doc, list):
            rows = doc
        for row in rows:
            if not isinstance(row, dict):
                continue
            jp, ko = row.get("jp"), row.get("ko")
            if isinstance(jp, str) and jp and isinstance(ko, str) and ko:
                found.setdefault(jp, []).append({"ko": ko, "source": str(path.relative_to(ROOT))})
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tip",
        type=Path,
        default=ROOT / "out/patch/monoeye_ko_expanded.wsc",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "out/patch/name75_untranslated_terms_audit.json",
    )
    args = parser.parse_args()

    tip_path = args.tip
    original_path = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
    tbl_path = ROOT / "out/patch/hangul_patch_pad3.tbl"
    out_path = args.out

    tip = bytes(load_rom(tip_path))
    original = bytes(load_rom(original_path))
    tbl = Tbl.load(tbl_path)
    d_orig = Dictionary(original)
    d_tip = make_dictionary_ext3(
        tip,
        load_ext_meta(ROOT / "out/patch/ext_dictionary_meta.json"),
        load_ext_meta(ROOT / "out/patch/ext3_dictionary_meta.json"),
    )
    catalogs = load_catalog()

    ptr_good_by_abs: dict[str, int] = {}
    mine_path = ROOT / "out/script/weapon_table_mine.json"
    if mine_path.is_file():
        mine = json.loads(mine_path.read_text(encoding="utf-8"))
        for entry in mine.get("entries", []):
            for site in entry.get("sites", []):
                ptr_good_by_abs[str(site.get("abs"))] = int(site.get("ptr_good") or 0)

    ui_by_index: dict[str, dict[str, Any]] = {}
    ui_path = ROOT / "out/script/ui_facing_term_candidates.json"
    if ui_path.is_file():
        ui = json.loads(ui_path.read_text(encoding="utf-8"))
        for bucket in ("safe_candidates", "glued_candidates"):
            for row in ui.get(bucket, []):
                ui_by_index[str(row.get("index"))] = {"bucket": bucket, **row}

    rows: list[dict[str, Any]] = []
    sb_tip = stock_base(tip)
    for lo, hi in NAME75_RANGES:
        for logical, original_payload, _kind in _walk_zstring_range(original, lo, hi, region="name75", max_len=64):
            got = read_encoded_z_safe(tip, sb_tip + logical, max_len=64)
            if not got:
                continue
            current_payload = bytes(got[0])
            original_text = d_orig.expand(original_payload, tbl)
            current_text = d_tip.expand(current_payload, tbl).rstrip("　 \t")
            if not has_japanese(current_text):
                continue
            exact_index = None
            if len(original_payload) == 2 and is_dict_token(original_payload[0]):
                exact_index = dict_index_from_token(original_payload[0], original_payload[1])
            index_hex = f"{exact_index:04X}" if exact_index is not None else None
            ui = ui_by_index.get(index_hex or "")
            ptr_good = ptr_good_by_abs.get(f"{logical:06X}", 0)
            likely_real = logical < 0x75E630 or ptr_good > 0
            rows.append(
                {
                    "abs": f"{logical:06X}",
                    "original_text": original_text,
                    "current_text": current_text,
                    "payload_bytes": len(original_payload),
                    "original_payload_hex": original_payload.hex().upper(),
                    "current_payload_hex": current_payload.hex().upper(),
                    "exact_dict_index": index_hex,
                    "exact_dict_current_text": d_tip.expand_index(exact_index, tbl) if exact_index is not None else None,
                    "catalog_translations": catalogs.get(original_text, []),
                    "ptr_good": ptr_good,
                    "likely_real_table_record": likely_real,
                    "ui_candidate": ui,
                }
            )

    likely = [row for row in rows if row["likely_real_table_record"]]
    exact = [row for row in likely if row["exact_dict_index"]]
    missing_catalog = [row for row in likely if not row["catalog_translations"]]
    searches = {}
    for term in ("ドレン", "セラ", "ロべルト", "ロベルト", "スナイパ", "ライフル"):
        searches[term] = [row for row in rows if term in row["original_text"] or term in row["current_text"]]

    dict_matches = []
    for index in range(d_orig.count):
        text = d_orig.expand_index(index, tbl)
        if any(term in text for term in ("スナイパ", "ライフル", "ドレン", "セラ", "ロべルト", "ロベルト")):
            dict_matches.append(
                {
                    "index": f"{index:04X}",
                    "original_text": text,
                    "current_text": d_tip.expand_index(index, tbl),
                    "ui_candidate": ui_by_index.get(f"{index:04X}"),
                }
            )

    part_rows: list[dict[str, Any]] = []
    for logical, original_payload, _kind in _walk_zstring_range(
        original, 0x76FD0B, 0x76FDE1, region="part76", max_len=64
    ):
        got = read_encoded_z_safe(tip, sb_tip + logical, max_len=64)
        if not got:
            continue
        current_payload = bytes(got[0])
        original_text = d_orig.expand(original_payload, tbl)
        current_text = d_tip.expand(current_payload, tbl).rstrip("　 \\t")
        part_rows.append(
            {
                "abs": f"{logical:06X}",
                "original_text": original_text,
                "current_text": current_text,
                "payload_bytes": len(original_payload),
                "japanese_residue": has_japanese(current_text),
            }
        )
    part_residue = [row for row in part_rows if row["japanese_residue"]]

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_name75_untranslated_terms.py",
        "read_only": True,
        "inputs": {"tip": identity(tip_path), "original": identity(original_path), "tbl": identity(tbl_path)},
        "counts": {
            "all_japanese_residue_records": len(rows),
            "likely_real_records": len(likely),
            "likely_real_exact_single_token_records": len(exact),
            "likely_real_missing_catalog_records": len(missing_catalog),
            "part76_records": len(part_rows),
            "part76_japanese_residue_records": len(part_residue),
        },
        "searches": searches,
        "dictionary_matches": dict_matches,
        "likely_real_records": likely,
        "all_records": rows,
        "part76_records": part_rows,
        "part76_japanese_residue": part_residue,
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], ensure_ascii=False, indent=2))
    print("dictionary matches:")
    for row in dict_matches:
        print(row)
    print("wrote", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
