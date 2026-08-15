#!/usr/bin/env python3
"""Audit Japanese-to-Korean proper-name mappings against the current main TIP.

The audit keeps three populations separate:
* curated JP->KO catalogs used by rebuilds;
* compressed dictionary entries in the current TIP;
* Original-derived encyclopedia/name records rendered by the current TIP.

Historical inputs are deliberately excluded.  The report is read-only with
respect to ROMs and is intended to accompany terminology source corrections.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from apply_ext_dict_unit import load_ext_meta, make_dictionary_ext3
from audit_gundam_terminology_standard import (
    dictionary_hits,
    entries as standard_entries,
    five_bank_dictionary_hits,
    find_bad,
    forbidden_index,
    rendered_record_hits,
    source_hits,
)
from expand_dictionary import _walk_zstring_range
from mixed_residual_classification import is_japanese_character
from monoeye_rom import Dictionary, Tbl, load_rom, read_encoded_z_safe, stock_base

MAIN = ROOT / "out/patch/monoeye_ko_expanded.wsc"
ORIGINAL = ROOT / "SD Gundam G Generation Mono-Eye Gundams.wsc"
TBL_PATH = ROOT / "out/patch/hangul_patch_pad3.tbl"
EXT_META = ROOT / "out/patch/exp_dictionary_meta.json"
EXT3_META = ROOT / "out/patch/ext3_dictionary_meta.json"
OUT = ROOT / "out/patch/main_tip_name_mapping_consistency_review_20260815.json"

PAIR_FILES = (
    "data/unit_names_ko.json",
    "data/proper_nouns_ko.json",
    "data/ui_proper_nouns_ko.json",
    "data/name75_terms_ko.json",
    "data/encyclopedia_character_batch01_ko.json",
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hiragana_to_katakana(text: str) -> str:
    return "".join(
        chr(ord(char) + 0x60) if "ぁ" <= char <= "ゖ" else char
        for char in text
    )


def jp_key(text: str) -> str:
    value = hiragana_to_katakana(unicodedata.normalize("NFKC", text))
    return re.sub(r"[\s・]+", "", value)


def ko_key(text: str) -> str:
    value = unicodedata.normalize("NFKC", text)
    return re.sub(r"[\s・·･]+", "", value)


def name_like(text: str) -> bool:
    value = hiragana_to_katakana(unicodedata.normalize("NFKC", text))
    katakana = sum("ァ" <= char <= "ヿ" for char in value)
    all_kanji = bool(value) and all(
        "一" <= char <= "龯" or char == "々" for char in value
    )
    sentence_markers = ("。", "！", "？", "、", "デス", "マス", "シタ", "スル", "ナイ")
    return (
        len(value) <= 30
        and (katakana >= 2 or all_kanji)
        and not any(marker in value for marker in sentence_markers)
    )


def add_pair(
    out: list[dict[str, Any]], jp: Any, ko: Any, source: str, metadata: dict[str, Any] | None = None
) -> None:
    if isinstance(jp, str) and jp and isinstance(ko, str) and ko and name_like(jp):
        out.append({"jp": jp, "ko": ko, "source": source, "metadata": metadata or {}})


def catalog_pairs() -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for relative in PAIR_FILES:
        doc = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        for row in list(doc.get("entries") or []) + list(doc.get("lines") or []):
            if isinstance(row, dict):
                add_pair(pairs, row.get("jp"), row.get("ko"), relative, row)

    relative = "data/name75_base_ko.json"
    doc = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    for bucket in ("bases", "overrides", "qualifiers"):
        for jp, ko in (doc.get(bucket) or {}).items():
            add_pair(pairs, jp, ko, f"{relative}#{bucket}")

    relative = "data/main_translation_glossary_ko.json"
    doc = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    for row in doc["entries"]:
        add_pair(pairs, row.get("jp"), row.get("canonical_ko"), relative, row)

    relative = "data/gundam_terminology_standard_ko.json"
    for row in standard_entries():
        for jp, ko in (row.get("component_ko") or {}).items():
            add_pair(pairs, jp, ko, f"{relative}#component", row)
        if len(row.get("jp") or []) == 1:
            add_pair(pairs, row["jp"][0], row.get("canonical_ko"), relative, row)
    return pairs


def mapping_conflicts(pairs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in pairs:
        grouped[jp_key(row["jp"])].append(row)

    conflicts: list[dict[str, Any]] = []
    for key, rows in sorted(grouped.items()):
        values = {ko_key(row["ko"]) for row in rows}
        if len(values) <= 1:
            continue
        official_matches = {
            str((row.get("metadata") or {}).get("official_match") or "")
            for row in rows
        }
        if official_matches & {"full_name_for_short_jp", "official_alias_variant"}:
            classification = "intentional_short_name_vs_full_name"
        elif key == jp_key("ウイングゼロカスタム"):
            classification = "approved_contextual_surface_name"
        else:
            classification = "actionable_inconsistent_mapping"
        unique = []
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            item = (row["jp"], row["ko"], row["source"])
            if item in seen:
                continue
            seen.add(item)
            unique.append({"jp": item[0], "ko": item[1], "source": item[2]})
        conflicts.append({"jp_key": key, "classification": classification, "mappings": unique})
    return conflicts


def bank5c_hits(
    original: bytes, tip: bytes, tbl: Tbl, original_dictionary: Dictionary, dictionary, bad_index
) -> list[dict[str, Any]]:
    sb = stock_base(tip)
    hits: list[dict[str, Any]] = []
    for logical, raw, kind in _walk_zstring_range(
        original, 0x5C0000, 0x5C7900, region="bank5c_encyclopedia", max_len=256
    ):
        got = read_encoded_z_safe(tip, sb + logical, max_len=256)
        if not got:
            continue
        try:
            current = dictionary.expand(got[0], tbl).rstrip("\u3000 \t")
        except Exception:
            continue
        matched = find_bad(current, bad_index)
        if matched:
            hits.append(
                {
                    "abs": f"{logical:06X}",
                    "kind": kind,
                    "jp": original_dictionary.expand(raw, tbl),
                    "current": current,
                    "matches": matched,
                }
            )
    return hits


def untranslated_standard_dictionary(
    original_dictionary: Dictionary, dictionary, tbl: Tbl
) -> list[dict[str, Any]]:
    wanted: dict[str, tuple[str, str]] = {}
    for row in standard_entries():
        for jp in row.get("jp") or []:
            wanted.setdefault(jp_key(jp), (row["id"], row["canonical_ko"]))
    rows = []
    for index in range(original_dictionary.count):
        jp = original_dictionary.expand_index(index, tbl)
        target = wanted.get(jp_key(jp))
        if target is None:
            continue
        current = dictionary.expand_index(index, tbl).rstrip("\u3000 \t")
        if any(is_japanese_character(char) for char in current):
            rows.append(
                {
                    "index": f"{index:04X}",
                    "term_id": target[0],
                    "jp": jp,
                    "canonical_ko": target[1],
                    "current": current,
                    "player_visible_in_scanned_records": False,
                }
            )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tip", type=Path, default=MAIN)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()

    tip = bytes(load_rom(args.tip))
    original = bytes(load_rom(ORIGINAL))
    tbl = Tbl.load(TBL_PATH)
    original_dictionary = Dictionary(original)
    dictionary = make_dictionary_ext3(
        tip, load_ext_meta(EXT_META), load_ext_meta(EXT3_META)
    )
    bad_index = forbidden_index(standard_entries())
    pairs = catalog_pairs()
    conflicts = mapping_conflicts(pairs)
    sources = source_hits(bad_index)
    dict_hits = dictionary_hits(tip, tbl, dictionary, bad_index)
    five_bank_hits = five_bank_dictionary_hits(tip, tbl, dictionary, bad_index)
    inventory_hits = rendered_record_hits(tip, tbl, dictionary, bad_index)
    complete_bank5c_hits = bank5c_hits(
        original, tip, tbl, original_dictionary, dictionary, bad_index
    )
    untranslated = untranslated_standard_dictionary(original_dictionary, dictionary, tbl)
    visible_abs = {row["abs"] for row in complete_bank5c_hits}
    for row in untranslated:
        row["player_visible_in_scanned_records"] = any(
            row["jp"] in record.get("current", "") for record in complete_bank5c_hits
        )

    report = {
        "schema_version": 1,
        "generated_by": "tools/audit_main_tip_name_mapping_consistency.py",
        "read_only_rom_audit": True,
        "tip": {"path": str(args.tip.resolve()), "size": len(tip), "sha256": sha(tip)},
        "catalog_scope": list(PAIR_FILES)
        + [
            "data/name75_base_ko.json",
            "data/main_translation_glossary_ko.json",
            "data/gundam_terminology_standard_ko.json",
        ],
        "counts": {
            "catalog_pairs": len(pairs),
            "catalog_name_keys": len({jp_key(row["jp"]) for row in pairs}),
            "catalog_conflicts_total": len(conflicts),
            "catalog_conflicts_actionable": sum(
                row["classification"] == "actionable_inconsistent_mapping" for row in conflicts
            ),
            "catalog_conflicts_intentional": sum(
                row["classification"] != "actionable_inconsistent_mapping" for row in conflicts
            ),
            "active_source_forbidden_hits": len(sources),
            "current_tip_dictionary_forbidden_hits": len(dict_hits),
            "current_tip_five_bank_dictionary_forbidden_hits": len(five_bank_hits),
            "current_tip_inventory_forbidden_hits": len(inventory_hits),
            "current_tip_complete_bank5c_forbidden_hits": len(complete_bank5c_hits),
            "current_tip_untranslated_standard_dictionary_entries": len(untranslated),
        },
        "resolved_source_findings": [
            {
                "jp": "フラナガン / フラナガン機関",
                "before": ["프라나간", "플래나간", "플래너간", "플래너건"],
                "after": "플라나간",
                "decision": "user_confirmed",
            },
            {
                "jp": "ブラ－ド・ファ－レン / ブラ－ド",
                "before": ["브래드 파렌", "브래드"],
                "after": "브라드 파렌 / 브라드",
                "decision": "user_confirmed",
            },
            {
                "jp": "チャップ・アデル",
                "before": ["채프・아델"],
                "after": "챕 아델",
                "decision": "main_glossary_official_current_reference",
            },
        ],
        "catalog_conflicts": conflicts,
        "active_source_forbidden_hits": sources,
        "current_tip_dictionary_forbidden_hits": dict_hits,
        "current_tip_five_bank_dictionary_forbidden_hits": five_bank_hits,
        "current_tip_inventory_forbidden_hits": inventory_hits,
        "current_tip_complete_bank5c_forbidden_hits": complete_bank5c_hits,
        "current_tip_untranslated_standard_dictionary_entries": untranslated,
        "status": (
            "sources_synced_but_current_tip_still_needs_candidate"
            if not sources and (dict_hits or five_bank_hits or complete_bank5c_hits)
            else "clean"
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], **report["counts"]}, ensure_ascii=False, indent=2))
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
